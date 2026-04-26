// Package httrace provides an HTTP middleware for capturing real production
// traffic and sending it to the Httrace API for automatic test generation.
//
// Works with the standard net/http library — and therefore with chi, gorilla/mux,
// gin (via gin.WrapH), echo, and any other Go HTTP framework.
//
// Usage (net/http / chi):
//
//	import "github.com/httrace-io/httrace-go"
//
//	cfg := httrace.Config{APIKey: "ht_...", Service: "orders-api"}
//	http.Handle("/", httrace.Middleware(cfg)(yourHandler))
//
// Usage (gin):
//
//	r.Use(gin.WrapH(httrace.Middleware(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))))
package httrace

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"
)

const defaultEndpoint = "https://api.httrace.com/v1/captures"

var sensitiveHeaders = map[string]bool{
	"authorization":        true,
	"cookie":              true,
	"set-cookie":          true,
	"x-api-key":           true,
	"x-auth-token":        true,
	"proxy-authorization": true,
}

var binaryContentTypes = []string{
	"application/octet-stream", "image/", "audio/", "video/",
	"application/gzip", "application/zip", "application/pdf", "multipart/",
}

var sensitiveKeys = []string{"password", "secret", "token", "ssn", "credit_card", "card_number", "cvv"}

// outgoingCallKey is the context key for per-request outgoing call capture.
type outgoingCallKey struct{}

// OutgoingCall represents a single outgoing dependency call captured during a request.
type OutgoingCall struct {
	Type           string      `json:"type"`
	Method         string      `json:"method,omitempty"`
	URL            string      `json:"url,omitempty"`
	RequestBody    interface{} `json:"request_body,omitempty"`
	ResponseStatus int         `json:"response_status,omitempty"`
	ResponseBody   interface{} `json:"response_body,omitempty"`
	LatencyMs      float64     `json:"latency_ms"`
}

var sensitiveURLParamRe = regexp.MustCompile(`(?i)api[-_]?key|apikey|token|secret|auth|password|passwd|credential|access[-_]?token`)

func sanitizeOutgoingURL(rawURL string) string {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}
	q := parsed.Query()
	for k := range q {
		if sensitiveURLParamRe.MatchString(k) {
			q.Set(k, "<REDACTED>")
		}
	}
	parsed.RawQuery = q.Encode()
	return parsed.String()
}

// RecordingTransport is an http.RoundTripper that records outgoing HTTP calls
// into the per-request context when CaptureOutgoing is enabled.
type RecordingTransport struct {
	Base http.RoundTripper
}

func (t *RecordingTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	calls, _ := req.Context().Value(outgoingCallKey{}).(*[]OutgoingCall)

	base := t.Base
	if base == nil {
		base = http.DefaultTransport
	}

	if calls == nil {
		return base.RoundTrip(req)
	}

	t0 := time.Now()
	resp, err := base.RoundTrip(req)
	latency := time.Since(t0).Seconds() * 1000

	if err == nil {
		var body interface{}
		ct := resp.Header.Get("Content-Type")
		if strings.Contains(ct, "application/json") {
			raw, readErr := io.ReadAll(resp.Body)
			resp.Body.Close()
			if readErr == nil {
				_ = json.Unmarshal(raw, &body)
				resp.Body = io.NopCloser(bytes.NewReader(raw))
			}
		}
		*calls = append(*calls, OutgoingCall{
			Type:           "http",
			Method:         req.Method,
			URL:            sanitizeOutgoingURL(req.URL.String()),
			ResponseStatus: resp.StatusCode,
			ResponseBody:   body,
			LatencyMs:      latency,
		})
	}

	return resp, err
}

// ClientFromContext returns an *http.Client whose transport records outgoing calls
// into the per-request context. Use this client for any outgoing HTTP calls
// you want to capture when CaptureOutgoing is enabled in the middleware.
//
//	client := httrace.ClientFromContext(r.Context())
//	resp, err := client.Get("https://api.stripe.com/...")
func ClientFromContext(ctx context.Context) *http.Client {
	return &http.Client{
		Transport: &RecordingTransport{Base: http.DefaultTransport},
	}
}

// contextWithOutgoingCapture returns a context that records outgoing calls.
func contextWithOutgoingCapture(ctx context.Context) (context.Context, *[]OutgoingCall) {
	calls := make([]OutgoingCall, 0)
	return context.WithValue(ctx, outgoingCallKey{}, &calls), &calls
}

// Config holds configuration for the Httrace middleware.
type Config struct {
	// APIKey is your Httrace API key — required.
	APIKey string

	// Service labels this service in the Httrace dashboard (default: "default").
	Service string

	// SampleRate is the fraction of requests to capture (0.0–1.0, default: 0.1).
	SampleRate float64

	// ExcludePaths is a list of URL paths to skip (default: /health, /metrics, /favicon.ico).
	ExcludePaths []string

	// Endpoint overrides the default Httrace API endpoint (useful for self-hosted).
	Endpoint string

	// CaptureOutgoing enables recording of outgoing HTTP calls made during a request.
	// Use ClientFromContext(r.Context()) to obtain an instrumented *http.Client.
	CaptureOutgoing bool
}

