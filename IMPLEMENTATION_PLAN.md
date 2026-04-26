# Httrace — Dependency Mocking (Keploy Killer Feature)

## Context

Keploy's main differentiator vs. Httrace is that it captures outgoing dependency calls (DB queries, external HTTP, Redis) during a real request and replays them as mocks in generated tests — so tests run without real infrastructure. This plan implements the same capability for all 4 SDKs (Python, Node.js, Go, Ruby) and all 5 test generators (pytest, Jest, Vitest, Go testing, RSpec), followed by a code review and docs/landing update.

**Why this matters for customers:** Generated tests today require a live server + database. With dependency mocking, generated tests are fully self-contained — they run in CI, on a laptop, or in a Docker container without any external dependencies. This is the single most important quality-of-life improvement for paying customers.

### Architecture

```
Inbound Request → SDK Middleware → App logic → DB / External HTTP
                       ↓                ↓  (if capture_outgoing=True)
                  ContextVar      OutgoingInterceptor appends calls
                       ↓
        Upload: { request, response, outgoing_calls: [...] }
                       ↓
        Backend stores outgoing_calls in CaptureRecord
                       ↓
        Generator produces mock fixtures from outgoing_calls
```

Each `OutgoingCall` entry:
```json
{
  "type": "http",
  "method": "GET",
  "url": "https://api.stripe.com/v1/charges/ch_123",
  "request_body": null,
  "response_status": 200,
  "response_body": { "id": "ch_123", "amount": 2000 },
  "query": null,
  "params": null,
  "result_count": null,
  "result_sample": null,
  "latency_ms": 45.2
}
```

---

## Files to Create / Modify

### Python SDK  `/Users/marcuswinter/Claude/httrace/sdk/httrace/`

**`capture.py`** (modify):
- Add `OutgoingCall` dataclass: `type`, `method`, `url`, `request_body`, `response_status`, `response_body`, `query`, `params`, `result_count`, `result_sample`, `latency_ms`
- Add `outgoing_calls: list = field(default_factory=list)` to `CapturedInteraction`
- Update `CapturedInteraction.to_dict()` to include `outgoing_calls`

**New file `interceptors.py`**:
- `_CONTEXT: ContextVar[list | None] = ContextVar("httrace_outgoing", default=None)`
- `patch_httpx()` — wraps `httpx.AsyncClient.send` and `httpx.Client.send`; inside wrapper: if `_CONTEXT.get()` is not None, append an `OutgoingCall` after response returns
- `patch_requests()` — wraps `requests.Session.send` (sync); same pattern
- `unpatch_httpx()` / `unpatch_requests()` — restore originals
- `register_sqlalchemy_engine(engine)` — `sqlalchemy.event.listen(engine, "after_cursor_execute", _sql_handler)`; `_sql_handler` appends SQL `OutgoingCall` to `_CONTEXT`
- All captured URLs/bodies run through `sanitize_json_body()` before appending

**`middleware.py`** (modify):
- New params: `capture_outgoing: bool = False`, `db_engines: list | None = None`
- In `__init__`: if `capture_outgoing`, call `patch_httpx()` + `patch_requests()`; for each engine in `db_engines`: call `register_sqlalchemy_engine(engine)`
- In `__call__` (ASGI): before calling `_app`, set `_CONTEXT.set([])`; after response complete, read `_CONTEXT.get()` and attach to `CapturedInteraction.outgoing_calls`
- WSGI: use `threading.local()` with same pattern

### Node.js SDK  `/Users/marcuswinter/Claude/httrace/sdk-node/src/index.js`

- Add `captureOutgoing: false` option
- Add `const { AsyncLocalStorage } = require('node:async_hooks')` + `const _store = new AsyncLocalStorage()`
- In middleware: `_store.run([], () => { next() })`; after response: append `_store.getStore()` as `outgoing_calls`
- Monkey-patch `https.request` / `http.request`: wrap `options` to intercept; record method, url, status, body sample, latency into `_store.getStore()` if store exists
- URL sanitizer: strip query params matching `/api.?key|token|secret|auth/i`
- Guard entire patch behind `if (captureOutgoing)` — zero overhead otherwise

### Go SDK  `/Users/marcuswinter/Claude/httrace/sdk-go/httrace.go`

- Add `CaptureOutgoing bool` to `Config`
- Add `OutgoingCall` struct and `RecordingTransport`:
  ```go
  type RecordingTransport struct {
      Base    http.RoundTripper
      calls   *[]OutgoingCall
  }
  func (t *RecordingTransport) RoundTrip(r *http.Request) (*http.Response, error)
  ```
