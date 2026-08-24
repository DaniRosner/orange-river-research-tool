"""
Inbound email intake: the user forwards an email to intake+TICKER@<mailgun
inbound domain>, Mailgun parses it and POSTs the result here as a plain
webhook (not an MCP tool — this is only ever hit by Mailgun's own
servers, never by an AI). Lets him get an email into a ticker's real
Dropbox folder from anywhere (phone, webmail), no app open — the drag-
onto-a-folder-card path in the frontend covers the "already have the app
open" case; this covers everywhere else.

Ticker routing is plus-addressing (intake+ZPAX@...), not subject
parsing — his own real forwarded subjects ("Fwd: TTAM on VIC") don't
reliably carry a clean ticker, so guessing from them would need the same
fallback anyway. An unresolved/ambiguous ticker never gets guessed at:
unlike save_final's AI-driven flow, there's no interactive turn here to
ask a clarifying question, so it lands in the app's own existing Needs
Review folder instead — reusing that feature's already-built "assign to
a ticker" flow (see files.py's assign_needs_review_file) rather than
inventing a separate, invisible-to-the-frontend holding folder (an
earlier version of this file did exactly that, and a real test forward
confirmed the gap: the file existed in Dropbox but never showed up
anywhere in the app, since Needs Review is the one folder the frontend
actually tracks and lists).
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Request, UploadFile

from app.config import settings
from app.services import activity_log, dropbox_client, email_render, notifications, ticker_registry

logger = logging.getLogger(__name__)

router = APIRouter()

_EMAIL_INTAKE_USER = {"full_name": "Email Intake", "email": "email-intake@yourfirm.local"}


def _verify_signature(timestamp: str, token: str, signature: str) -> bool:
    """Mailgun's inbound-route signing scheme: HMAC-SHA256 over
    timestamp+token, keyed with the account's webhook signing key,
    compared to the `signature` field Mailgun sends alongside. This is a
    write path into the user's real Dropbox with no human-in-the-loop
    confirmation step, so an unverified POST must never be trusted —
    every other check in this router assumes this one already passed."""
    if not settings.mailgun_webhook_signing_key:
        return False
    expected = hmac.new(
        key=settings.mailgun_webhook_signing_key.encode("utf-8"),
        msg=f"{timestamp}{token}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _header_value(message_headers_json: str, header_name: str) -> str:
    """Mailgun doesn't hand over a plain "date"/"from" field for most
    original-message headers — instead `message-headers` is a JSON-
    encoded list of [name, value] pairs straight off the original email
    (see Mailgun's inbound-route docs). Pulls one out by name,
    case-insensitively (email header casing isn't guaranteed
    consistent); returns "" if it's missing or the field isn't valid
    JSON — never raises, since a malformed/missing header here shouldn't
    block filing the email itself."""
    try:
        headers = json.loads(message_headers_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    for name, value in headers:
        if name.lower() == header_name.lower():
            return value
    return ""


def _ticker_tag_from_recipient(recipient: str) -> str | None:
    """Pulls TICKER out of a plus-addressed recipient like
    "intake+ZPAX@sandbox123.mailgun.org" — None if there's no '+' tag at
    all (nothing to route it to)."""
    local_part = recipient.split("@", 1)[0]
    if "+" not in local_part:
        return None
    tag = local_part.split("+", 1)[1].strip()
    return tag or None


def _notify_needs_sorting(ticker_tag: str, preview_url: str) -> None:
    if not notifications.notifications_enabled():
        return
    notifications.send_bridge_notification(
        [
            {
                "sender": "Email Intake",
                "ticker": ticker_tag,
                "note": f"Forwarded email couldn't be auto-filed under a known ticker — check the Needs Review tab in the app. {preview_url}",
            }
        ]
    )


@router.post("/inbound")
async def inbound_email(request: Request) -> dict:
    """Mailgun's inbound-route webhook target. multipart/form-data with
    the parsed email's fields (recipient, sender, subject, timestamp,
    token, signature, stripped-text/body-plain, attachment-N files) —
    read directly off the request rather than declared as individual
    Form(...) params, since Mailgun's field set includes a variable
    number of attachment-N files this endpoint doesn't know the count of
    ahead of time.

    Always returns 200 with a plain status dict (Mailgun doesn't retry
    on anything but a genuine 5xx) — a routing miss (unverified
    signature, no ticker tag, unresolved ticker) is a normal, logged
    outcome here, not a server error."""
    form = await request.form()

    timestamp = form.get("timestamp", "")
    token = form.get("token", "")
    signature = form.get("signature", "")
    if not _verify_signature(timestamp, token, signature):
        logger.warning("Email intake: rejected a webhook POST with an invalid/missing Mailgun signature.")
        return {"status": "rejected", "reason": "invalid signature"}

    recipient = form.get("recipient", "")
    ticker_tag = _ticker_tag_from_recipient(recipient)
    if not ticker_tag:
        logger.info("Email intake: no +TICKER tag on recipient %r — nothing to route.", recipient)
        return {"status": "ignored", "reason": "no ticker tag on recipient"}

    subject = form.get("subject", "")
    sender = form.get("from", "") or form.get("sender", "")
    date_str = _header_value(form.get("message-headers", ""), "Date")
    body_text = form.get("stripped-text") or form.get("body-plain") or ""

    known = ticker_registry.get_known_tickers()
    # A plus-address tag is always a deliberate choice, same reasoning
    # bridge.py's ticker-resolution helpers use for a dragged folder's
    # name or a save_final ticker argument — uppercase it before
    # resolving so resolve_ticker's lowercase-looks-like-a-sentence
    # heuristic (meant for text parsed out of a filename) never applies
    # here.
    resolution = ticker_registry.resolve_ticker(ticker_tag.upper(), known)

    pdf_bytes = email_render.render_email_to_pdf(subject, sender, date_str, body_text)
    base_filename = (subject or "Forwarded email").strip()[:80]

    if resolution["kind"] == "matched":
        folder = f"{ticker_registry.folder_path_for_status(resolution['status'])}/{resolution['ticker']}"
        real_ticker = resolution["ticker"]
        pdf_filename = f"{base_filename}.pdf"
    else:
        # new_ticker_needs_status / confirm_needed / not_a_ticker — no
        # interactive turn to ask a clarifying question here, so this
        # goes to the app's own Needs Review folder (flat, no per-ticker
        # subfolders — see files.py's list_needs_review) rather than
        # guessing a bucket or silently creating a folder. The typed
        # ticker tag is prefixed onto the filename, since Needs Review
        # has nowhere else to show it, so the user sees at a glance what he
        # typed when he goes to assign it a real ticker.
        folder = settings.dropbox_needs_review_path
        real_ticker = None
        pdf_filename = f"[{ticker_tag.upper()}] {base_filename}.pdf"

    actual_filename = dropbox_client.upload_file(f"{folder}/{pdf_filename}", pdf_bytes, overwrite=False)
    preview_url = dropbox_client.get_shareable_link(f"{folder}/{actual_filename}")

    attachment_count = int(form.get("attachment-count", "0") or "0")
    saved_attachments = []
    for i in range(1, attachment_count + 1):
        attachment = form.get(f"attachment-{i}")
        if not isinstance(attachment, UploadFile):
            continue
        data = await attachment.read()
        attachment_filename = attachment.filename if real_ticker else f"[{ticker_tag.upper()}] {attachment.filename}"
        saved_name = dropbox_client.upload_file(f"{folder}/{attachment_filename}", data, overwrite=False)
        saved_attachments.append(saved_name)

    activity_log.record(
        _EMAIL_INTAKE_USER,
        "uploaded",
        ticker=real_ticker,
        filename=actual_filename,
        detail=f"forwarded email ({len(saved_attachments)} attachment(s))" if saved_attachments else "forwarded email",
    )

    if real_ticker is None:
        _notify_needs_sorting(ticker_tag, preview_url)

    return {
        "status": "saved" if real_ticker else "needs_sorting",
        "ticker": real_ticker or ticker_tag.upper(),
        "filename": actual_filename,
        "attachments": saved_attachments,
        "preview_url": preview_url,
    }
