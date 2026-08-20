"""
Storage for the ChatGPT<->Claude research-report "bridge" (see
app/routers/bridge.py) — a small mailbox table letting one AI hand a
report to the other, and a record of when the user's been emailed about a
new one. Backed by its own SQLite connection pattern (mirrors
activity_log.py deliberately rather than importing from it) but lives in
the same DB file at settings.activity_db_path, so it rides the same
Railway Volume and survives redeploys with no new infra.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# the user's timezone (Your Firm) — everything is stored as UTC
# (see `timestamp`/`delivered_at` below), which is the right call for
# storage, but showing an AI-facing tool a raw UTC ISO string just gets
# relayed to the user as-is, in the wrong timezone. _to_dict formats a
# parallel human-readable Eastern-time field for exactly that reason.
_DISPLAY_TZ = ZoneInfo("America/New_York")

# How long a duplicate send_report call (same sender/recipient/ticker/
# content) gets treated as "the same one" rather than a genuinely new
# message — see send() below. Real-world trigger: ChatGPT's own client
# times out waiting on a slow permission-confirmation click and silently
# retries the tool call, which without this would create several
# identical pending messages for the other AI to see.
_DEDUPE_WINDOW = timedelta(minutes=2)

from app.config import settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_configured_path = Path(settings.activity_db_path)
_DB_PATH = _configured_path if _configured_path.is_absolute() else _BACKEND_ROOT / _configured_path


@contextmanager
def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bridge_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                ticker TEXT NOT NULL,
                content TEXT NOT NULL,
                note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                delivered_at TEXT,
                notified_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bridge_recipient_status ON bridge_messages(recipient, status, ticker)"
        )
        # Added after the table already existed in production — a plain
        # CREATE TABLE IF NOT EXISTS above wouldn't add these to an
        # existing table, so migrate forward explicitly. Safe to run every
        # startup: ADD COLUMN fails harmlessly once the column is there.
        for column_sql in (
            "ALTER TABLE bridge_messages ADD COLUMN encoding TEXT NOT NULL DEFAULT 'text'",
            "ALTER TABLE bridge_messages ADD COLUMN filename TEXT",
        ):
            try:
                conn.execute(column_sql)
            except sqlite3.OperationalError:
                pass  # column already exists


_init_db()


def _format_et(timestamp_iso: str | None) -> str | None:
    """Converts a stored UTC ISO timestamp to a human-readable Eastern
    time string (e.g. "Aug 19, 2026 4:03 PM ET") — handles the EST/EDT
    switch automatically via zoneinfo. Returns None as-is for a null
    delivered_at rather than raising."""
    if not timestamp_iso:
        return None
    dt = datetime.fromisoformat(timestamp_iso).astimezone(_DISPLAY_TZ)
    label = "EDT" if dt.dst() else "EST"
    return dt.strftime(f"%b %-d, %Y %-I:%M %p {label}")


def _to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "timestamp_et": _format_et(row["timestamp"]),
        "sender": row["sender"],
        "recipient": row["recipient"],
        "ticker": row["ticker"],
        "content": row["content"],
        "encoding": row["encoding"],
        "filename": row["filename"],
        "note": row["note"],
        "status": row["status"],
        "delivered_at": row["delivered_at"],
        "delivered_at_et": _format_et(row["delivered_at"]),
    }


def send(
    sender: str,
    recipient: str,
    ticker: str,
    content: str,
    note: str | None = None,
    encoding: str = "text",
    filename: str | None = None,
) -> int:
    """Insert a new pending message — `content` is either plain text or,
    when `encoding='base64'`, a real file's bytes (e.g. a draft financial
    model) with `filename` naming it, exactly the same encoding scheme
    save_final uses. Returns the id of an existing still-pending message
    instead of inserting a new one if it's an exact duplicate (same
    sender/recipient/ticker/content/encoding) sent within the last
    _DEDUPE_WINDOW — see that constant's comment for why this matters.
    Returns the row's id either way, so callers can't tell the
    difference."""
    with _connect() as conn:
        cutoff = (datetime.now(timezone.utc) - _DEDUPE_WINDOW).isoformat()
        existing = conn.execute(
            "SELECT id FROM bridge_messages WHERE sender = ? AND recipient = ? AND ticker = ? "
            "AND content = ? AND encoding = ? AND status = 'pending' AND timestamp > ? ORDER BY id DESC LIMIT 1",
            (sender, recipient, ticker, content, encoding, cutoff),
        ).fetchone()
        if existing:
            return existing["id"]

        cursor = conn.execute(
            "INSERT INTO bridge_messages (timestamp, sender, recipient, ticker, content, note, encoding, filename) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), sender, recipient, ticker, content, note, encoding, filename),
        )
        return cursor.lastrowid


def fetch_pending(recipient: str, ticker: str | None = None, include_delivered: bool = True) -> list[dict]:
    """
    Messages addressed to `recipient`, optionally scoped to one `ticker`.
    Returns full history by default (include_delivered=True) — status/
    delivered_at is bookkeeping only, never a filter that hides a message
    from a later check. Any still-'pending' row in the result is flipped
    to 'delivered' as a side effect of being returned, regardless of
    which mode fetched it. Pass include_delivered=False for the narrower
    "only what's genuinely new" behavior, if ever needed.
    """
    with _connect() as conn:
        query = "SELECT * FROM bridge_messages WHERE recipient = ?"
        params: list = [recipient]
        if not include_delivered:
            query += " AND status = 'pending'"
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        query += " ORDER BY id ASC"
        rows = conn.execute(query, params).fetchall()

        pending_ids = [row["id"] for row in rows if row["status"] == "pending"]
        if pending_ids:
            placeholders = ",".join("?" * len(pending_ids))
            conn.execute(
                f"UPDATE bridge_messages SET status = 'delivered', delivered_at = ? WHERE id IN ({placeholders})",
                (datetime.now(timezone.utc).isoformat(), *pending_ids),
            )

        return [_to_dict(row) for row in rows]


def fetch_unnotified() -> list[dict]:
    """Messages nobody's been emailed about yet, regardless of delivery
    status — used by the notification poll loop in app/main.py."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bridge_messages WHERE notified_at IS NULL ORDER BY id ASC"
        ).fetchall()
        return [_to_dict(row) for row in rows]


def mark_notified(message_ids: list[int]) -> None:
    if not message_ids:
        return
    with _connect() as conn:
        placeholders = ",".join("?" * len(message_ids))
        conn.execute(
            f"UPDATE bridge_messages SET notified_at = ? WHERE id IN ({placeholders})",
            (datetime.now(timezone.utc).isoformat(), *message_ids),
        )