- In middleware handler: create `calls := make([]OutgoingCall, 0)`; attach `RecordingTransport{Base: http.DefaultTransport, calls: &calls}` to a client stored in request context via a package-local context key; after handler returns, attach `calls` to the capture interaction
- User injects the recording client via a provided helper: `httrace.ClientFromContext(r.Context())` returns an `*http.Client` with the recording transport; this is the idiomatic Go approach (no global state mutation)

### Ruby SDK  `/Users/marcuswinter/Claude/httrace/sdk-ruby/lib/httrace.rb`

- Add `capture_outgoing: false` kwarg
- If `capture_outgoing`, `prepend Httrace::NetHTTPInterceptor` into `Net::HTTP` in `initialize`
- `NetHTTPInterceptor` module: override `request(req, body=nil, &block)` to record call to `Thread.current[:httrace_outgoing]` if set
- In middleware `call(env)`: set `Thread.current[:httrace_outgoing] = []`; after app returns, read the list; reset `Thread.current[:httrace_outgoing] = nil`
- For ActiveRecord: `ActiveSupport::Notifications.subscribe("sql.active_record") { |*args| ... }` if `capture_db: true` kwarg is set (Rails only)

### Backend: Model  `/opt/httrace/backend/models.py`

```python
outgoing_calls: Optional[Any] = Field(default=None, sa_column=Column(JSON))
```
(one line addition to `CaptureRecord`)

### Backend: Ingest  `/opt/httrace/backend/routes/ingest.py`

- Add `outgoing_calls: Optional[list] = None` to `RawCapture`
- Before storing: sanitize each call's `response_body` and `request_body` with `sanitize_json_body()`
- Add `outgoing_calls=cap.outgoing_calls` to `CaptureRecord(...)` constructor

### pytest Generator  `/opt/httrace/backend/generator/pytest_writer.py`

New function `_generate_mock_fixtures(outgoing_calls: list) -> tuple[str, list[str]]`:
- Returns `(fixture_code, fixture_names)` 
- HTTP calls → `@pytest.fixture` using `respx_mock` (pytest-respx):
  ```python
  @pytest.fixture
  def mock_external_http(respx_mock):
      respx_mock.get("https://api.stripe.com/v1/charges/ch_123").mock(
          return_value=httpx.Response(200, json={"id": "ch_123", "amount": 2000})
      )
      yield respx_mock
  ```
- SQL calls → `@pytest.fixture` using `pytest-mock`:
  ```python
  @pytest.fixture
  def mock_db(mocker):
      mock_conn = mocker.MagicMock()
      mock_conn.execute.return_value.fetchall.return_value = [
          {"id": "ord_1", "status": "shipped"},
      ]
      # TODO: mocker.patch("app.database.get_db", return_value=mock_conn)
      yield mock_conn
  ```
- Update `generate_module()`: if `outgoing_calls` on any record, inject fixture names into test args
- Update `generate_conftest()`: add `# pip install pytest-respx pytest-mock` comment if mocks present

### Jest Generator  `/opt/httrace/backend/generator/jest_writer.py`

New function `_generate_nock_setup(outgoing_calls)`:
```javascript
const nock = require('nock');
beforeEach(() => {
  nock('https://api.stripe.com')
    .get('/v1/charges/ch_123')
    .reply(200, { id: 'ch_123', amount: 2000 });
});
afterEach(() => nock.cleanAll());
```

### Vitest Generator  `/opt/httrace/backend/generator/vitest_writer.py`

New function `_generate_msw_setup(outgoing_calls)`:
```typescript
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
const server = setupServer(
  http.get('https://api.stripe.com/v1/charges/ch_123', () =>
    HttpResponse.json({ id: 'ch_123', amount: 2000 })
  )
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

### Go Generator  `/opt/httrace/backend/generator/go_writer.py`

New function `_generate_httpmock_setup(outgoing_calls)`:
```go
import "github.com/jarcoal/httpmock"

httpmock.Activate()
defer httpmock.DeactivateAndReset()
httpmock.RegisterResponder("GET", "https://api.stripe.com/v1/charges/ch_123",
    httpmock.NewJsonResponderOrPanic(200, map[string]interface{}{"id": "ch_123"}))
```

### RSpec Generator  `/opt/httrace/backend/generator/rspec_writer.py`

New function `_generate_webmock_stubs(outgoing_calls)`:
```ruby
before do
  stub_request(:get, "https://api.stripe.com/v1/charges/ch_123")
    .to_return(status: 200, body: '{"id":"ch_123","amount":2000}',
               headers: { 'Content-Type' => 'application/json' })
