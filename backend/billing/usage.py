"""
Quota checking and usage tracking.
Called on every ingest request.

The core check_and_increment uses a single conditional UPDATE + RETURNING
so the read-check-write is atomic within SQLite's write serialization.
No race condition possible even under concurrent requests.
"""
from datetime import datetime, timezone
from sqlalchemy import text
from sqlmodel import Session, select

from .models import ApiKey, UsageRecord, PLAN_QUOTAS


class QuotaExceeded(Exception):
    def __init__(self, plan: str, used: int, limit: int):
        self.plan = plan
        self.used = used
        self.limit = limit
        super().__init__(f"Quota exceeded: {used}/{limit} on plan '{plan}'")


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_api_key(session: Session, key: str) -> ApiKey | None:
    return session.exec(select(ApiKey).where(ApiKey.key == key)).first()


def check_and_increment(session: Session, api_key: str, count: int) -> int:
    """
    Atomically verify quota and increment usage.

    Uses a conditional UPDATE with RETURNING so that the entire
    read-check-write is a single SQLite operation — no race condition
    even with multiple concurrent workers.

    Returns the new total. Raises QuotaExceeded if over the limit.
    """
    key_record = get_api_key(session, api_key)
    if not key_record or not key_record.is_active:
        raise PermissionError("Invalid or inactive API key")

    month = current_month()
    quota = PLAN_QUOTAS.get(key_record.plan, PLAN_QUOTAS["free"])
    now = datetime.now(timezone.utc).isoformat()

    # Ensure a row exists for this key+month (safe under concurrent inserts)
    session.execute(
        text(
            "INSERT OR IGNORE INTO usagerecord (api_key, month, requests_captured, last_updated) "
            "VALUES (:key, :month, 0, :now)"
        ),
        {"key": api_key, "month": month, "now": now},
    )

    # Atomic conditional increment:
    # Only updates (and returns the new total) if the result stays within quota.
    # If quota would be exceeded, the UPDATE matches 0 rows → scalar() is None.
    result = session.execute(
        text(
            "UPDATE usagerecord "
            "SET requests_captured = requests_captured + :count, last_updated = :now "
            "WHERE api_key = :key AND month = :month "
            "  AND requests_captured + :count <= :quota "
            "RETURNING requests_captured"
        ),
        {"key": api_key, "month": month, "count": count, "quota": quota, "now": now},
    )
    new_total = result.scalar()
    session.commit()

    if new_total is None:
        # Quota exceeded — fetch current usage for the error message
        current = session.execute(
            text(
                "SELECT requests_captured FROM usagerecord "
                "WHERE api_key = :key AND month = :month"
            ),
            {"key": api_key, "month": month},
        ).scalar() or 0
        raise QuotaExceeded(key_record.plan, current, quota)

    return new_total


def get_usage_summary(session: Session, api_key: str) -> dict:
    key_record = get_api_key(session, api_key)
    if not key_record:
        return {}

    month = current_month()
    usage = session.exec(
        select(UsageRecord)
        .where(UsageRecord.api_key == api_key)
        .where(UsageRecord.month == month)
    ).first()

    used = usage.requests_captured if usage else 0
    quota = PLAN_QUOTAS.get(key_record.plan, 0)
    return {
        "plan": key_record.plan,
        "month": month,
        "used": used,
        "quota": quota,
        "remaining": max(0, quota - used),
        "percent_used": round(used / quota * 100, 1) if quota else 0,
    }
