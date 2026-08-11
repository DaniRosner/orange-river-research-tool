"""
Lightweight local log of who did what through this app — uploads,
deletes, renames, moves, assigns. Exists specifically because Dropbox's
own "modified by" only reflects activity done directly in Dropbox
(desktop sync, their own web app, etc.) — every action taken *through
this app* runs under one fixed service account (see
dropbox_client.get_client()), so Dropbox's own attribution would always
show that same identity regardless of which signed-in team member
actually did it. This records the real person instead, using the session
identity already established at sign-in (see app/services/auth.py) — no
change to Dropbox auth/permissions needed.

Backed by a local SQLite file rather than Dropbox itself: far simpler, no
risk of concurrent-write races on a shared log file, and easy to query.
The one real tradeoff: this is local disk state, not synced anywhere —
`settings.activity_db_path` must point at a mounted Railway Volume once
deployed (see README.md "Deploying"), or the log resets on every
redeploy. A relative path (the local-dev default) resolves against the
backend package root; an absolute path (a Railway volume's mount point)
is used as-is.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_name TEXT NOT NULL,
                user_email TEXT NOT NULL,
                action TEXT NOT NULL,
                ticker TEXT,
                filename TEXT,
                relative_path TEXT NOT NULL DEFAULT '',
                detail TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_file ON activity(ticker, filename, relative_path)")


_init_db()


def record(
    user: dict,
    action: str,
    *,
    ticker: str | None = None,
    filename: str | None = None,
    relative_path: str = "",
    detail: str | None = None,
) -> None:
    """
    Logs one action. `user` is the session dict from auth.current_user
    (has `name`/`full_name`/`email`) — the full display name is what gets
    stored (see below), matching the "Welcome, {full_name}" greeting and
    the Owner column's full-name display, rather than the shorter
    `given_name` used nowhere else in the app. What ticker/filename mean
    together:
    - ticker set, filename set: a file-level action inside a real ticker
      (upload, delete, assign).
    - ticker None, filename set: a Needs Review file action (nothing's
      been filed under a ticker yet).
    - ticker set, filename None: a ticker-level action (create, rename,
      move, delete-ticker).
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO activity (timestamp, user_name, user_email, action, ticker, filename, relative_path, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                user["full_name"],
                user["email"],
                action,
                ticker,
                filename,
                relative_path or "",
                detail,
            ),
        )


def _to_dict(row: sqlite3.Row) -> dict:
    return {
        "action": row["action"],
        "user_name": row["user_name"],
        "user_email": row["user_email"],
        "timestamp": row["timestamp"],
        "detail": row["detail"],
    }


def latest_for_files(ticker: str, filenames: list[str]) -> dict[tuple[str, str], dict]:
    """
    Batch lookup: the most recent action for each (filename,
    relative_path) under `ticker`, in one query — used by
    list_files_for_ticker() to enrich a whole file list without one
    request per file. A file with no logged activity (existed before this
    feature, or was only ever touched directly in Dropbox) simply won't
    have an entry — callers should treat a missing entry as "unknown,"
    not an error.
    """
    if not filenames:
        return {}
    with _connect() as conn:
        placeholders = ",".join("?" * len(filenames))
        rows = conn.execute(
            f"SELECT * FROM activity WHERE ticker = ? AND filename IN ({placeholders}) ORDER BY id DESC",
            (ticker, *filenames),
        ).fetchall()

    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["filename"], row["relative_path"])
        if key not in latest:
            latest[key] = _to_dict(row)
    return latest


def latest_for_ticker(ticker: str) -> dict | None:
    """Most recent *ticker-level* action (created/renamed/moved/deleted)
    — not file-level activity within it. Used for e.g. "created by X" on
    a ticker card."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM activity WHERE ticker = ? AND filename IS NULL ORDER BY id DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return _to_dict(row) if row else None


def latest_for_tickers(tickers: list[str]) -> dict[str, dict]:
    """Batch version of latest_for_ticker() — one query for a whole tab's
    worth of tickers instead of one per ticker."""
    if not tickers:
        return {}
    with _connect() as conn:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT * FROM activity WHERE filename IS NULL AND ticker IN ({placeholders}) ORDER BY id DESC",
            tickers,
        ).fetchall()

    latest: dict[str, dict] = {}
    for row in rows:
        if row["ticker"] not in latest:
            latest[row["ticker"]] = _to_dict(row)
    return latest


def latest_for_needs_review(filenames: list[str]) -> dict[str, dict]:
    """Same idea as latest_for_files(), for Needs Review files (no ticker
    assigned yet, so keyed by filename alone)."""
    if not filenames:
        return {}
    with _connect() as conn:
        placeholders = ",".join("?" * len(filenames))
        rows = conn.execute(
            f"SELECT * FROM activity WHERE ticker IS NULL AND filename IN ({placeholders}) ORDER BY id DESC",
            filenames,
        ).fetchall()

    latest: dict[str, dict] = {}
    for row in rows:
        if row["filename"] not in latest:
            latest[row["filename"]] = _to_dict(row)
    return latest