end
```

---

## Implementation Order

1. Python SDK `capture.py` + `interceptors.py` + `middleware.py`
2. Backend model + ingest (add `outgoing_calls` column + schema field)
3. pytest generator mock generation
4. Node.js SDK + Jest/Vitest generators
5. Go SDK + Go generator
6. Ruby SDK + RSpec generator
7. Code review of all changes
8. Docs + Landing Page update

---

## DB Migration

SQLite does not support `ADD COLUMN IF NOT EXISTS`. Use this migration script at container startup:
```python
# In database.py or a migration helper
from sqlalchemy import text, inspect
def _migrate_add_outgoing_calls(engine):
    with engine.connect() as conn:
        cols = [c['name'] for c in inspect(engine).get_columns('capturerecord')]
        if 'outgoing_calls' not in cols:
            conn.execute(text("ALTER TABLE capturerecord ADD COLUMN outgoing_calls JSON"))
            conn.commit()
```
Call from `main.py` after `SQLModel.metadata.create_all(engine)`.

---

## Verification

```bash
# 1. Python SDK unit test — does interceptor capture httpx calls?
cd /Users/marcuswinter/Claude/httrace/sdk
python3 -c "
from httrace.interceptors import patch_httpx, _CONTEXT
import asyncio, httpx
patch_httpx()
_CONTEXT.set([])
async def t():
    async with httpx.AsyncClient() as c:
        await c.get('https://httpbin.org/get')
    print('calls captured:', len(_CONTEXT.get()))  # should be 1
asyncio.run(t())
"

# 2. End-to-end: ingest capture with outgoing_calls
curl -X POST https://api.httrace.com/v1/captures \
  -H 'x-api-key: ht_xH3uVseoCPDu88OJ89xnBW6_UVogZRE4' \
  -H 'Content-Type: application/json' \
  -d '{
    "captures": [{
      "service": "mock-test",
      "request": {"method": "POST", "path": "/orders", "headers": {}, "body": {"amount": 100}},
      "response": {"status_code": 201, "headers": {}, "body": {"order_id": "ord_1"}, "latency_ms": 50},
      "outgoing_calls": [
        {"type": "http", "method": "GET", "url": "https://api.stripe.com/v1/charges/ch_123",
         "response_status": 200, "response_body": {"id": "ch_123"}, "latency_ms": 40}
      ]
    }]
  }'

# 3. Generate pytest and check for mock fixture
curl -s -X POST "https://api.httrace.com/v1/generate-tests?service=mock-test&format=pytest" \
  -H 'x-api-key: ht_xH3uVseoCPDu88OJ89xnBW6_UVogZRE4' \
  | python3 -c "
import json,sys; d=json.load(sys.stdin)
for name,code in d['code'].items():
    if 'respx' in code or 'mock_external' in code:
        print(name, '→ has HTTP mocks ✓')
"

# 4. Run generated pytest file (syntax check)
cd /tmp && python3 -m pytest --collect-only generated_test.py
```

---

## Code Review Checklist (to run after implementation)

- [ ] No outgoing call capture happens when `capture_outgoing=False` (default)
- [ ] URLs containing API keys in query params are sanitized before storage
- [ ] Response bodies of outgoing calls pass through `sanitize_json_body()`
- [ ] Generated mock fixtures are valid Python/JS/Go/Ruby syntax (ast.parse / node --check)
- [ ] Tests that have no `outgoing_calls` are unchanged (backward compat)
- [ ] Python ContextVar correctly isolates concurrent requests (no cross-contamination)
- [ ] `httpx.AsyncClient` patching does not break existing httrace middleware HTTP uploads
- [ ] SQL query templates shown but parameter values redacted
- [ ] `latency_ms` included in all captured OutgoingCalls

---

## Docs + Landing Page Updates

**`/Users/marcuswinter/Claude/httrace/landing/docs/index.html`**:
- Add sidebar link "Dependency mocking" under Features
- New section `#dependency-mocking`: explain `capture_outgoing=True`, show examples for all 4 SDKs, list required dev dependencies per framework

**`/Users/marcuswinter/Claude/httrace/landing/index.html`**:
- Update comparison table: "Auth fixtures auto-detected" row → add second row "Dependency mocks auto-generated" (✓ Httrace, ✕ all others)
- Update hero code example to show a test with `mock_external_http` fixture
- Update pricing feature bullets (Pro tier) to mention "Dependency mocking"

---

# Httrace — Comprehensive Product Improvements (Previous Phases)

## Context

Httrace (httrace.com) ist ein SaaS-Tool das echten HTTP-Traffic captured und daraus automatisch pytest-Integrationstests generiert. Der Nutzer möchte das Tool auf allen Ebenen weiterentwickeln: bessere Testqualität, mehr Ausgabeformate, CI/CD-Integration, Anomaly-Alerts, Replay-Testing, Team-Features, eine VS-Code-Extension sowie vollständig aktualisierte Docs und Landing Page.

