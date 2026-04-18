"""
Infers setUp fixtures needed for a test based on the request chain.

For each chain, looks at what IDs appear in requests and tries to generate
meaningful fixture calls that would produce those values.
"""
from typing import Any


_FIXTURE_MAP = {
    "user_id":    "create_test_user()",
    "cart_id":    "create_cart()",
    "order_id":   "create_order()",
    "product_id": "create_product()",
    "session_id": "create_session()",
    "token":      "get_auth_token()",
}


def infer_fixtures(req_body: Any, req_headers: dict) -> list[str]:
    """
    Returns a list of fixture setup lines for a test's Arrange block.
    """
    fixtures = []
    has_auth = any(
        k.lower() in ("authorization", "x-auth-token", "x-api-key")
        for k in req_headers
    )
    if has_auth:
        fixtures.append("auth = create_test_user()")

    if isinstance(req_body, dict):
        for field, fixture_call in _FIXTURE_MAP.items():
            if field in req_body:
                var_name = field.replace("_id", "").strip("_")
                fixtures.append(f"{var_name} = {fixture_call}")

    return fixtures


def resolve_body_refs(body: Any, fixtures: list[str]) -> Any:
    """
    Replace known ID fields in the body with fixture variable references
    (as string markers for the code generator to use).
    """
    if not isinstance(body, dict):
        return body
    # Build set of variable names that were generated as fixtures
    fixture_var_names = {
        line.split(" = ")[0].strip()
        for line in fixtures
        if " = " in line
    }
    result = {}
    for k, v in body.items():
        var = k.replace("_id", "").strip("_")
        if k in _FIXTURE_MAP and var in fixture_var_names:
            result[k] = f"__REF_{var}__"
        else:
            result[k] = v
    return result
