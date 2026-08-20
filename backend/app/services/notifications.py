"""
One-line email pings to the user when the ChatGPT<->Claude bridge (see
app/routers/bridge.py, app/services/bridge_store.py) has something new for
him to look at. Plain SMTP with a Gmail app password — deliberately not a
transactional-email vendor, since this is one recipient getting a handful
of short notifications, not bulk mail.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def notifications_enabled() -> bool:
    return bool(settings.bridge_notify_email and settings.bridge_notify_smtp_user and settings.bridge_notify_smtp_app_password)


def send_bridge_notification(messages: list[dict]) -> bool:
    """Send one email summarizing new bridge messages. `messages` is a
    list of bridge_store row dicts — never empty when this is called.
    Returns whether it actually sent, so the poll loop in app/main.py only
    marks messages notified on real success — a failed send should leave
    them unmarked so the next poll retries instead of silently dropping
    the notification."""
    lines = [f"- {m['sender']} sent something on {m['ticker']}" + (f": {m['note']}" if m["note"] else "") for m in messages]
    body = "New research-report activity in the Your Firm bridge:\n\n" + "\n".join(lines)

    email = EmailMessage()
    email["Subject"] = "Your Firm bridge: new update" if len(messages) == 1 else f"Your Firm bridge: {len(messages)} new updates"
    email["From"] = settings.bridge_notify_smtp_user
    email["To"] = settings.bridge_notify_email
    email.set_content(body)

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(settings.bridge_notify_smtp_user, settings.bridge_notify_smtp_app_password)
            smtp.send_message(email)
        return True
    except Exception:
        # A failed notification shouldn't take down the poll loop or crash
        # anything upstream — worst case the user just doesn't get pinged for
        # this round and the messages stay marked unnotified for a retry.
        logger.exception("Failed to send bridge notification email")
        return False