// Middleware returns an http.Handler middleware that captures traffic.
func Middleware(cfg Config) func(http.Handler) http.Handler {
	if cfg.Service == "" {
		cfg.Service = "default"
	}
	if cfg.SampleRate == 0 {
		cfg.SampleRate = 0.1
	}
	if cfg.Endpoint == "" {
		cfg.Endpoint = defaultEndpoint
	}

	exclude := map[string]bool{
		"/health": true, "/metrics": true, "/favicon.ico": true,
	}
	for _, p := range cfg.ExcludePaths {
		exclude[p] = true
	}

	c := newClient(cfg.APIKey, cfg.Endpoint)

	captureOutgoing := cfg.CaptureOutgoing

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if exclude[r.URL.Path] || rand.Float64() >= cfg.SampleRate {
				next.ServeHTTP(w, r)
				return
			}

			// Buffer request body so both us and the handler can read it
			reqBody, _ := io.ReadAll(r.Body)
			r.Body = io.NopCloser(bytes.NewReader(reqBody))

			// Optionally activate outgoing call capture for this request
			var outgoingCalls *[]OutgoingCall
			if captureOutgoing {
				ctx, calls := contextWithOutgoingCapture(r.Context())
				r = r.WithContext(ctx)
				outgoingCalls = calls
			}

			rw := &responseRecorder{ResponseWriter: w, statusCode: 200}
			tStart := time.Now()
			next.ServeHTTP(rw, r)
			latency := time.Since(tStart).Seconds() * 1000

			go record(c, cfg.Service, r, reqBody, rw, latency, outgoingCalls)
		})
	}
}

func record(c *client, service string, r *http.Request, reqBody []byte, rw *responseRecorder, latency float64, outgoingCalls *[]OutgoingCall) {
	defer func() { recover() }()

	query := make(map[string]string)
	for k, v := range r.URL.Query() {
		if len(v) > 0 {
			query[k] = v[0]
		}
	}

	var outgoing []OutgoingCall
	if outgoingCalls != nil {
		outgoing = *outgoingCalls
	}

	interaction := map[string]interface{}{
		"service":    service,
		"session_id": firstNonEmpty(r.Header.Get("X-Session-Id"), r.Header.Get("X-Request-Id")),
		"request": map[string]interface{}{
			"method":       r.Method,
			"path":         r.URL.Path,
			"query_params": query,
			"headers":      filterHeaders(r.Header),
			"body":         sanitize(parseBody(reqBody, r.Header.Get("Content-Type"))),
			"timestamp":    float64(time.Now().UnixNano()) / 1e9,
		},
		"response": map[string]interface{}{
			"status_code": rw.statusCode,
			"headers":     map[string]string{},
			"body":        sanitize(parseBody(rw.body.Bytes(), rw.Header().Get("Content-Type"))),
			"latency_ms":  latency,
		},
		"outgoing_calls": outgoing,
	}

	c.enqueue(interaction)
}

// ── responseRecorder captures status code + body ───────────────────────────

type responseRecorder struct {
	http.ResponseWriter
	statusCode int
	body       bytes.Buffer
}

func (rr *responseRecorder) WriteHeader(code int) {
	rr.statusCode = code
	rr.ResponseWriter.WriteHeader(code)
}

func (rr *responseRecorder) Write(b []byte) (int, error) {
	rr.body.Write(b)
	return rr.ResponseWriter.Write(b)
}

// ── Helpers ────────────────────────────────────────────────────────────────

func filterHeaders(h http.Header) map[string]string {
	out := make(map[string]string)
	for k, v := range h {
		if !sensitiveHeaders[strings.ToLower(k)] && len(v) > 0 {
			out[k] = v[0]
		}
	}
	return out
}

func parseBody(raw []byte, ct string) interface{} {
	if len(raw) == 0 {
		return nil
	}
	for _, t := range binaryContentTypes {
		if strings.Contains(ct, t) {
			return nil
		}
	}
	if strings.Contains(ct, "application/json") {
		var v interface{}
		if json.Unmarshal(raw, &v) == nil {
			return v
		}
	}
	return string(raw)
}

func sanitize(val interface{}) interface{} {
	switch v := val.(type) {
	case string:
		return v // simple strings pass through (PII patterns handled server-side)
	case map[string]interface{}:
		out := make(map[string]interface{}, len(v))
		for k, vv := range v {
			lk := strings.ToLower(k)
			redact := false
			for _, s := range sensitiveKeys {
				if strings.Contains(lk, s) {
					redact = true
					break
				}
			}
			if redact {
				out[k] = "[REDACTED]"
			} else {
				out[k] = sanitize(vv)
			}
		}
		return out
	case []interface{}:
		out := make([]interface{}, len(v))
		for i, vv := range v {
			out[i] = sanitize(vv)
		}
		return out
	}
	return val
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// ── Background client ──────────────────────────────────────────────────────

type client struct {
	apiKey   string
	endpoint string
	mu       sync.Mutex
	queue    []interface{}
}

func newClient(apiKey, endpoint string) *client {
	c := &client{apiKey: apiKey, endpoint: endpoint}
	go c.worker()
	return c
}

func (c *client) enqueue(item interface{}) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.queue) < 10_000 {
		c.queue = append(c.queue, item)
	}
}

func (c *client) worker() {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		c.flush()
	}
}

func (c *client) flush() {
	c.mu.Lock()
	if len(c.queue) == 0 {
		c.mu.Unlock()
		return
	}
	batch := c.queue
	c.queue = nil
	c.mu.Unlock()

	body, err := json.Marshal(map[string]interface{}{"captures": batch})
	if err != nil {
		return
	}

	req, err := http.NewRequest(http.MethodPost, c.endpoint, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Api-Key", c.apiKey)

	httpClient := &http.Client{Timeout: 5 * time.Second}
	resp, err := httpClient.Do(req)
	if err == nil {
		resp.Body.Close()
	}
}
