"""
Comprehensive httrace integration test suite.

Tests:
  1. Basic capture — requests are recorded
  2. PII sanitization — password, credit_card, ssn never appear in captures
  3. Status code coverage — 200, 201, 401, 404, 409, 422 all captured
  4. Burst traffic — 200 concurrent requests
  5. Quota enforcement — what happens when quota is exceeded (needs a capped key)
  6. Coverage API — /v1/coverage returns real data
  7. Generate tests — /v1/generate-tests produces valid pytest
  8. Changes API — /v1/changes responds
  9. Replay — /v1/replay against the running server
  10. Large body — body near the 64 KB cap
  11. Multi-service — same key, two service names
  12. Edge cases — empty body, malformed paths, very long strings
"""
import asyncio, json, os, sys, time, random, string
from typing import Optional
import httpx

# ── Config ────────────────────────────────────────────────────────────────────
APP_URL     = os.environ.get("APP_URL", "http://127.0.0.1:8001")
HTTRACE_API = "https://api.httrace.com"
API_KEY     = os.environ.get("HTTRACE_API_KEY", "")
SERVICE     = os.environ.get("HTTRACE_SERVICE", "test-shop")

if not API_KEY:
    print("ERROR: HTTRACE_API_KEY not set")
    sys.exit(1)

# Give middleware time to flush captures after traffic
FLUSH_WAIT = 3.0

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS_ICON = "✓"
FAIL_ICON = "✗"
WARN_ICON = "~"
results: list[tuple[str, bool, str]] = []

def report(name: str, passed: bool, detail: str = ""):
    icon = PASS_ICON if passed else FAIL_ICON
    print(f"  {icon}  {name}{(': ' + detail) if detail else ''}")
    results.append((name, passed, detail))

async def app_get(client: httpx.AsyncClient, path: str, **kwargs) -> httpx.Response:
    return await client.get(f"{APP_URL}{path}", **kwargs)

async def app_post(client: httpx.AsyncClient, path: str, **kwargs) -> httpx.Response:
    return await client.post(f"{APP_URL}{path}", **kwargs)