Scope: **Alles inkl. Langfristig** — alle Phasen werden implementiert.
Test-Formate: **alle** — pytest, Jest, Go testing, RSpec, Vitest.

---

## Übersicht der Phasen

| Phase | Inhalt | Priorität |
|---|---|---|
| **0** | **Signup-Bug fixen (KRITISCH — Account-Erstellung kaputt)** | **Sofort** |
| 1 | Bessere pytest-Assertions + Multi-Format-Output | Hoch |
| 2 | OpenAPI-Generierung + Dashboard Coverage/Changes | Hoch |
| 3 | GitHub Actions + `httrace diff` CLI | Mittel |
| 4 | Anomaly Alerts (Slack/Email) | Mittel |
| 5 | Replay Testing (`httrace replay`) | Mittel |
| 6 | Team Features / Multi-User | Niedrig (langfristig) |
| 7 | VS Code Extension | Niedrig (langfristig) |
| 8 | Docs + Landing Page Update | Begleitend (alle Phasen) |

---

## Phase 0 — Signup-Bug fixen (SOFORT — höchste Priorität)

### Problem
Account-Erstellung schlägt fehl mit "something went wrong — please try again", E-Mail-Feld wird rot markiert. Benutzer können sich nicht registrieren.

### Diagnose-Schritte

1. **Browser-Konsole prüfen** — welcher Netzwerk-Request schlägt fehl? Status-Code?
2. **Cloudflare Turnstile** — häufigste Ursache: Turnstile-Widget gibt leeren oder ungültigen Token zurück, Backend lehnt mit 400 ab
3. **Passwort-Validierung** — Frontend-seitige Validierung (Passwörter stimmen nicht überein, zu kurz) triggert fälschlicherweise für die E-Mail

### Wahrscheinlichste Ursachen + Fixes

**Ursache A: Turnstile-Token leer beim Submit**

**Datei:** `/Users/marcuswinter/Claude/httrace/landing/index.html`

Im `handleWaitlist()`-Handler:
```javascript
// VORHER (Problem): cf_turnstile_response wird leer gesendet
const token = document.querySelector('[name="cf-turnstile-response"]')?.value || "";

// NACHHER (Fix): Turnstile explizit abfragen + Fehler anzeigen wenn leer
const token = window.turnstile?.getResponse() || "";
if (!token && TURNSTILE_CONFIGURED) {
    showError("Please complete the CAPTCHA.");
    return;
}
```

**Ursache B: Fehler-Handler zeigt E-Mail als invalid bei Backend-400**

Im Error-Handler: Wenn Backend 400 zurückgibt (CAPTCHA failed), wird fälschlicherweise die E-Mail-Validierung getriggert. Fix: Error-Message klar differenzieren und nur E-Mail als invalid markieren wenn es ein Email-Validation-Fehler ist.

```javascript
// VORHER:
emailInput.classList.add("invalid");  // immer bei Fehler
// NACHHER:
if (err.detail?.includes("email") || err.detail?.includes("Email")) {
    emailInput.classList.add("invalid");
} else {
    showGenericError(err.detail || "Something went wrong");
}
```

**Ursache C: Backend-500 wegen fehlendem Feld**

Prüfen ob `WaitlistRequest` alle Felder erwartet. Sicherstellen dass `password` korrekt im Body gesendet wird (nicht als Query-Parameter).

### Verifikation Phase 0
```bash
# Direkt auf API testen (ohne Turnstile, da TURNSTILE_SECRET_KEY leer = skip)
curl -X POST "https://api.httrace.com/v1/waitlist" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass123","cf_turnstile_response":""}'
# → Muss 201 zurückgeben, nicht 400/500

# Smoke-Test: Signup-Seite im Browser öffnen, DevTools-Network-Tab beobachten
# E-Mail eingeben, absenden → Request-Body und Response-Status prüfen
```

---

## Phase 1 — Testqualität + Multi-Format-Output

### 1a. Bessere pytest-Assertions

**Datei:** `/opt/httrace/backend/generator/pytest_writer.py`

Aktuell: `_generate_assertions()` prüft nur key presence und einfache String-Gleichheit für kurze Strings.

**Änderungen:**
- Value-Matching für primitive Felder (str < 200 Zeichen, int, bool, None)
- Schema-Assertions für Listen (prüft Länge und Typen der Elemente)
- Nested-Object-Assertions (rekursiv für dicts bis Tiefe 2)
- Status-Code-Assertion (bereits vorhanden, beibehalten)
- Latency-Assertion: `assert response.elapsed.total_seconds() * 1000 < {latency_ms * 3}`

