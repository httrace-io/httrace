package httrace

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMiddlewarePassesThrough(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"ok":true}`))
	})

	cfg := Config{APIKey: "ht_test", SampleRate: 1.0, Service: "test-svc"}
	mw := Middleware(cfg)(handler)

	req := httptest.NewRequest(http.MethodGet, "/api/orders", nil)
	req.Header.Set("Content-Type", "application/json")

	rr := httptest.NewRecorder()
	mw.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	body := rr.Body.String()
	if body != `{"ok":true}` {
		t.Fatalf("unexpected body: %s", body)
	}
}

func TestMiddlewareExcludesHealthPath(t *testing.T) {
	called := false
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(200)
	})

	mw := Middleware(Config{APIKey: "ht_test", SampleRate: 1.0})(handler)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	mw.ServeHTTP(rr, req)

	if !called {
		t.Fatal("handler was not called for excluded path")
	}
}

func TestMiddlewareReadsRequestBody(t *testing.T) {
	var captured []byte
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		captured, _ = io.ReadAll(r.Body)
		w.WriteHeader(201)
	})

	mw := Middleware(Config{APIKey: "ht_test", SampleRate: 1.0})(handler)

	payload := `{"cart_id":"abc123"}`
	req := httptest.NewRequest(http.MethodPost, "/checkout", bytes.NewBufferString(payload))
	req.Header.Set("Content-Type", "application/json")

	rr := httptest.NewRecorder()
	mw.ServeHTTP(rr, req)

	if string(captured) != payload {
		t.Fatalf("handler didn't get the request body: got %q", string(captured))
	}
	if rr.Code != 201 {
		t.Fatalf("expected 201, got %d", rr.Code)
	}
}

func TestFilterHeaders(t *testing.T) {
	h := http.Header{
		"Authorization": []string{"Bearer secret"},
		"Cookie":        []string{"session=abc"},
		"X-Custom":      []string{"kept"},
		"Content-Type":  []string{"application/json"},
	}
	filtered := filterHeaders(h)

	if _, ok := filtered["Authorization"]; ok {
		t.Error("Authorization header should be filtered")
	}
	if _, ok := filtered["Cookie"]; ok {
		t.Error("Cookie header should be filtered")
	}
	if filtered["X-Custom"] != "kept" {
		t.Error("X-Custom should be kept")
	}
}

func TestParseBodyJSON(t *testing.T) {
	raw := []byte(`{"user_id":42}`)
	result := parseBody(raw, "application/json")
	m, ok := result.(map[string]interface{})
	if !ok {
		t.Fatalf("expected map, got %T", result)
	}
	if m["user_id"].(float64) != 42 {
		t.Error("wrong user_id")
	}
}

func TestParseBodyBinary(t *testing.T) {
	raw := []byte{0x89, 0x50, 0x4e, 0x47}
	result := parseBody(raw, "image/png")
	if result != nil {
		t.Errorf("binary body should return nil, got %v", result)
	}
}

func TestSanitizeRedactsPassword(t *testing.T) {
	input := map[string]interface{}{
		"username": "alice",
		"password": "hunter2",
	}
	out := sanitize(input).(map[string]interface{})
	if out["password"] != "[REDACTED]" {
		t.Errorf("password should be redacted, got %v", out["password"])
	}
	if out["username"] != "alice" {
		t.Errorf("username should not be redacted, got %v", out["username"])
	}
}

func TestResponseRecorder(t *testing.T) {
	rr := httptest.NewRecorder()
	rec := &responseRecorder{ResponseWriter: rr, statusCode: 200}

	rec.WriteHeader(404)
	rec.Write([]byte("not found"))

	if rec.statusCode != 404 {
		t.Errorf("expected 404, got %d", rec.statusCode)
	}
	if rec.body.String() != "not found" {
		t.Errorf("expected body 'not found', got %q", rec.body.String())
	}
}

func TestClientEnqueueAndFlush(t *testing.T) {
	// Set up a test server to receive captures
	var received map[string]interface{}
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(200)
	}))
	defer ts.Close()

	c := newClient("ht_test", ts.URL)
	c.enqueue(map[string]interface{}{"service": "test"})
	c.flush() // flush immediately

	if received == nil {
		t.Fatal("no data received by test server")
	}
	captures, ok := received["captures"].([]interface{})
	if !ok || len(captures) == 0 {
		t.Fatal("captures array is empty")
	}
}
