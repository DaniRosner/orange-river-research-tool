import hashlib
import hmac

import pytest

from app.routers import email_intake


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr(email_intake.settings, "mailgun_webhook_signing_key", "test-signing-key")


def _sign(timestamp: str, token: str, key: str = "test-signing-key") -> str:
    return hmac.new(key.encode("utf-8"), f"{timestamp}{token}".encode("utf-8"), hashlib.sha256).hexdigest()


def test_verify_signature_accepts_correctly_signed_request():
    timestamp, token = "1234567890", "abcdefTOKEN"
    signature = _sign(timestamp, token)
    assert email_intake._verify_signature(timestamp, token, signature) is True


def test_verify_signature_rejects_wrong_signature():
    timestamp, token = "1234567890", "abcdefTOKEN"
    assert email_intake._verify_signature(timestamp, token, "not-the-real-signature") is False


def test_verify_signature_rejects_signature_signed_with_wrong_key():
    timestamp, token = "1234567890", "abcdefTOKEN"
    forged = _sign(timestamp, token, key="attacker-guessed-key")
    assert email_intake._verify_signature(timestamp, token, forged) is False


def test_verify_signature_rejects_when_no_signing_key_configured(monkeypatch):
    monkeypatch.setattr(email_intake.settings, "mailgun_webhook_signing_key", "")
    timestamp, token = "1234567890", "abcdefTOKEN"
    signature = _sign(timestamp, token)
    assert email_intake._verify_signature(timestamp, token, signature) is False


def test_ticker_tag_from_recipient_extracts_plus_tag():
    assert email_intake._ticker_tag_from_recipient("intake+ZPAX@sandbox123.mailgun.org") == "ZPAX"


def test_ticker_tag_from_recipient_none_without_plus():
    assert email_intake._ticker_tag_from_recipient("intake@sandbox123.mailgun.org") is None


def test_ticker_tag_from_recipient_none_for_empty_tag():
    assert email_intake._ticker_tag_from_recipient("intake+@sandbox123.mailgun.org") is None