```python
# Neue Signatur:
def _generate_assertions(resp_body: dict | list | None, status_code: int, latency_ms: float) -> list[str]:
```

### 1b. Jest Writer

**Neue Datei:** `/opt/httrace/backend/generator/jest_writer.py`

- `generate_module(endpoint, records) -> str` — erzeugt `.test.js`-Datei
- `describe('GET /path', () => { test('returns 200', async () => { ... }) })`
- Verwendet `axios` oder `fetch` für HTTP-Requests
- Assertions via `expect(res.status).toBe(200)` und `expect(res.data.key).toBe(value)`
- Header-Datei: `jest.config.js` Template

### 1c. Go Testing Writer

**Neue Datei:** `/opt/httrace/backend/generator/go_writer.py`

- `generate_module(endpoint, records) -> str` — erzeugt `*_test.go`-Datei
- Verwendet `net/http` + `testing` packages
- `func TestGET_path(t *testing.T) { resp, err := http.Get(...) }`
- JSON-Assertions via `encoding/json`

### 1d. RSpec Writer

**Neue Datei:** `/opt/httrace/backend/generator/rspec_writer.py`

- `generate_module(endpoint, records) -> str` — erzeugt `*_spec.rb`
- Verwendet `rspec` + `faraday` oder `net/http`
- `describe 'GET /path' do; it 'returns 200' do; ... end; end`

### 1e. Vitest Writer

**Neue Datei:** `/opt/httrace/backend/generator/vitest_writer.py`

- Ähnlich Jest, aber mit Vitest-Import: `import { describe, it, expect } from 'vitest'`
- Erzeugt `.test.ts`-Dateien mit TypeScript-Typen

### 1f. Generate-Route: Format-Parameter

**Datei:** `/opt/httrace/backend/routes/generate.py`

```python
@router.post("/v1/generate-tests")
async def generate_tests(
    service: str,
    format: str = "pytest",  # neu: pytest|jest|go|rspec|vitest
    ...
)
```

- Router wählt den passenden Writer per `format`-Parameter
- Dateiendung wird angepasst: `.py` / `.test.js` / `_test.go` / `_spec.rb` / `.test.ts`
- `GeneratedTest.test_name` enthält Dateiendung

### 1g. CLI: `--format` Flag

**Datei:** `/Users/marcuswinter/Claude/httrace/cli/main.py`

```python
@app.command()
def generate(
    service: str = typer.Option(...),
    format: str = typer.Option("pytest", help="pytest|jest|go|rspec|vitest"),
):
```

### Verifikation Phase 1
```bash
# Pytest (bestehend)
curl -X POST "https://api.httrace.com/v1/generate-tests?service=test&format=pytest" -H "x-api-key: ht_..."
# Jest
curl -X POST "https://api.httrace.com/v1/generate-tests?service=test&format=jest" -H "x-api-key: ht_..."
# Go
curl -X POST "https://api.httrace.com/v1/generate-tests?service=test&format=go" -H "x-api-key: ht_..."
# Alle Dateien ausgegeben und korrekt formatiert
```

---

## Phase 2 — OpenAPI + Dashboard Coverage/Changes

### 2a. OpenAPI-Endpunkt

**Neue Datei:** `/opt/httrace/backend/routes/openapi_gen.py`

```python
GET /v1/openapi.yaml?service=X
GET /v1/openapi.json?service=X
```

- Liest `CaptureRecord`-Einträge für den Service
- Generiert OpenAPI 3.0 YAML/JSON aus beobachtetem Traffic:
  - Pfade + Methoden aus `CaptureRecord.method` + `CaptureRecord.path`
  - Request-Body-Schema aus `req_body` (JSON Schema inference)
  - Response-Schema aus `resp_body`
  - Status-Codes aus `CaptureRecord.status_code`
- Gibt YAML als `text/yaml` oder JSON als `application/json` zurück

**Hilfsmodul:** `/opt/httrace/backend/generator/schema_inference.py`
- `infer_json_schema(obj) -> dict` — konvertiert Python-Dict zu JSON Schema
- Typen: `string`, `number`, `integer`, `boolean`, `null`, `array`, `object`
- Nullable detection, required fields aus häufigsten Keys

### 2b. Dashboard: Coverage & Changes Tab

**Datei:** `/Users/marcuswinter/Claude/httrace/landing/dashboard.html`

Neuer Tab neben "Recent Captures" und "Generated Tests":

