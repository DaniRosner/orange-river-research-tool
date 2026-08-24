from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.services.eml_parse import parse_eml


def _build_eml(subject: str, sender: str, date: str, body: str) -> bytes:
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = date
    msg.attach(MIMEText(body, "plain"))
    return msg.as_bytes()


def test_parse_eml_extracts_subject_sender_date_body():
    data = _build_eml(
        subject="Q2 update on ZPAX",
        sender="Analyst <analyst@example.com>",
        date="Mon, 24 Aug 2026 13:26:46 -0400",
        body="Reviewed the quarter. Margins held up.",
    )
    parsed = parse_eml(data)
    assert parsed["subject"] == "Q2 update on ZPAX"
    assert parsed["sender"] == "Analyst <analyst@example.com>"
    assert parsed["date_str"] == "Mon, 24 Aug 2026 13:26:46 -0400"
    assert parsed["body_text"] == "Reviewed the quarter. Margins held up."


def test_parse_eml_handles_missing_headers_gracefully():
    msg = MIMEText("just a body, no real headers set")
    parsed = parse_eml(msg.as_bytes())
    assert parsed["subject"] == ""
    assert parsed["sender"] == ""
    assert parsed["body_text"] == "just a body, no real headers set"


def test_parse_eml_decodes_encoded_word_subject():
    data = _build_eml(
        subject="=?utf-8?b?UsOpc3Vtw6k=?=",  # "Résumé" MIME-encoded
        sender="a@example.com",
        date="Mon, 24 Aug 2026 13:26:46 -0400",
        body="body",
    )
    parsed = parse_eml(data)
    assert parsed["subject"] == "Résumé"
