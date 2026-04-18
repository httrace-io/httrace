"""
Stripe webhook handler.
Listens for subscription changes and updates the local ApiKey plan accordingly.

Setup in Stripe Dashboard:
  Endpoint URL: https://your-domain.com/v1/billing/webhook
  Events to send:
    - customer.subscription.updated
    - customer.subscription.deleted
    - invoice.payment_failed
"""
import os
import secrets
import logging
from fastapi import APIRouter, Request, HTTPException
from sqlmodel import Session, select

from ..database import engine
from .models import ApiKey
from .usage import get_usage_summary

logger = logging.getLogger("httrace.billing")

router = APIRouter(prefix="/v1/billing", tags=["billing"])

PRICE_TO_PLAN: dict[str, str] = {
    os.getenv("STRIPE_PRICE_STARTER", "price_starter"):    "starter",
    os.getenv("STRIPE_PRICE_GROWTH", "price_growth"):      "growth",
    os.getenv("STRIPE_PRICE_ENTERPRISE", "price_ent"):     "enterprise",
}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    import stripe
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    event_type = event["type"]
    logger.info("Stripe event: %s", event_type)

    with Session(engine) as session:
        if event_type in ("customer.subscription.updated", "customer.subscription.created"):
            _handle_subscription_update(session, event["data"]["object"])
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(session, event["data"]["object"])
        elif event_type == "invoice.payment_failed":
            logger.warning("Payment failed for customer %s", event["data"]["object"].get("customer"))

    return {"received": True}


def _handle_subscription_update(session: Session, subscription: dict) -> None:
    customer_id = subscription.get("customer")
    key = session.exec(select(ApiKey).where(ApiKey.stripe_customer_id == customer_id)).first()
    if not key:
        return
    price_id = subscription["items"]["data"][0]["price"]["id"]
    key.plan = PRICE_TO_PLAN.get(price_id, "free")
    key.stripe_subscription_id = subscription["id"]
    session.add(key)
    session.commit()
    logger.info("Updated %s → plan=%s", key.owner_email, key.plan)


def _handle_subscription_deleted(session: Session, subscription: dict) -> None:
    customer_id = subscription.get("customer")
    key = session.exec(select(ApiKey).where(ApiKey.stripe_customer_id == customer_id)).first()
    if key:
        key.plan = "free"
        session.add(key)
        session.commit()
        logger.info("Downgraded %s → free (subscription cancelled)", key.owner_email)


@router.post("/provision-key")
async def provision_key(request: Request):
    # Require a shared admin secret — set PROVISION_SECRET in environment
    provision_secret = os.getenv("PROVISION_SECRET", "")
    if not provision_secret:
        raise HTTPException(status_code=503, detail="Key provisioning not configured")
    auth_header = request.headers.get("authorization", "")
    if auth_header != f"Bearer {provision_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="email required")

    new_key = f"ht_{secrets.token_urlsafe(24)}"
    plan = data.get("plan", "starter")
    with Session(engine) as session:
        key = ApiKey(
            key=new_key,
            owner_email=email,
            plan=plan,
            stripe_customer_id=data.get("stripe_customer_id"),
        )
        session.add(key)
        session.commit()

    return {"api_key": new_key, "plan": plan}


@router.get("/usage")
async def get_usage(request: Request):
    api_key = request.headers.get("x-api-key", "")
    if not api_key.startswith("ht_"):
        raise HTTPException(status_code=401, detail="Invalid API key")
    with Session(engine) as session:
        return get_usage_summary(session, api_key)