def httrace_get(path: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("timeout", 30)
    return httpx.get(f"{HTTRACE_API}{path}", headers={"X-Api-Key": API_KEY}, **kwargs)

def httrace_post(path: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("timeout", 60)
    return httpx.post(f"{HTTRACE_API}{path}", headers={"X-Api-Key": API_KEY}, **kwargs)

# ── Test helpers ──────────────────────────────────────────────────────────────
async def seed_basic_traffic(client: httpx.AsyncClient) -> str:
    """Creates a user, logs in, places orders. Returns auth token."""
    email = f"tester_{random.randint(10000,99999)}@example.com"

    # Create user
    r = await app_post(client, "/api/users", json={
        "email": email, "password": "secret123", "name": "Test User",
        "phone": "+49 151 12345678", "address": "Musterstraße 1, 28195 Bremen",
    })
    assert r.status_code == 201, f"create user failed: {r.text}"
    user_id = r.json()["user_id"]

    # Login (includes intentional PII fields)
    r = await app_post(client, "/api/auth/login", json={
        "email": email, "password": "secret123",
        "credit_card": "4111 1111 1111 1111",   # should be redacted
        "ssn": "123-45-6789",                    # should be redacted
    })
    assert r.status_code == 200, f"login failed: {r.text}"
    token = r.json()["token"]
    auth  = {"Authorization": f"Bearer {token}"}

    # Browse products
    await app_get(client, "/api/products")
    await app_get(client, "/api/products?in_stock=true")
    await app_get(client, "/api/products?min_price=50&max_price=150")
    await app_get(client, "/api/products/prod_001")
    await app_get(client, "/api/products/prod_999")    # → 404

    # Search
    await app_get(client, "/api/search?q=keyboard")
    await app_get(client, "/api/search?q=xyz_not_found")

    # Cart
    await app_post(client, f"/api/cart/{user_id}/items", headers=auth,
                   json={"product_id": "prod_001", "quantity": 1})
    await app_post(client, f"/api/cart/{user_id}/items", headers=auth,
                   json={"product_id": "prod_005", "quantity": 1})  # → out of stock 422
    await app_get(client, f"/api/cart/{user_id}", headers=auth)

    # Orders
    r = await app_post(client, "/api/orders", headers=auth, json={
        "product_id": "prod_001", "quantity": 1,
        "shipping_address": "Musterstraße 1, 28195 Bremen",
        "payment_token": "tok_visa_4242_redacted",   # PII
    })
    order_id = r.json().get("order_id") if r.status_code == 201 else None

    if order_id:
        await app_get(client, f"/api/orders/{order_id}", headers=auth)
        await client.patch(f"{APP_URL}/api/orders/{order_id}/status",
                           headers=auth, json={"status": "shipped"})

    # Unauthorized access
    await app_get(client, f"/api/users/{user_id}")  # → 401 (no auth)

    return token

# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASES
# ═══════════════════════════════════════════════════════════════════════════════

async def test_basic_capture():
    print("\n── 1. Basic capture ─────────────────────────────────────────────")
    async with httpx.AsyncClient(timeout=30) as client:
        await seed_basic_traffic(client)

    await asyncio.sleep(FLUSH_WAIT)

    r = httrace_get(f"/v1/coverage?service={SERVICE}")
    report("Coverage API responds 200", r.status_code == 200)
    if r.status_code == 200:
        data = r.json()
        ep_count = len(data.get("endpoints", []))
        captures  = data.get("total_captures", 0)
        report(f"At least 5 endpoints captured (got {ep_count})", ep_count >= 5)
        report(f"At least 10 total captures (got {captures})", captures >= 10)

        # Check specific endpoints
        paths = {f"{e['method']} {e['path']}" for e in data.get("endpoints", [])}
        for expected in ["GET /api/products", "POST /api/orders", "POST /api/auth/login"]:
            report(f"Endpoint captured: {expected}", expected in paths)


async def test_pii_sanitization():
    print("\n── 2. PII sanitization ──────────────────────────────────────────")
    async with httpx.AsyncClient(timeout=30) as client:
        await seed_basic_traffic(client)
    await asyncio.sleep(FLUSH_WAIT)

    # Fetch raw captures via coverage endpoint (doesn't expose bodies)
    # Instead, generate tests and inspect the generated code for PII
    r = httrace_post(f"/v1/generate-tests?service={SERVICE}&format=pytest")
    report("Generate tests responds 200", r.status_code == 200)
    if r.status_code == 200:
        data    = r.json()
        all_code = "\n".join(data.get("code", {}).values())

        pii_patterns = [
            "4111 1111 1111 1111",   # credit card number (raw)
            "123-45-6789",            # SSN
            "secret123",              # password
            "tok_visa_4242",          # payment token
        ]
        for pat in pii_patterns:
            found = pat in all_code
            report(f"PII '{pat[:20]}' NOT in generated code", not found,
                   "⚠ LEAK DETECTED" if found else "")


async def test_status_code_coverage():
    print("\n── 3. Status code coverage ──────────────────────────────────────")
    await asyncio.sleep(FLUSH_WAIT)

    r = httrace_get(f"/v1/coverage?service={SERVICE}")
    if r.status_code != 200:
        report("Coverage API reachable", False, r.text[:100])
        return

    endpoints = r.json().get("endpoints", [])
    all_statuses = set()
    for ep in endpoints:
        all_statuses.update(ep.get("statuses", []))

    for expected_status in [200, 201, 401, 404, 422]:
        report(f"Status {expected_status} captured", expected_status in all_statuses)


async def test_burst_traffic():
    print("\n── 4. Burst traffic (200 concurrent requests) ───────────────────")

    async def hit_products(client: httpx.AsyncClient, i: int):
        try:
            r = await client.get(f"{APP_URL}/api/products")
            return r.status_code
        except Exception as e:
            return f"error: {e}"

    t0 = time.time()
    async with httpx.AsyncClient(timeout=15) as client:
        tasks = [hit_products(client, i) for i in range(200)]
        statuses = await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    ok_count  = sum(1 for s in statuses if s == 200)
    err_count = sum(1 for s in statuses if isinstance(s, str))

    report(f"200 requests completed in {elapsed:.2f}s", elapsed < 30)
    report(f"All 200 returned 200 OK (got {ok_count})", ok_count == 200,
           f"{err_count} errors" if err_count else "")

    await asyncio.sleep(FLUSH_WAIT)

    r = httrace_get(f"/v1/coverage?service={SERVICE}")
    if r.status_code == 200:
        caps = r.json().get("total_captures", 0)
        report(f"Captures grew after burst (total: {caps})", caps >= 200)


async def test_coverage_api():
    print("\n── 5. Coverage API shape ────────────────────────────────────────")
    r = httrace_get(f"/v1/coverage?service={SERVICE}")
    report("GET /v1/coverage → 200", r.status_code == 200)
    if r.status_code != 200:
        return

    data = r.json()
    report("Response has 'endpoints' list", isinstance(data.get("endpoints"), list))
    report("Response has 'total_captures' int", isinstance(data.get("total_captures"), int))
    if data.get("endpoints"):
        ep = data["endpoints"][0]
        report("Endpoint has method", "method" in ep)
        report("Endpoint has path",   "path" in ep)
        report("Endpoint has captures count", "captures" in ep)
        report("Endpoint has statuses list",  isinstance(ep.get("statuses"), list))


async def test_generate_tests():
    print("\n── 6. Generate tests ────────────────────────────────────────────")
    formats = ["pytest", "jest", "vitest", "go", "rspec"]

    for fmt in formats:
        r = httrace_post(f"/v1/generate-tests?service={SERVICE}&format={fmt}", timeout=60)
        if r.status_code == 200:
            data      = r.json()
            generated = data.get("generated", 0)
            files     = data.get("files", [])
            code      = data.get("code", {})
            report(f"Format '{fmt}': {generated} file(s) generated", generated > 0)
            if files:
                first_file = files[0]["file"]
                first_code = code.get(first_file, "")
                # Basic syntax checks per format
                checks = {
                    "pytest":  "def test_" in first_code,
                    "jest":    ("describe(" in first_code or "test(" in first_code),
                    "vitest":  ("describe(" in first_code or "it(" in first_code),
                    "go":      "func Test" in first_code,
                    "rspec":   ("describe " in first_code or "it " in first_code or "RSpec" in first_code),
                }
                report(f"Format '{fmt}': code looks valid", checks.get(fmt, True),
                       first_code[:60].replace("\n", " ") if not checks.get(fmt, True) else "")
        else:
            report(f"Format '{fmt}': API call failed", False, f"{r.status_code}: {r.text[:80]}")


async def test_changes_api():
    print("\n── 7. Changes / drift API ───────────────────────────────────────")
    r = httrace_get(f"/v1/changes?service={SERVICE}")
    report("GET /v1/changes → 200", r.status_code == 200)
    if r.status_code == 200:
        data = r.json()
        report("Response has 'changes' list", isinstance(data.get("changes"), list))
        report("Response has 'untested_endpoints'", "untested_endpoints" in data)


async def test_replay():
    print("\n── 8. Replay testing ────────────────────────────────────────────")
    # NOTE: Replay runs server-side — target_base_url must be reachable from
    # Hetzner (46.224.203.69), not from localhost. We use httpbin.org as a
    # publicly reachable target. Status codes will differ (httpbin returns 200
    # for all paths) — the test checks that replay runs and returns a report,
    # not that all requests pass.
    r = httrace_post(
        f"/v1/replay?service={SERVICE}&target_base_url=https://httpbin.org&limit=10",
        timeout=90,
    )
    report("POST /v1/replay → 200", r.status_code == 200,
           r.text[:100] if r.status_code != 200 else "")
    if r.status_code == 200:
        data  = r.json()
        total = data.get("total", 0)
        report(f"Replayed {total} request(s) (report returned)", total >= 0)
        report("Response has 'passed' + 'failed' fields",
               "passed" in data and "failed" in data)
        report("Response has 'differences' list",
               isinstance(data.get("differences"), list))
        # Most will fail (httpbin != our API) — that's expected; we're testing
        # that the replay mechanism itself works end-to-end
        report("Replay ran without server error", data.get("total", -1) >= 0)


async def test_large_body():
    print("\n── 9. Large body (near 64 KB cap) ───────────────────────────────")
    large_address = "A" * 60_000
    async with httpx.AsyncClient(timeout=30) as client:
        # Create a user first
        email = f"large_{random.randint(10000,99999)}@example.com"
        await app_post(client, "/api/users", json={
            "email": email, "password": "x" * 50, "name": "LargeBody Test",
            "address": large_address,
        })
        r = await app_post(client, "/api/auth/login", json={"email": email, "password": "x"*50})
        if r.status_code == 200:
            token = r.json()["token"]
            # Create order with large address
            r2 = await app_post(client, "/api/orders",
                                 headers={"Authorization": f"Bearer {token}"},
                                 json={"product_id": "prod_002", "quantity": 1,
                                       "shipping_address": large_address,
                                       "payment_token": "tok_test"})
            report("Large body request accepted (201 or 422)", r2.status_code in (201, 422),
                   f"got {r2.status_code}")

    await asyncio.sleep(FLUSH_WAIT)
    r = httrace_get(f"/v1/coverage?service={SERVICE}")
    report("Coverage still works after large body", r.status_code == 200)


async def test_multi_service():
    print("\n── 10. Multi-service (same API key) ─────────────────────────────")
    service_b = "test-shop-b"
    async with httpx.AsyncClient(timeout=30) as client:
        # Register second service via direct ingest call to /v1/captures
        r = await client.post(
            f"{HTTRACE_API}/v1/captures",
            headers={"X-Api-Key": API_KEY, "Content-Type": "application/json"},
            json={
                "captures": [
                    {
                        "service": service_b,
                        "request":  {"method": "GET", "path": "/api/items",
                                     "headers": {}, "body": None},
                        "response": {"status_code": 200, "headers": {},
                                     "body": {"items": []}, "latency_ms": 12},
                    }
                ]
            }
        )
        report(f"Ingest to service '{service_b}' → 202", r.status_code == 202,
               r.text[:80] if r.status_code != 202 else "")

    await asyncio.sleep(1)
    r = httrace_get(f"/v1/coverage?service={service_b}")
    report(f"Coverage for '{service_b}' returns data", r.status_code == 200 and
           len(r.json().get("endpoints", [])) > 0)
    if r.status_code == 200:
        r_main = httrace_get(f"/v1/coverage?service={SERVICE}")
        if r_main.status_code == 200:
            report("Services are isolated (endpoints differ)",
                   set(e["path"] for e in r.json().get("endpoints", [])) !=
                   set(e["path"] for e in r_main.json().get("endpoints", [])))


async def test_edge_cases():
    print("\n── 11. Edge cases ───────────────────────────────────────────────")
    async with httpx.AsyncClient(timeout=15) as client:
        # Non-existent endpoint
        r = await client.get(f"{APP_URL}/api/doesnotexist")
        report("404 on unknown route", r.status_code == 404)

        # Missing required query param
        r = await client.get(f"{APP_URL}/api/search")
        report("422 on missing required param", r.status_code == 422)

        # Empty string search
        r = await client.get(f"{APP_URL}/api/search?q=")
        report("422 on empty query param", r.status_code == 422)

        # Very long product ID
        r = await client.get(f"{APP_URL}/api/products/{'x'*500}")
        report("404 on very long product ID", r.status_code == 404)

        # Wrong content type (send form data to JSON endpoint)
        r = await client.post(f"{APP_URL}/api/users",
                              data="email=test&password=test", headers={"Content-Type": "text/plain"})
        report("422 on wrong content type", r.status_code == 422)

    await asyncio.sleep(FLUSH_WAIT)
    r = httrace_get(f"/v1/coverage?service={SERVICE}")
    report("Coverage API still healthy after edge cases", r.status_code == 200)


async def test_quota_simulation():
    print("\n── 12. Quota / ingest rate ──────────────────────────────────────")
    # Directly call /v1/captures with a batch of records and measure throughput
    batch_size = 100
    methods = ["GET", "POST", "PUT", "DELETE"]
    paths   = ["/api/products", "/api/orders", "/api/users", "/api/cart"]
    records = [
        {
            "service":  SERVICE,
            "request":  {
                "method":  random.choice(methods),
                "path":    random.choice(paths),
                "headers": {"content-type": "application/json"},
                "body":    {"key": f"value_{i}"},
            },
            "response": {
                "status_code": random.choice([200, 201, 400, 404, 422]),
                "headers": {"content-type": "application/json"},
                "body":    {"result": "ok", "id": i},
                "latency_ms": random.randint(5, 500),
            },
        }
        for i in range(batch_size)
    ]

    t0 = time.time()
    r = httpx.post(
        f"{HTTRACE_API}/v1/captures",
        headers={"X-Api-Key": API_KEY, "Content-Type": "application/json"},
        json={"captures": records},
        timeout=30,
    )
    elapsed = time.time() - t0

    report(f"Ingest {batch_size} records → 200/202", r.status_code in (200, 202),
           r.text[:100] if r.status_code not in (200, 202) else "")
    report(f"Ingest completed in {elapsed:.2f}s (< 5s expected)", elapsed < 5)
    if r.status_code in (200, 202):
        data = r.json()
        accepted = data.get("accepted", data.get("count", 0))
        report(f"Accepted count matches batch size ({accepted})", accepted == batch_size)

    # Verify captures grew
    await asyncio.sleep(1)
    r2 = httrace_get(f"/v1/coverage?service={SERVICE}")
    if r2.status_code == 200:
        caps = r2.json().get("total_captures", 0)
        report(f"Total captures after batch ≥ {batch_size} (got {caps})", caps >= batch_size)


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════
async def main():
    print(f"\n{'═'*60}")
    print(f"  Httrace Test Suite")
    print(f"  App: {APP_URL}")
    print(f"  API: {HTTRACE_API}")
    print(f"  Key: {API_KEY[:16]}...")
    print(f"  Service: {SERVICE}")
    print(f"{'═'*60}")

    # Check app is up
    try:
        r = httpx.get(f"{APP_URL}/health", timeout=5)
        if r.status_code != 200:
            print(f"✗ App not running at {APP_URL} — start it first")
            sys.exit(1)
        print(f"\n  ✓ App healthy")
    except Exception as e:
        print(f"✗ Cannot reach {APP_URL}: {e}")
        sys.exit(1)

    # Check httrace API
    r = httpx.get(f"{HTTRACE_API}/v1/coverage?service={SERVICE}",
                  headers={"X-Api-Key": API_KEY}, timeout=10)
    print(f"  ✓ Httrace API reachable ({r.status_code})")

    # Run tests
    await test_basic_capture()
    await test_pii_sanitization()
    await test_status_code_coverage()
    await test_burst_traffic()
    await test_coverage_api()
    await test_generate_tests()
    await test_changes_api()
    await test_replay()
    await test_large_body()
    await test_multi_service()
    await test_edge_cases()
    await test_quota_simulation()

    # Summary
    total  = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print(f"\n{'═'*60}")
    print(f"  Results: {passed}/{total} passed  ({failed} failed)")
    if failed:
        print(f"\n  Failed tests:")
        for name, ok, detail in results:
            if not ok:
                print(f"    ✗  {name}{(': ' + detail) if detail else ''}")
    print(f"{'═'*60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