**Coverage-Tab:**
- Tabelle mit Spalten: Endpoint | Captures | Status Codes | Last seen | Tests generated
- Daten von `GET /v1/coverage?service=X`
- Grüner Punkt wenn Tests generiert, grauer Punkt wenn nicht

**Changes-Tab:**
- Zeigt Schema-Änderungen seit letzter Generierung
- Daten von `GET /v1/changes?service=X`
- Rote Badge für Breaking Changes, gelbe für Neue Endpoints
- Button: "Regenerate tests" → triggert `POST /v1/generate-tests`

**Service-Selector:**
- Dropdown oben im Dashboard für aktiven Service
- Alle Coverage/Changes-Calls nutzen den gewählten Service

### Verifikation Phase 2
```bash
curl "https://api.httrace.com/v1/openapi.yaml?service=myapp" -H "x-api-key: ht_..."
# → valides OpenAPI 3.0 YAML
# Dashboard: Coverage-Tab zeigt Endpunkte, Changes-Tab zeigt Änderungen
```

---

## Phase 3 — GitHub Actions + `httrace diff`

### 3a. GitHub Actions Template

**Neue Datei:** `/opt/httrace/backend/routes/github.py`

```python
GET /v1/github-actions?service=X
```
- Gibt eine vorgefertigte `httrace.yml` GitHub Actions Workflow-Datei zurück
- Workflow: `httrace diff` ausführen → bei Breaking Changes Pipeline scheitern lassen

**Template-Inhalt:**
```yaml
name: Httrace API Drift Check
on: [push, pull_request]
jobs:
  httrace-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install httrace
      - run: httrace diff --service ${{ vars.HTTRACE_SERVICE }} --fail-on-breaking
        env:
          HTTRACE_API_KEY: ${{ secrets.HTTRACE_API_KEY }}
```

### 3b. CLI: `httrace diff`

**Datei:** `/Users/marcuswinter/Claude/httrace/cli/main.py`

```python
@app.command()
def diff(
    service: str = typer.Option(...),
    fail_on_breaking: bool = typer.Option(False),
    output: str = typer.Option("table"),  # table|json
):
    """Show schema drift since last test generation."""
```

- Ruft `GET /v1/changes?service=X` auf
- Gibt Änderungen als Rich-Tabelle oder JSON aus
- `--fail-on-breaking`: Exit-Code 1 wenn Breaking Changes

### Verifikation Phase 3
```bash
httrace diff --service myapp
# → Tabelle mit Änderungen
httrace diff --service myapp --fail-on-breaking
# → Exit 1 wenn Breaking Changes vorhanden
curl "https://api.httrace.com/v1/github-actions?service=myapp" -H "x-api-key: ht_..."
# → YAML-Datei
```

---

## Phase 4 — Anomaly Alerts

### 4a. Alert-Modelle

**Datei:** `/opt/httrace/backend/billing/models.py` (ergänzen)

```python
class AlertConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    api_key: str = Field(index=True)
    service: str
    channel: str  # "slack" | "email"
    destination: str  # Webhook-URL oder E-Mail
    alert_on: str  # "error_spike" | "latency_spike" | "new_endpoint" | "breaking_change"
    threshold: float = Field(default=10.0)  # % Anstieg
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

### 4b. Alert-Route

**Neue Datei:** `/opt/httrace/backend/routes/alerts.py`

```python
POST /v1/alerts          # Alert-Config erstellen
GET  /v1/alerts          # Alert-Configs auflisten
DELETE /v1/alerts/{id}   # Alert löschen
POST /v1/alerts/test     # Test-Alert senden
```

### 4c. Alert-Checker

**Neue Datei:** `/opt/httrace/backend/pipeline/alert_checker.py`

- Wird nach jedem `POST /v1/ingest` ausgeführt (Background Task)
- `check_alerts(api_key, service, session)`:
  - Error-Rate: Vergleicht letzte 5 Min vs. vorherige Stunde
  - Latency-Spike: P95 Latenz letzte 5 Min vs. vorherige Stunde
  - Neue Endpoints: Vergleicht bekannte Endpoints
- `send_slack_alert(webhook_url, message)` via `httpx.post`
- `send_email_alert(email, subject, body)` via Resend

### 4d. Dashboard: Alerts-Tab

**Datei:** `/Users/marcuswinter/Claude/httrace/landing/dashboard.html`

- Neuer Tab "Alerts"
- Formular: Service + Channel (Slack/Email) + Trigger + Threshold
- Liste bestehender Alert-Configs
- Löschen-Button pro Config

### Verifikation Phase 4
```bash
# Alert-Config erstellen
curl -X POST "https://api.httrace.com/v1/alerts" \
  -H "Authorization: Bearer JWT" \
  -d '{"service":"myapp","channel":"slack","destination":"https://hooks.slack.com/...","alert_on":"error_spike"}'
