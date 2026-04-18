"""
Billing models — API keys, usage tracking, subscriptions.
Separate from core models to keep concerns clean.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field


PLAN_QUOTAS = {
    "free":       10_000,
    "starter":  1_000_000,
    "growth":  10_000_000,
    "enterprise": 999_999_999,  # effectively unlimited
}

PLAN_PRICES_EUR = {
    "free": 0,
    "starter": 99,
    "growth": 499,
    "enterprise": 2000,
}


class ApiKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)            # tr_xxx
    owner_email: str
    plan: str = Field(default="free")                    # free|starter|growth|enterprise
    stripe_customer_id: Optional[str] = Field(default=None)
    stripe_subscription_id: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    api_key: str = Field(index=True)
    month: str = Field(index=True)           # "2026-04" — resets monthly
    requests_captured: int = Field(default=0)
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
