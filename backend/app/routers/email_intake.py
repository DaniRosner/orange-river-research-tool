"""
Inbound email intake: the user forwards an email straight to
TICKER@<mailgun inbound domain>, Mailgun parses it and POSTs the result
here as a plain webhook (not an MCP tool — this is only ever hit by
Mailgun's own servers, never by an AI). Lets him get an email into a
ticker's real Dropbox folder from anywhere (phone, webmail), no app
open — the drag-onto-a-folder-card path in the frontend covers the
"already have the app open" case; this covers everywhere else.

Ticker routing is address-based, not subject parsing — his own real
forwarded subjects ("Fwd: TTAM on VIC") don't reliably carry a clean
ticker, so guessing from them would need the same fallback anyway. The
whole local part of the recipient address is taken as the ticker tag
(a fixed "+tag" style address also still works, but isn't required —
see _ticker_tag_from_recipient). An unresolved/ambiguous ticker never
gets guessed at:
unlike save_final's AI-driven flow, there's no interactive turn here to
ask a clarifying question, so it lands in the app's own existing Needs
Review folder instead — reusing that feature's already-built "assign to
a ticker" flow (see files.py's assign_needs_review_file) rather than
inventing a separate, invisible-to-the-frontend holding folder (an
earlier version of this file did exactly that, and a real test forward
confirmed the gap: the file existed in Dropbox but never showed up
anywhere in the app, since Needs Review is the one folder the frontend
actually tracks and lists).

A batch of emails forwarded together via Gmail/Outlook's "Forward as
attachment" (each original message attached as its own .eml, rather
than one email's own body) is handled by the same rule, applied once
per attachment instead of once for the whole request — see
_is_nested_email/_resolve_and_file_email below. One consistent
priority order covers every case: an explicit address that resolves to
a real ticker always wins (for the outer email AND every nested
attachment — addressing a batch to one real ticker deliberately files
everything in it there, e.g. "I want all of these in one bucket").
Only when the address doesn't resolve to a real ticker does each
individual email (the outer one, or each nested attachment
independently) fall back to scanning its own Subject line for an
unambiguous real ticker, before finally deferring to Needs Review.
"""

import hashlib
import hmac
import json
import logging
import re
from email.utils import parseaddr, parsedate_to_datetime

from fastapi import APIRouter, Request

# NOT fastapi.UploadFile — a distinct subclass, not the same type. This
# router reads multipart fields via request.form() directly (no FastAPI
# File()/UploadFile() dependency injection involved at all), and that
# always hands back plain starlette.datastructures.UploadFile instances.
# Checking isinstance(x, fastapi.UploadFile) against one of those is
# always False — confirmed directly (fastapi.UploadFile is a subclass,
# not an alias) after a real attachment silently vanished with no error
# despite the payload genuinely containing it, correctly typed, right up
# until this exact check.
from starlette.datastructures import UploadFile

from app.config import settings
from app.services import activity_log, dropbox_client, eml_parse, email_render, notifications, sorting, ticker_registry

logger = logging.getLogger(__name__)

router = APIRouter()

_EMAIL_INTAKE_USER = {"full_name": "Email Intake", "email": "email-intake@yourfirm.local"}

# Matches Mailgun's "attachment-N" field naming exactly (not
# "attachment-count" or anything else) — used to discover attachments
# directly from the form rather than trusting attachment-count (see
# the comment where this is used, in inbound_email()).
_ATTACHMENT_KEY_RE = re.compile(r"^attachment-\d+$")


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
    """Pulls the ticker tag out of the recipient's local part (everything
    before the '@'). the user just addresses the forward directly to the
    ticker itself — "ZPAX@sandbox123.mailgun.org" — so the whole local
    part is the tag; a '+' is also still accepted ("anything+ZPAX@...")
    for anyone who'd rather keep a fixed base address and vary the tag
    after a plus, but it's optional, not required. None only for a
    genuinely empty local part (nothing to route it to at all)."""
    local_part = recipient.split("@", 1)[0].strip()
    if "+" in local_part:
        local_part = local_part.split("+", 1)[1].strip()
    return local_part or None


def _is_allowed_sender(sender: str) -> bool:
    """True if `sender`'s address matches settings.email_intake_allowed_senders
    — a domain entry (no "@") matches any address at that domain, a full
    address matches exactly. Case-insensitive (email domains/local parts
    aren't meaningfully case-sensitive in practice). Empty settings value
    means nothing is accepted (fails closed) — see the config.py comment
    for why this check exists at all."""
    _, address = parseaddr(sender or "")
    address = address.lower()
    if not address or "@" not in address:
        return False
    domain = address.split("@", 1)[1]
    allowed = [entry.strip().lower() for entry in settings.email_intake_allowed_senders.split(",") if entry.strip()]
    for entry in allowed:
        if "@" in entry:
            if address == entry:
                return True
        elif domain == entry:
            return True
    return False