# Test-Alert senden
curl -X POST "https://api.httrace.com/v1/alerts/test" -H "Authorization: Bearer JWT"
```

---

## Phase 5 — Replay Testing

### 5a. Replay-Route

**Neue Datei:** `/opt/httrace/backend/routes/replay.py`

```python
POST /v1/replay?service=X&target_base_url=https://staging.myapp.com
```

- Lädt die letzten N CaptureRecords für den Service
- Sendet HTTP-Requests an `target_base_url` + ursprünglichen Pfad
- Vergleicht Status-Codes und Body-Schema mit den ursprünglichen Antworten
- Gibt Report zurück: `{total, passed, failed, differences: [...]}`

### 5b. CLI: `httrace replay`

**Datei:** `/Users/marcuswinter/Claude/httrace/cli/main.py`

```python
@app.command()
def replay(
    service: str = typer.Option(...),
    target: str = typer.Option(..., help="Base URL to replay against"),
    limit: int = typer.Option(50),
    fail_on_diff: bool = typer.Option(False),
):
    """Replay captured traffic against a target URL and compare responses."""
```

- Ruft `POST /v1/replay` auf
- Zeigt Rich-Tabelle mit Ergebnissen (✓/✗ pro Request)
- `--fail-on-diff`: Exit-Code 1 bei Unterschieden

### Verifikation Phase 5
```bash
httrace replay --service myapp --target https://staging.myapp.com
# → Tabelle: 45/50 passed, 5 differences
httrace replay --service myapp --target https://staging.myapp.com --fail-on-diff
# → Exit 1 bei Unterschieden
```

---

## Phase 6 — Team Features / Multi-User

### 6a. Organisation-Modelle

**Datei:** `/opt/httrace/backend/billing/models.py` (ergänzen)

```python
class Organization(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    owner_email: str
    plan: str = Field(default="free")
    created_at: datetime = ...

class OrgMember(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    email: str
    role: str = Field(default="member")  # "owner" | "admin" | "member"
    invited_at: datetime = ...
    accepted_at: Optional[datetime] = ...

class OrgApiKey(SQLModel, table=True):
    # API keys gehören zu Org statt zu einzelnem User
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    key: str = Field(unique=True, index=True)
    name: str  # "Production", "Staging" etc.
    created_by: str
```

### 6b. Org-Routen

**Neue Datei:** `/opt/httrace/backend/routes/orgs.py`

```python
POST /v1/orgs                    # Org erstellen
GET  /v1/orgs/me                 # Eigene Orgs
POST /v1/orgs/{slug}/invite      # Mitglied einladen
GET  /v1/orgs/{slug}/members     # Mitglieder auflisten
DELETE /v1/orgs/{slug}/members/{email}  # Mitglied entfernen
GET  /v1/orgs/{slug}/api-keys    # Org API Keys
POST /v1/orgs/{slug}/api-keys    # Neuen Org API Key erstellen
```

### 6c. Dashboard: Team-Tab

- Mitgliederliste mit Rollen
- Invite-Formular (E-Mail eingeben)
- API-Key-Management für die Org
- Plan-Limits gelten für die Org (nicht einzelne User)

### Verifikation Phase 6
```bash
curl -X POST "https://api.httrace.com/v1/orgs" \
  -H "Authorization: Bearer JWT" \
  -d '{"name":"Acme Corp","slug":"acme"}'
curl -X POST "https://api.httrace.com/v1/orgs/acme/invite" \
  -d '{"email":"colleague@acme.com","role":"member"}'
```

---

## Phase 7 — VS Code Extension

**Neues Verzeichnis:** `/Users/marcuswinter/Claude/httrace/vscode-extension/`

### Struktur
```
vscode-extension/
  package.json           # VS Code Extension Manifest
  src/
    extension.ts         # Aktivierungspunkt
    httrace-panel.ts     # WebviewPanel mit Dashboard-ähnlicher UI
    commands/
      generate.ts        # "Httrace: Generate Tests" Command
      diff.ts            # "Httrace: Show API Drift" Command
      replay.ts          # "Httrace: Replay Traffic" Command
    decorations/
      coverage.ts        # Inline-Decorations: "✓ tested" bei Endpunkten
```

### Features
1. **"Httrace: Generate Tests"** — ruft `POST /v1/generate-tests` auf, öffnet generierte Dateien im Editor
2. **Inline Coverage Decorations** — zeigt `✓ 42 captures` neben Route-Definitionen (FastAPI/Express)
3. **Side Panel** — zeigt Coverage + Changes wie das Web-Dashboard
4. **Status Bar Item** — "Httrace: 5 new captures" zeigt aktuelle Aktivität

### Verifikation Phase 7
- Extension in VS Code installieren via `code --install-extension httrace.vsix`
- Rechtsklick in FastAPI-Router-Datei → "Generate Tests" → `.py`-Datei öffnet sich
- Status Bar zeigt Capture-Count

---

## Phase 8 — Docs + Landing Page Update (begleitend)

### 8a. Docs-Rewrite

**Datei:** `/Users/marcuswinter/Claude/httrace/landing/docs/index.html`

Neue Sektionen hinzufügen / erweitern:

1. **Quick Start** (bereits vorhanden, erweitern)
2. **CLI Reference** — vollständige Doku aller Commands:
   - `httrace init` — API-Key setzen, Config-Datei erstellen
   - `httrace generate [--service X] [--format pytest|jest|go|rspec|vitest]`
   - `httrace status [--service X]` — Coverage-Übersicht
   - `httrace diff [--service X] [--fail-on-breaking]`
   - `httrace replay [--service X] [--target URL] [--fail-on-diff]`
3. **Middleware Reference** — Python FastAPI, Django, Flask; Node.js Express; Go; Ruby Rails
4. **Test Formats** — je ein Beispiel für alle 5 Formate
5. **GitHub Actions Integration** — vorgefertigter Workflow, erklärung der Flags
6. **OpenAPI Export** — `GET /v1/openapi.yaml` mit Beispiel
7. **Alerts & Notifications** — Slack Webhook setup, E-Mail-Alerts
8. **Replay Testing** — Erklärung, Use Cases, CLI-Beispiel
9. **Coverage & Changes API** — `GET /v1/coverage`, `GET /v1/changes` mit Response-Beispielen
10. **Team & Organizations** — Invite-Workflow, Rollen, Org-API-Keys

### 8b. Landing Page Update

**Datei:** `/Users/marcuswinter/Claude/httrace/landing/index.html`

Änderungen:
- **Hero-Tagline:** beibehalten ("Your users write your tests.")
- **How It Works:** dritten Schritt ergänzen: `httrace diff` in CI
- **Feature-Highlights** (neue Sektion nach "How It Works"):
  - Multi-format output (pytest, Jest, Go, RSpec, Vitest)
  - GitHub Actions integration in 1 line
  - API drift detection
  - Replay testing against staging
  - Slack/Email alerts
- **Vergleichstabelle** (Sektion 4): Zeile für Zeile checken, neue Rows:
  - "5 test frameworks supported" → ✅ Httrace / ❌ Keploy / ❌ GoReplay
  - "GitHub Actions native" → ✅ / ❌ / ❌
  - "API drift detection" → ✅ / ❌ / ❌
- **Pricing:** Limits überprüfen (Free: 10K, nicht 50K)
- **Social Proof** (neue Sektion nach Pricing): Platzhalter für 2-3 Testimonials

### Verifikation Phase 8
- Landing Page auf Mobile + Desktop prüfen
- Alle Links in Docs funktionieren
- Code-Beispiele in Docs sind korrekt

---

## Deployment-Strategie

Für jede Phase:
1. Neue/geänderte Python-Dateien via `scp` auf Server kopieren
2. `docker build -t httrace-api .` auf Server
3. `docker stop httrace-api && docker run -d --name httrace-api ...`
4. Neue HTML/CSS-Dateien in `landing/` über GitHub + Cloudflare Workers deployen
5. CLI-Updates über PyPI oder direkt via `pip install -e .`

**Server:** `root@46.224.203.69`  
**Backend-Pfad auf Server:** `/opt/httrace/backend/`  
**Lokaler Pfad:** `/Users/marcuswinter/Claude/httrace/`

---

## Reihenfolge der Implementierung

1. **Phase 1** (pytest-Assertions + Multi-Format) — sofort, maximaler Mehrwert
2. **Phase 8a Docs** (begleitend ab Phase 1) — neue Features sofort dokumentieren
3. **Phase 2** (OpenAPI + Dashboard Coverage/Changes) — nach Phase 1
4. **Phase 3** (GitHub Actions + `httrace diff`) — nach Phase 2
5. **Phase 8b Landing Page** — nach Phase 3 (alle neuen Features bekannt)
6. **Phase 4** (Alerts) — nach Phase 3
7. **Phase 5** (Replay) — nach Phase 4
8. **Phase 6** (Teams) — langfristig, eigener Sprint
9. **Phase 7** (VS Code Extension) — langfristig, eigener Sprint
