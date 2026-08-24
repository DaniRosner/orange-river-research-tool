import pytest

from app.routers import email_intake

_KNOWN = {"ZPAX": "active", "ZBQ": "active"}


@pytest.fixture(autouse=True)
def stub_dropbox_and_activity(monkeypatch):
    """_resolve_and_file_email's own logic (which of three paths it takes
    and what folder/filename it picks) is what these tests care about —
    stub out the real Dropbox write and activity log so no network/DB
    call happens, and capture what path was actually written to."""
    uploaded = []

    def fake_upload_file(path, content, overwrite=False):
        uploaded.append(path)
        return path.rsplit("/", 1)[-1]

    monkeypatch.setattr(email_intake.dropbox_client, "upload_file", fake_upload_file)
    monkeypatch.setattr(email_intake.activity_log, "record", lambda *a, **k: None)
    return uploaded


def test_explicit_matched_ticker_wins_over_subject_content(stub_dropbox_and_activity):
    # Subject mentions ZBQ, but the address explicitly resolved to ZPAX —
    # explicit address must win (this is the "all of these go in one
    # bucket" case).
    explicit = {"kind": "matched", "ticker": "ZPAX", "status": "active"}
    filename, real_ticker, folder = email_intake._resolve_and_file_email(
        "Notes mentioning ZBQ", "a@example.com", "", "body", _KNOWN, explicit, "ZPAX"
    )
    assert real_ticker == "ZPAX"
    assert "/ZPAX" in folder
    assert stub_dropbox_and_activity[0].startswith(folder)


def test_falls_back_to_subject_detection_when_address_unresolved(stub_dropbox_and_activity):
    unresolved = {"kind": "not_a_ticker"}
    filename, real_ticker, folder = email_intake._resolve_and_file_email(
        "Quick notes on ZBQ ahead of earnings", "a@example.com", "", "body", _KNOWN, unresolved, "NOTES"
    )
    assert real_ticker == "ZBQ"
    assert "/ZBQ" in folder


def test_falls_back_to_needs_review_when_nothing_resolves(stub_dropbox_and_activity):
    unresolved = {"kind": "not_a_ticker"}
    filename, real_ticker, folder = email_intake._resolve_and_file_email(
        "Just some general market notes", "a@example.com", "", "body", _KNOWN, unresolved, "NOTES"
    )
    assert real_ticker is None
    assert folder == email_intake.settings.dropbox_needs_review_path
    assert filename.startswith("[NOTES]")


def test_needs_review_filename_uses_ticker_unclear_label_with_no_tag(stub_dropbox_and_activity):
    # Nested .eml attachments pass unresolved_tag=None — there's no typed
    # address tag for an individual attachment inside a batch.
    unresolved = {"kind": "not_a_ticker"}
    filename, real_ticker, folder = email_intake._resolve_and_file_email(
        "Just some general market notes", "a@example.com", "", "body", _KNOWN, None, None
    )
    assert real_ticker is None
    assert filename.startswith("[ticker unclear]")