def _is_nested_email(attachment: UploadFile) -> bool:
    """True for an attachment that is itself a whole email — Gmail/
    Outlook's "Forward as attachment" on a batch of selected messages
    sends each original message this way, one per attachment, rather
    than as the outer email's own body. Checked two ways (either is
    enough) since real-world delivery of nested messages varies: some
    clients/servers set a proper message/rfc822 content type, others
    just carry a .eml-named file with a generic content type."""
    content_type = (attachment.content_type or "").lower()
    filename = (attachment.filename or "").lower()
    return content_type == "message/rfc822" or filename.endswith(".eml")


_PATH_UNSAFE_RE = re.compile(r"[/\\]")


_FORWARD_REPLY_PREFIX_RE = re.compile(r"^\s*(?:(?:fwd|fw|re)\s*:\s*)+", re.IGNORECASE)


def _clean_subject(subject: str) -> str:
    """Strips repeated leading Fwd:/Fw:/Re: markers a forward chain piles
    on (e.g. "Fwd: Fwd: Re: SMRT notes" -> "SMRT notes") — those say
    nothing about the email itself, just how many times it's been
    forwarded, so they make otherwise-different emails harder to tell
    apart in a folder rather than easier."""
    return _FORWARD_REPLY_PREFIX_RE.sub("", subject or "").strip()


def _sender_display_name(sender: str) -> str:
    """Just the human name from a From header ("John Smith
    <john@company.com>" -> "John Smith") — never the email address
    itself, which is long and not what makes one email distinguishable
    from another at a glance. Falls back to the address's own local part
    (before the @) if there's no display name, and to "" (omitted from
    the filename entirely) if sender is empty/unparseable."""
    name, address = parseaddr(sender or "")
    if name:
        return name
    return address.split("@", 1)[0] if address else ""


def _format_date_for_filename(date_str: str) -> str:
    """YYYY-MM-DD from a raw email Date header, "" if missing/unparseable
    — omitted from the filename entirely rather than guessing."""
    if not date_str:
        return ""
    try:
        return parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _sanitize_for_dropbox_path(name: str) -> str:
    """A real email Subject line can contain "/" (confirmed directly —
    a forward with subject "Fw: SMRT/Tegus Call with ..." landed as a
    file named "Tegus Call with ..." *inside* an accidentally-created
    "Email - Fw: SMRT" folder, since Dropbox's upload API treats any "/"
    in a path exactly like a real path separator). Filenames built from
    arbitrary email text need this before ever being joined into a
    Dropbox path — "\\" is included too, since Windows-style paths in a
    forwarded subject would hit the same problem."""
    return _PATH_UNSAFE_RE.sub("-", name)


