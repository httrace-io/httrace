/**
 * Httrace API client — thin wrapper around Node.js https module.
 * No external dependencies, only Node built-ins.
 */
import * as https from 'https';
import * as http from 'http';
import { URL } from 'url';

export interface CoverageEndpoint {
  method: string;
  path: string;
  captures: number;
  statuses: number[];
  has_tests: boolean;
}

export interface CoverageResponse {
  endpoints: CoverageEndpoint[];
  total_captures: number;
}

export interface ChangeItem {
  type: string;   // "breaking" | "schema" | "new_field"
  detail: string;
}

export interface EndpointChange {
  endpoint: string;
  changes: ChangeItem[];
}

export interface DriftResponse {
  changes: EndpointChange[];
  untested_endpoints: string[];
}

export interface GeneratedFile {
  file: string;
  test_count: number;
  quality_score: number | null;
}

export interface GenerateResponse {
  generated: number;
  lang: string;
  files: GeneratedFile[];
  code: Record<string, string>;
}

export interface ReplayDiff {
  method: string;
  path: string;
  original_status: number;
  replay_status: number | null;
  status_match: boolean;
  body_diff: string;
  error?: string;
}

export interface ReplayResponse {
  total: number;
  passed: number;
  failed: number;
  duration_ms: number;
  differences: ReplayDiff[];
  message?: string;
}

export class HttptraceClient {
  constructor(private apiKey: string, private baseUrl: string) {}

  private async request<T>(
    method: string,
    path: string,
    params?: Record<string, string>,
    body?: unknown,
    timeoutMs = 30_000,
  ): Promise<T> {
    const url = new URL(path, this.baseUrl.replace(/\/$/, '') + '/');
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        url.searchParams.set(k, v);
      }
    }

    const bodyStr = body ? JSON.stringify(body) : undefined;
    const options: https.RequestOptions = {
      method,
      hostname: url.hostname,
      port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname + url.search,
      headers: {
        'X-Api-Key': this.apiKey,
        'Content-Type': 'application/json',
        'User-Agent': 'httrace-vscode/0.2.0',
        ...(bodyStr ? { 'Content-Length': Buffer.byteLength(bodyStr).toString() } : {}),
      },
      timeout: timeoutMs,
    };

    return new Promise((resolve, reject) => {
      const lib = url.protocol === 'https:' ? https : http;
      const req = lib.request(options, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('end', () => {
          const raw = Buffer.concat(chunks).toString('utf-8');
          if (res.statusCode && res.statusCode >= 400) {
            reject(new Error(`API error ${res.statusCode}: ${raw.slice(0, 200)}`));
            return;
          }
          try {
            resolve(JSON.parse(raw) as T);
          } catch {
            reject(new Error(`Invalid JSON response: ${raw.slice(0, 200)}`));
          }
        });
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
      if (bodyStr) req.write(bodyStr);
      req.end();
    });
  }

  async getCoverage(service: string): Promise<CoverageResponse> {
    return this.request<CoverageResponse>('GET', '/v1/coverage', { service });
  }

  async getChanges(service: string): Promise<DriftResponse> {
    return this.request<DriftResponse>('GET', '/v1/changes', { service });
  }

  async generateTests(service: string, format: string): Promise<GenerateResponse> {
    return this.request<GenerateResponse>(
      'POST', '/v1/generate-tests',
      { service, format },
      undefined,
      60_000,
    );
  }

  async replayTraffic(service: string, targetBaseUrl: string, limit = 50): Promise<ReplayResponse> {
    return this.request<ReplayResponse>(
      'POST', '/v1/replay',
      { service, target_base_url: targetBaseUrl, limit: String(limit) },
      undefined,
      120_000,
    );
  }
}
