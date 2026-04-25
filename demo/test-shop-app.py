"""
Httrace test app — realistic e-commerce API with multiple endpoints.
Used to test httrace capture, PII sanitization, quota enforcement.
"""
import os, uuid, random
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

# ── In-memory "database" ──────────────────────────────────────────────────────
PRODUCTS = [
    {"id": "prod_001", "name": "Wireless Headphones",   "price": 79.99,  "stock": 50},
    {"id": "prod_002", "name": "Mechanical Keyboard",   "price": 129.99, "stock": 20},
    {"id": "prod_003", "name": "USB-C Hub",             "price": 39.99,  "stock": 100},
    {"id": "prod_004", "name": "Monitor Stand",         "price": 49.99,  "stock": 15},
    {"id": "prod_005", "name": "Webcam HD",             "price": 89.99,  "stock": 0},   # out of stock
]

ORDERS: dict = {}
USERS: dict = {}
CART: dict = {}

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Httrace Test Shop API", version="1.0.0")

# Attach httrace middleware
HTTRACE_API_KEY = os.environ.get("HTTRACE_API_KEY", "")
HTTRACE_SERVICE = os.environ.get("HTTRACE_SERVICE", "test-shop")
if HTTRACE_API_KEY:
    try:
        from httrace import HttraceCaptureMiddleware
        app.add_middleware(
            HttraceCaptureMiddleware,
            api_key=HTTRACE_API_KEY,
            service=HTTRACE_SERVICE,
            sample_rate=float(os.environ.get("HTTRACE_SAMPLE_RATE", "1.0")),
        )
        print(f"✓ Httrace middleware active — service={HTTRACE_SERVICE}, key={HTTRACE_API_KEY[:12]}...")
    except ImportError:
        print("⚠ httrace not installed, skipping middleware")
else:
    print("⚠ HTTRACE_API_KEY not set, skipping middleware")

# ── Models ────────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    email: str
    password: str
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str
    credit_card: Optional[str] = None    # intentional PII — should be redacted
    ssn: Optional[str] = None            # intentional PII — should be redacted

class OrderCreate(BaseModel):
    product_id: str
    quantity: int
    shipping_address: str
    payment_token: str                   # PII — should be redacted

class CartItem(BaseModel):
    product_id: str
    quantity: int

# ── Auth helper ───────────────────────────────────────────────────────────────
def require_auth(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid auth token")
    return authorization[7:]

# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

# ── Products ──────────────────────────────────────────────────────────────────
@app.get("/api/products")
def list_products(
    category: Optional[str] = Query(default=None),
    min_price: Optional[float] = Query(default=None),
    max_price: Optional[float] = Query(default=None),
    in_stock: bool = Query(default=False),
):
    products = PRODUCTS[:]
    if in_stock:
        products = [p for p in products if p["stock"] > 0]
    if min_price is not None:
        products = [p for p in products if p["price"] >= min_price]
    if max_price is not None:
        products = [p for p in products if p["price"] <= max_price]
    return {"products": products, "total": len(products)}

@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return product

# ── Users ─────────────────────────────────────────────────────────────────────
@app.post("/api/users", status_code=201)
def create_user(body: UserCreate):
    if body.email in USERS:
        raise HTTPException(status_code=409, detail="Email already registered")
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    USERS[body.email] = {"id": uid, "email": body.email, "name": body.name}
    return {"user_id": uid, "email": body.email, "name": body.name}

@app.post("/api/auth/login")
def login(body: LoginRequest):
    # Simulate auth — always succeeds with a fake JWT
    if body.email not in USERS:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = f"tok_{uuid.uuid4().hex}"
    return {"token": token, "user_id": USERS[body.email]["id"], "expires_in": 3600}

@app.get("/api/users/{user_id}")
def get_user(user_id: str, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    user = next((u for u in USERS.values() if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# ── Cart ──────────────────────────────────────────────────────────────────────
@app.get("/api/cart/{user_id}")
def get_cart(user_id: str, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    items = CART.get(user_id, [])
    total = sum(
        next((p["price"] for p in PRODUCTS if p["id"] == i["product_id"]), 0) * i["quantity"]
        for i in items
    )
    return {"user_id": user_id, "items": items, "total": round(total, 2)}

@app.post("/api/cart/{user_id}/items", status_code=201)
def add_to_cart(user_id: str, item: CartItem, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    product = next((p for p in PRODUCTS if p["id"] == item.product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product["stock"] == 0:
        raise HTTPException(status_code=422, detail="Product out of stock")
    cart = CART.setdefault(user_id, [])
    existing = next((i for i in cart if i["product_id"] == item.product_id), None)
    if existing:
        existing["quantity"] += item.quantity
    else:
        cart.append({"product_id": item.product_id, "quantity": item.quantity})
    return {"message": "Added to cart", "cart_size": len(cart)}

@app.delete("/api/cart/{user_id}/items/{product_id}", status_code=204)
def remove_from_cart(user_id: str, product_id: str, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    cart = CART.get(user_id, [])
    CART[user_id] = [i for i in cart if i["product_id"] != product_id]
    return None

# ── Orders ────────────────────────────────────────────────────────────────────
@app.post("/api/orders", status_code=201)
def create_order(body: OrderCreate, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    product = next((p for p in PRODUCTS if p["id"] == body.product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product["stock"] < body.quantity:
        raise HTTPException(status_code=422, detail="Insufficient stock")

    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    total = round(product["price"] * body.quantity, 2)
    order = {
        "order_id": order_id,
        "product_id": body.product_id,
        "product_name": product["name"],
        "quantity": body.quantity,
        "total": total,
        "shipping_address": body.shipping_address,
        "status": "confirmed",
        "created_at": datetime.utcnow().isoformat(),
    }
    ORDERS[order_id] = order
    product["stock"] -= body.quantity
    return order

@app.get("/api/orders/{order_id}")
def get_order(order_id: str, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail="Order not found")
    return ORDERS[order_id]

@app.get("/api/orders")
def list_orders(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, le=100),
    authorization: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    orders = list(ORDERS.values())
    if status:
        orders = [o for o in orders if o["status"] == status]
    return {"orders": orders[:limit], "total": len(orders)}

@app.patch("/api/orders/{order_id}/status")
def update_order_status(
    order_id: str,
    body: dict,
    authorization: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail="Order not found")
    new_status = body.get("status")
    if new_status not in ("confirmed", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=422, detail="Invalid status")
    ORDERS[order_id]["status"] = new_status
    return ORDERS[order_id]

# ── Search ────────────────────────────────────────────────────────────────────
@app.get("/api/search")
def search(q: str = Query(..., min_length=1)):
    results = [p for p in PRODUCTS if q.lower() in p["name"].lower()]
    return {"query": q, "results": results, "count": len(results)}
