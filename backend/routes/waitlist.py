"""
Waitlist signup endpoint.
Saves the email to the database and sends two emails via Resend:
  1. A notification to the founders
  2. A confirmation to the subscriber
"""
import os
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from ..database import get_session
from ..models import WaitlistEntry

logger = logging.getLogger("httrace.waitlist")
router = APIRouter(prefix="/v1", tags=["waitlist"])

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL", "founders@httrace.com")
FROM_EMAIL     = os.getenv("FROM_EMAIL", "httrace <noreply@httrace.com>")


class WaitlistRequest(BaseModel):
    email: EmailStr


def _send_emails(email: str, position: int) -> None:
    """Fire-and-forget email sending via Resend. Logs but never raises."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email for %s", email)
        return

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        # 1. Notify founders
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": NOTIFY_EMAIL,
            "subject": f"New waitlist signup #{position}: {email}",
            "html": f"""
            <p>New signup on the Httrace waitlist.</p>
            <p><strong>Email:</strong> {email}</p>
            <p><strong>Position:</strong> #{position}</p>
            """,
        })

        # 2. Confirm to subscriber
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "You're on the Httrace waitlist.",
            "html": """
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto;color:#111">
              <h2 style="font-size:24px;margin-bottom:8px">You're on the list.</h2>
              <p style="color:#555;line-height:1.6">
                Thanks for signing up for early access to
                <strong>Httrace</strong> — the tool that turns your production
                HTTP traffic into pytest integration tests automatically.
              </p>
              <p style="color:#555;line-height:1.6">
                We'll reach out personally as soon as we open beta access.
                You'll be among the first in, with founding team pricing.
              </p>
              <p style="margin-top:32px;color:#999;font-size:13px">
                — The Httrace Team<br>
                <a href="https://httrace.com" style="color:#3b82f6">httrace.com</a>
              </p>
            </div>
            """,
        })

        logger.info("Emails sent for waitlist signup: %s (#%d)", email, position)

    except Exception:
        logger.exception("Failed to send Resend emails for %s", email)


@router.post("/waitlist", status_code=201)
async def join_waitlist(
    body: WaitlistRequest,
    session: Session = Depends(get_session),
):
    # Check if already signed up
    existing = session.exec(
        select(WaitlistEntry).where(WaitlistEntry.email == body.email)
    ).first()
    if existing:
        # Return success anyway — no need to tell the user they're a duplicate
        return {"message": "You're on the list."}

    entry = WaitlistEntry(email=body.email)
    session.add(entry)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return {"message": "You're on the list."}

    # Count total signups for position number in the notification email
    position = session.exec(select(WaitlistEntry)).all().__len__()

    _send_emails(body.email, position)

    return {"message": "You're on the list."}


@router.get("/waitlist/count")
async def waitlist_count(session: Session = Depends(get_session)):
    """Public endpoint — returns total signup count for the website counter."""
    count = len(session.exec(select(WaitlistEntry)).all())
    return {"count": count}