def _resolve_and_file_email(
    subject: str,
    sender: str,
    date_str: str,
    body_text: str,
    known: dict[str, str],
    explicit_resolution: dict | None,
    unresolved_tag: str | None,
) -> tuple[str, str | None, str]:
    """Shared by the outer email and every nested .eml attachment (see
    module docstring for the priority order this implements): renders
    subject/sender/date/body into its own PDF and files it, then logs
    the save. `explicit_resolution` is the recipient address's own
    ticker_registry.resolve_ticker() result — a "matched" real ticker
    there always wins over content detection. Otherwise falls back to
    sorting.find_ticker_mentioned_in_text() against `subject`, then
    finally to Needs Review. `unresolved_tag`, shown in the Needs Review
    filename if it gets that far, is the typed address tag for the
    outer email, or None for a nested attachment (which has no typed
    tag of its own — see the "[ticker unclear]" fallback label).
    Returns (actual_filename, real_ticker_or_None, folder_it_landed_in).
    """
    pdf_bytes = email_render.render_email_to_pdf(subject, sender, date_str, body_text)
    # "Email - {subject} - {sender} - {date}" so two emails with the same
    # (often generic, e.g. "notes" or "call") subject are still
    # distinguishable at a glance in a folder listing, without needing an
    # AI call to summarize the body — see project memory/discussion on
    # why body-content summarization was ruled out (cost/infra, not
    # worth it for a filename). Forward/reply markers are stripped from
    # the subject first (a forward chain's "Fwd: Fwd: Re:" says nothing
    # about the email itself), and the sender is just their display name,
    # never the full email address — any piece that's missing/unparseable
    # is simply omitted rather than leaving an empty "- -" gap.
    name_parts = [
        part
        for part in (
            _clean_subject(subject) or "Forwarded email",
            _sender_display_name(sender),
            _format_date_for_filename(date_str),
        )
        if part
    ]
    base_filename = _sanitize_for_dropbox_path(f"Email - {' - '.join(name_parts)}"[:140])

    if explicit_resolution and explicit_resolution["kind"] == "matched":
        real_ticker = explicit_resolution["ticker"]
        folder = f"{ticker_registry.folder_path_for_status(explicit_resolution['status'])}/{real_ticker}"
        pdf_filename = f"{base_filename}.pdf"
    else:
        detected = sorting.find_ticker_mentioned_in_text(subject, list(known.keys()))
        if detected:
            real_ticker = detected
            folder = f"{ticker_registry.folder_path_for_status(known[detected])}/{detected}"
            pdf_filename = f"{base_filename}.pdf"
        else:
            real_ticker = None
            folder = settings.dropbox_needs_review_path
            label = f"[{unresolved_tag}]" if unresolved_tag else "[ticker unclear]"
            pdf_filename = f"{label} {base_filename}.pdf"

    actual_filename = dropbox_client.upload_file(f"{folder}/{pdf_filename}", pdf_bytes, overwrite=False)
    activity_log.record(
        _EMAIL_INTAKE_USER,
        "uploaded",
        ticker=real_ticker,
        filename=actual_filename,
        detail="forwarded email",
    )
    return actual_filename, real_ticker, folder


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

    # A verified Mailgun signature only proves the POST came from Mailgun,
    # not that the original email's sender is anyone the user actually
    # wants filing things into his real Dropbox — see config.py's
    # email_intake_allowed_senders comment. Checked before any ticker
    # routing, since an unauthorized sender's content shouldn't be
    # processed at all, not even filed to Needs Review.
    sender = form.get("from", "") or form.get("sender", "")
    if not _is_allowed_sender(sender):
        logger.warning("Email intake: rejected an email from an unauthorized sender %r.", sender)
        return {"status": "rejected", "reason": "unauthorized sender"}

    recipient = form.get("recipient", "")
    ticker_tag = _ticker_tag_from_recipient(recipient)
    if not ticker_tag:
        logger.info("Email intake: no +TICKER tag on recipient %r — nothing to route.", recipient)
        return {"status": "ignored", "reason": "no ticker tag on recipient"}

    subject = form.get("subject", "")
    date_str = _header_value(form.get("message-headers", ""), "Date")
    # body-plain (the full, unstripped plain-text body) FIRST, not
    # stripped-text — Mailgun's stripped-text is designed to strip out
    # quoted/forwarded content, treating it as a signature or an old
    # reply to trim. That's exactly backwards for this use case: a
    # forwarded email IS the wanted content, not noise to remove.
    # Confirmed directly on a real forward: stripped-text left nothing
    # but the sender's own signature line, body-plain has the real
    # message.
    body_text = form.get("body-plain") or form.get("stripped-text") or ""

    known = ticker_registry.get_known_tickers()
    # A plus-address tag is always a deliberate choice, same reasoning
    # bridge.py's ticker-resolution helpers use for a dragged folder's
    # name or a save_final ticker argument — uppercase it before
    # resolving so resolve_ticker's lowercase-looks-like-a-sentence
    # heuristic (meant for text parsed out of a filename) never applies
    # here. See module docstring for the full priority order this feeds
    # into: passed as-is to _resolve_and_file_email below, which only
    # actually uses it when its kind is "matched" — a real ticker there
    # wins outright, for the outer email AND every nested .eml
    # attachment (addressing a batch to one real ticker means "all of
    # these go in one bucket").
    resolution = ticker_registry.resolve_ticker(ticker_tag.upper(), known)

    actual_filename, real_ticker, folder = _resolve_and_file_email(
        subject, sender, date_str, body_text, known, resolution, ticker_tag.upper()
    )
    preview_url = dropbox_client.get_shareable_link(f"{folder}/{actual_filename}")

    # Discovered directly from the form rather than trusting
    # attachment-count: a real 3-attachment test confirmed attachment-1/
    # 2/3 present and correctly typed in the payload, but
    # attachment-count came back empty/zero for Mailgun's "Forward"
    # route action specifically (unlike its documented behavior for a
    # plain webhook), which silently skipped every attachment via
    # range(1, 0+1). Scanning the actual keys sidesteps that field
    # entirely, regardless of whether it's ever populated correctly.
    attachment_items = sorted(
        (
            (key, value)
            for key, value in form.multi_items()
            if _ATTACHMENT_KEY_RE.match(key) and isinstance(value, UploadFile)
        ),
        key=lambda item: int(item[0].split("-", 1)[1]),
    )
    saved_attachments = []
    for _key, attachment in attachment_items:
        data = await attachment.read()
        if _is_nested_email(attachment):
            # A whole other email attached — one of a batch from
            # "Forward as attachment," not a real file the user meant to
            # keep alongside this one. Same resolution passed through:
            # an explicit real-ticker address still wins for this
            # attachment too; only falls back to its own Subject line
            # when the outer address didn't resolve to a real ticker.
            parsed = eml_parse.parse_eml(data)
            nested_filename, _, _ = _resolve_and_file_email(
                parsed["subject"], parsed["sender"], parsed["date_str"], parsed["body_text"], known, resolution, None
            )
            saved_attachments.append(nested_filename)
            continue
        safe_attachment_name = _sanitize_for_dropbox_path(attachment.filename or "attachment")
        attachment_filename = safe_attachment_name if real_ticker else f"[{ticker_tag.upper()}] {safe_attachment_name}"
        saved_name = dropbox_client.upload_file(f"{folder}/{attachment_filename}", data, overwrite=False)
        saved_attachments.append(saved_name)

    if real_ticker is None:
        _notify_needs_sorting(ticker_tag, preview_url)

    return {
        "status": "saved" if real_ticker else "needs_sorting",
        "ticker": real_ticker or ticker_tag.upper(),
        "filename": actual_filename,
        "attachments": saved_attachments,
        "preview_url": preview_url,
    }
