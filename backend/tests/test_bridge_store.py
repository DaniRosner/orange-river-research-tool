from datetime import datetime, timedelta, timezone

import pytest

from app.services import bridge_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point bridge_store at a fresh temp DB file for each test, instead
    of the real activity DB — mirrors how the module resolves its path at
    import time, just redirected."""
    monkeypatch.setattr(bridge_store, "_DB_PATH", tmp_path / "test_bridge.db")
    bridge_store._init_db()


def test_send_creates_pending_message():
    message_id = bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", "first pass")
    assert message_id == 1

    pending = bridge_store.fetch_pending("claude")
    assert len(pending) == 1
    assert pending[0]["sender"] == "chatgpt"
    assert pending[0]["ticker"] == "ZBQ"
    assert pending[0]["content"] == "draft content"
    assert pending[0]["note"] == "first pass"


def test_fetch_pending_only_returns_messages_for_that_recipient():
    bridge_store.send("chatgpt", "claude", "ZBQ", "for claude", None)
    bridge_store.send("claude", "chatgpt", "ZBQ", "for chatgpt", None)

    assert len(bridge_store.fetch_pending("claude")) == 1
    assert len(bridge_store.fetch_pending("chatgpt")) == 1


def test_fetch_pending_scoped_to_ticker():
    bridge_store.send("chatgpt", "claude", "ZBQ", "zbq content", None)
    bridge_store.send("chatgpt", "claude", "ZPAX", "zpax content", None)

    zbq_only = bridge_store.fetch_pending("claude", ticker="ZBQ")
    assert len(zbq_only) == 1
    assert zbq_only[0]["ticker"] == "ZBQ"


def test_fetch_pending_marks_delivered_and_does_not_redeliver():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)

    first_fetch = bridge_store.fetch_pending("claude", include_delivered=False)
    assert len(first_fetch) == 1

    second_fetch = bridge_store.fetch_pending("claude", include_delivered=False)
    assert second_fetch == []


def test_fetch_pending_default_returns_full_history():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)
    bridge_store.fetch_pending("claude")  # delivers it

    # Default call (no include_delivered passed) should still show it —
    # status/delivered_at is bookkeeping, never a filter that hides a
    # message from a later default fetch.
    again = bridge_store.fetch_pending("claude")
    assert len(again) == 1
    assert again[0]["status"] == "delivered"


def test_include_delivered_shows_history_without_mutating():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)
    bridge_store.fetch_pending("claude")  # marks it delivered

    history = bridge_store.fetch_pending("claude", include_delivered=True)
    assert len(history) == 1
    assert history[0]["status"] == "delivered"

    # Calling again with include_delivered=True should be a pure read —
    # still shows up, nothing new gets mutated.
    history_again = bridge_store.fetch_pending("claude", include_delivered=True)
    assert len(history_again) == 1


def test_fetch_unnotified_and_mark_notified():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)
    bridge_store.send("claude", "chatgpt", "ZPAX", "another draft", None)

    unnotified = bridge_store.fetch_unnotified()
    assert len(unnotified) == 2

    bridge_store.mark_notified([m["id"] for m in unnotified])

    assert bridge_store.fetch_unnotified() == []


def test_mark_notified_does_not_affect_delivery_status():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)

    unnotified = bridge_store.fetch_unnotified()
    bridge_store.mark_notified([m["id"] for m in unnotified])

    # Notifying about a message is independent of the recipient AI having
    # actually fetched it yet — it should still show up as pending.
    pending = bridge_store.fetch_pending("claude")
    assert len(pending) == 1


def test_send_dedupes_identical_pending_message_within_window():
    # Simulates the real ChatGPT retry-on-slow-confirmation bug: the exact
    # same tool call arriving twice in quick succession should not create
    # two separate pending messages for the other AI to see.
    first_id = bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", "first pass")
    second_id = bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", "first pass")

    assert first_id == second_id
    assert len(bridge_store.fetch_pending("claude")) == 1


def test_send_does_not_dedupe_different_content_or_ticker():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft v1", None)
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft v2", None)
    bridge_store.send("chatgpt", "claude", "ZPAX", "draft v1", None)

    assert len(bridge_store.fetch_pending("claude")) == 3


def test_send_does_not_dedupe_outside_the_window(monkeypatch):
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)

    # Simulate the first message having been sent well before the dedupe
    # window by monkeypatching what "now" the module sees.
    later = datetime.now(timezone.utc) + bridge_store._DEDUPE_WINDOW + timedelta(minutes=1)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    monkeypatch.setattr(bridge_store, "datetime", _FrozenDatetime)
    second_id = bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)

    assert second_id == 2
    assert len(bridge_store.fetch_pending("claude")) == 2


def test_send_dedupe_ignores_already_delivered_messages():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)
    bridge_store.fetch_pending("claude")  # marks it delivered

    # A second, later send of literally the same content is a genuinely
    # new event once the first one was already delivered — not a retry.
    second_id = bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)
    assert second_id == 2
    assert len(bridge_store.fetch_pending("claude", include_delivered=False)) == 1


def test_send_defaults_to_text_encoding_with_no_filename():
    bridge_store.send("chatgpt", "claude", "ZBQ", "draft content", None)
    pending = bridge_store.fetch_pending("claude")
    assert pending[0]["encoding"] == "text"
    assert pending[0]["filename"] is None


def test_send_carries_a_real_file():
    bridge_store.send(
        "chatgpt", "claude", "ZBQ", "ZmFrZSBiaW5hcnkgYnl0ZXM=", "draft model",
        encoding="base64", filename="ZBQ Draft Model.xlsx",
    )
    pending = bridge_store.fetch_pending("claude")
    assert len(pending) == 1
    assert pending[0]["encoding"] == "base64"
    assert pending[0]["filename"] == "ZBQ Draft Model.xlsx"
    assert pending[0]["content"] == "ZmFrZSBiaW5hcnkgYnl0ZXM="


def test_send_dedupe_treats_different_encoding_as_distinct():
    # Same ticker/content string, but one's plain text and one's meant to
    # be interpreted as base64 — must not be deduped against each other.
    bridge_store.send("chatgpt", "claude", "ZBQ", "same-string", None, encoding="text")
    bridge_store.send("chatgpt", "claude", "ZBQ", "same-string", None, encoding="base64", filename="x.bin")

    assert len(bridge_store.fetch_pending("claude")) == 2
