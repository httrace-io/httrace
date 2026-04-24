'use strict';

const https = require('https');
const http = require('http');

const DEFAULT_ENDPOINT = 'https://api.httrace.com/v1/captures';

const SENSITIVE_HEADERS = new Set([
  'authorization', 'cookie', 'set-cookie',
  'x-api-key', 'x-auth-token', 'proxy-authorization',
]);

const BINARY_TYPES = [
  'application/octet-stream', 'image/', 'audio/', 'video/',
  'application/gzip', 'application/zip', 'application/pdf', 'multipart/',
];

const SENSITIVE_KEYS = ['password', 'secret', 'token', 'ssn', 'credit_card', 'card_number', 'cvv'];

// ── Client ─────────────────────────────────────────────────────────────────

class HttraceClient {
  constructor(apiKey, endpoint = DEFAULT_ENDPOINT, batchSize = 50, maxQueue = 10_000) {
    this._apiKey = apiKey;
    this._endpoint = new URL(endpoint);
    this._batchSize = batchSize;
    this._maxQueue = maxQueue;
    this._queue = [];

    // Flush every 2s in background — unref so it doesn't block process exit
    this._timer = setInterval(() => this._flush(), 2000).unref();

    const onExit = () => this._flush();
    process.on('exit', onExit);
    process.on('SIGINT', () => { this._flush(); process.exit(0); });
    process.on('SIGTERM', () => { this._flush(); process.exit(0); });
  }

  enqueue(interaction) {
    if (this._queue.length >= this._maxQueue) return;
    this._queue.push(interaction);
    if (this._queue.length >= this._batchSize) this._flush();
  }

  _flush() {
    if (this._queue.length === 0) return;
    const batch = this._queue.splice(0);
    const body = JSON.stringify({ captures: batch });

    const options = {
      hostname: this._endpoint.hostname,
      port: this._endpoint.port || (this._endpoint.protocol === 'https:' ? 443 : 80),
      path: this._endpoint.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
        'X-Api-Key': this._apiKey,
      },
    };

    const proto = this._endpoint.protocol === 'https:' ? https : http;
    const req = proto.request(options, (res) => res.resume());
    req.on('error', () => {}); // fire-and-forget
    req.write(body);
    req.end();
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

function filterHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers || {})) {
    if (!SENSITIVE_HEADERS.has(k.toLowerCase())) out[k] = v;
  }
  return out;
}

function parseBody(raw, contentType = '') {
  if (!raw) return null;
  const buf = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
  if (buf.length === 0) return null;
  if (BINARY_TYPES.some((t) => contentType.includes(t))) return null;
  if (contentType.includes('application/json')) {
    try { return JSON.parse(buf.toString('utf8')); } catch {}
  }
  return buf.toString('utf8');
}

function sanitize(val) {
  if (typeof val === 'string') {
    return val
      .replace(/\b[\w.+\-]+@[\w.\-]+\.\w{2,}\b/g, '[EMAIL]')
      .replace(/\b(?:\d[ -]?){13,16}\b/g, '[CARD]');
  }
  if (Array.isArray(val)) return val.map(sanitize);
  if (val && typeof val === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(val)) {
      out[k] = SENSITIVE_KEYS.some((s) => k.toLowerCase().includes(s))
        ? '[REDACTED]'
        : sanitize(v);
    }
    return out;
  }
  return val;
}

// ── Middleware factory ──────────────────────────────────────────────────────

/**
 * Httrace Express middleware.
 *
 * @param {object} options
 * @param {string} options.apiKey     — your Httrace API key (required)
 * @param {string} [options.service]  — service name label (default: "default")
 * @param {number} [options.sampleRate] — fraction of requests to capture 0–1 (default: 0.1)
 * @param {string[]} [options.excludePaths] — paths to skip
 * @param {string} [options.endpoint] — override API endpoint (for self-hosted)
 *
 * Usage:
 *   const httrace = require('httrace');
 *   app.use(httrace({ apiKey: 'ht_...' }));
 */
function httrace(options = {}) {
  const {
    apiKey,
    service = 'default',
    sampleRate = 0.1,
    excludePaths = ['/health', '/metrics', '/favicon.ico'],
    endpoint,
  } = options;

  if (!apiKey) throw new Error('[httrace] apiKey is required');

  const _client = new HttraceClient(apiKey, endpoint || DEFAULT_ENDPOINT);
  const _exclude = new Set(excludePaths);

  return function httraceMiddleware(req, res, next) {
    const path = req.path || (req.url || '').split('?')[0];
    if (_exclude.has(path) || Math.random() >= sampleRate) return next();

    const tStart = Date.now();
    const respChunks = [];

    const origWrite = res.write.bind(res);
    const origEnd = res.end.bind(res);

    res.write = function (chunk, ...args) {
      if (chunk) respChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
      return origWrite(chunk, ...args);
    };

    res.end = function (chunk, ...args) {
      if (chunk) respChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));

      setImmediate(() => {
        try {
          const latency = Date.now() - tStart;
          const reqCT = req.headers['content-type'] || '';
          const respCT = res.getHeader('content-type') || '';
          const respBuf = respChunks.length ? Buffer.concat(respChunks) : null;

          // req.body is populated by express.json() / body-parser
          let reqBody = null;
          if (req.body !== undefined) {
            reqBody = sanitize(req.body);
          } else {
            reqBody = sanitize(parseBody(null, reqCT));
          }

          const interaction = {
            service,
            session_id: req.headers['x-session-id'] || req.headers['x-request-id'] || null,
            request: {
              method: req.method,
              path,
              query_params: req.query || {},
              headers: filterHeaders(req.headers),
              body: reqBody,
              timestamp: tStart / 1000,
            },
            response: {
              status_code: res.statusCode,
              headers: {},
              body: sanitize(parseBody(respBuf, String(respCT))),
              latency_ms: latency,
            },
          };

          _client.enqueue(interaction);
        } catch (_) {
          // never crash the app
        }
      });

      return origEnd(chunk, ...args);
    };

    next();
  };
}

module.exports = httrace;
module.exports.default = httrace;
