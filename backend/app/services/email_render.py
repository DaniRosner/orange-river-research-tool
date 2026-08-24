"""
Renders a forwarded email (subject/sender/date/body) to a plain, readable
PDF — used by app/routers/email_intake.py so a forwarded email lands in
Dropbox as something the user can open and skim, not raw .eml source.

Deliberately NOT built on pdf_render.render_markdown_to_pdf(): that
renderer's recommendation-box and citation-footnote detection is tuned
for AI-drafted investment memos and would misfire on arbitrary email
text (e.g. a body that happens to contain a bracketed number, or a line
starting with "Recommendation:"). This is a much simpler, single-pass
document — a header block plus the body as plain paragraphs, using
reportlab's SimpleDocTemplate directly rather than pdf_render's
two-pass/footnote-aware BaseDocTemplate machinery.
"""

import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_NAVY = colors.HexColor("#1a3a5c")
_GRAY = colors.HexColor("#666666")
_INK = colors.HexColor("#1a1a1a")

_STYLE_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=_NAVY, spaceAfter=10)
_STYLE_META = ParagraphStyle("meta", fontName="Helvetica", fontSize=9.5, leading=13, textColor=_GRAY, spaceAfter=3)
_STYLE_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=8, textColor=_INK)


def _escape(text: str) -> str:
    """Escape the handful of characters reportlab's Paragraph markup
    treats specially — this is plain email text, not markdown, so no
    bold/italic conversion is attempted here, just making arbitrary
    forwarded content safe to hand to Paragraph at all."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_email_to_pdf(subject: str, sender: str, date_str: str, body_text: str) -> bytes:
    """Build a simple PDF: a title (the subject), a small metadata block
    (from/date), then the body as one paragraph per blank-line-separated
    chunk of plain text. `body_text` is the email's plain-text content —
    callers are responsible for stripping HTML down to text first, since
    that's a lossy, format-specific step this function shouldn't own."""
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
    )

    story = [
        Paragraph(_escape(subject or "(no subject)"), _STYLE_TITLE),
        Paragraph(f"From: {_escape(sender or 'unknown sender')}", _STYLE_META),
        Paragraph(f"Date: {_escape(date_str or 'unknown date')}", _STYLE_META),
        Spacer(1, 12),
    ]

    # Blank-line-separated paragraphs, same convention as an ordinary
    # plain-text email — a lone newline inside one paragraph is treated
    # as a soft wrap (rendered as <br/>), not a new paragraph.
    paragraphs = re.split(r"\n\s*\n", (body_text or "").strip())
    for para in paragraphs:
        if not para.strip():
            continue
        html = _escape(para.strip()).replace("\n", "<br/>")
        story.append(Paragraph(html, _STYLE_BODY))

    if len(story) == 4:  # nothing but the header block — no real body text
        story.append(Paragraph("(no readable body text)", _STYLE_BODY))

    doc.build(story)
    return buffer.getvalue()
