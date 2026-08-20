"""
Extracts plain text from a file's raw bytes, keyed off its filename
extension — used by bridge.py's read_ticker_file so the AI can pull actual
content out of an existing Dropbox file (a prior quarter's memo, an
existing model) rather than only ever seeing files it just sent itself.
Deliberately narrow: only formats we can reliably turn into clean text
(PDF, plain text, markdown) are supported — anything else (Excel, Word,
PowerPoint, images) returns None rather than guessing at a lossy
conversion, and the caller falls back to handing over a Dropbox link
instead.
"""

from io import BytesIO

import pypdfium2 as pdfium

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv"}


def _extension_of(filename: str) -> str:
    return f".{filename.rsplit('.', 1)[1].lower()}" if "." in filename else ""


def _extract_pdf_text(data: bytes) -> str:
    doc = pdfium.PdfDocument(BytesIO(data))
    pages = [page.get_textpage().get_text_range() for page in doc]
    return "\n\n".join(pages)


def extract_text(filename: str, data: bytes) -> str | None:
    """Returns extracted text, or None if `filename`'s extension isn't a
    supported format — never raises for an unsupported type, since that's
    an expected, normal outcome the caller handles gracefully."""
    ext = _extension_of(filename)
    if ext == ".pdf":
        return _extract_pdf_text(data)
    if ext in _TEXT_EXTENSIONS:
        return data.decode("utf-8", errors="replace")
    return None
