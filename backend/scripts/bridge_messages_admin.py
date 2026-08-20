"""
Admin CLI for the send_report/get_pending message queue (bridge_store.py)
— for inspecting or deleting rows directly, e.g. stale test messages that
ended up in a real (non-test) identity's queue before the chatgpt_test/
claude_test isolation existed. Not exposed as an MCP tool on purpose:
deleting a message is a real, irreversible action that shouldn't be
something an AI can do on its own — this is a human-run script only.

Usage (run on the actual deployed environment, e.g. `railway ssh --
python3 scripts/bridge_messages_admin.py list --sender chatgpt --recipient
claude`, not locally, since the SQLite file lives on the Railway Volume):

  python3 scripts/bridge_messages_admin.py list [--sender S] [--recipient R] [--ticker T]
  python3 scripts/bridge_messages_admin.py delete --ids 1,2,3 --yes
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import bridge_store  # noqa: E402


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(bridge_store._DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_messages(sender: str | None, recipient: str | None, ticker: str | None) -> None:
    query = "SELECT id, timestamp, sender, recipient, ticker, status, substr(content, 1, 60) AS preview FROM bridge_messages WHERE 1=1"
    params: list = []
    if sender:
        query += " AND sender = ?"
        params.append(sender)
    if recipient:
        query += " AND recipient = ?"
        params.append(recipient)
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker)
    query += " ORDER BY id ASC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    if not rows:
        print("No matching messages.")
        return
    for row in rows:
        print(f"[{row['id']}] {row['timestamp']} {row['sender']} -> {row['recipient']} "
              f"{row['ticker']} ({row['status']}): {row['preview']!r}")
    print(f"\n{len(rows)} message(s).")


def delete_messages(ids: list[int], confirmed: bool) -> None:
    if not confirmed:
        print("Refusing to delete without --yes. Run 'list' first to confirm these are the right rows.")
        return
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        cursor = conn.execute(f"DELETE FROM bridge_messages WHERE id IN ({placeholders})", ids)
        conn.commit()
        print(f"Deleted {cursor.rowcount} message(s) with ids {ids}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List messages, optionally filtered.")
    list_parser.add_argument("--sender")
    list_parser.add_argument("--recipient")
    list_parser.add_argument("--ticker")

    delete_parser = subparsers.add_parser("delete", help="Delete specific messages by id.")
    delete_parser.add_argument("--ids", required=True, help="Comma-separated message ids, e.g. 1,2,3")
    delete_parser.add_argument("--yes", action="store_true", help="Actually perform the deletion.")

    args = parser.parse_args()
    if args.command == "list":
        list_messages(args.sender, args.recipient, args.ticker)
    elif args.command == "delete":
        ids = [int(x) for x in args.ids.split(",")]
        delete_messages(ids, args.yes)


if __name__ == "__main__":
    main()
