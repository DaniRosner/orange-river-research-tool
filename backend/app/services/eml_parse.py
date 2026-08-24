"""
Parses a raw .eml attachment's bytes into plain fields — used by
app/routers/email_intake.py when an inbound email carries other emails
as attachments (Gmail/Outlook's "Forward as attachment" on a batch of
selected messages). Each attached .eml is a real, standalone email (full
MIME structure, its own Subject/From/Date/body), not something the outer
email's own headers say anything about — this reads it directly rather
than treating it like an opaque binary file.
"""

import email
from email.header import decode_header
from email.message import Message


def _decode_header_value(raw: str | None) -> str:
    """Email headers can carry MIME-encoded-word text for non-ASCII
    subjects/names (e.g. "=?UTF-8?B?...?="); decode_header() splits that
    into (bytes_or_str, charset) chunks that need reassembling. Returns
    "" for a missing header rather than "None", so callers don't need
    their own null-check."""
    if not raw:
        return ""
    parts = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _extract_body_text(msg: Message) -> str:
    """First text/plain part found, decoded with its own declared
    charset — "" if none exists. Deliberately doesn't fall back to
    converting a text/html-only part: that's a lossy, format-specific
    step (same reasoning document_text.py already uses for formats it
    won't guess-convert), and this function's caller can still route
    and file the email correctly off its Subject line alone even with
    an empty body."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    if msg.get_content_type() == "text/plain":
        payload = msg.get_payload(decode=True)
        if payload is not None:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def parse_eml(data: bytes) -> dict:
    """Returns {"subject": str, "sender": str, "date_str": str,
    "body_text": str} — every field defaults to "" rather than raising,
    since a malformed/unusual nested email shouldn't block the rest of
    a bulk-forward batch from being processed."""
    msg = email.message_from_bytes(data)
    return {
        "subject": _decode_header_value(msg.get("Subject")),
        "sender": _decode_header_value(msg.get("From")),
        "date_str": msg.get("Date", ""),
        "body_text": _extract_body_text(msg),
    }
