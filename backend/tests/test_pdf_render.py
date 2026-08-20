import pypdfium2 as pdfium

from app.services import pdf_render


def _extract_text(pdf_bytes: bytes) -> str:
    pdf = pdfium.PdfDocument(pdf_bytes)
    return "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))


def test_render_markdown_to_pdf_produces_a_real_pdf():
    pdf_bytes = pdf_render.render_markdown_to_pdf("# Title\n\nSome body text.")
    assert pdf_bytes.startswith(b"%PDF")


def test_render_markdown_to_pdf_preserves_headings_and_body_text():
    text = _extract_text(pdf_render.render_markdown_to_pdf("# ZBQ Memo\n\nRevenue grew 12%."))
    assert "ZBQ Memo" in text
    assert "Revenue grew 12%" in text


def test_render_markdown_to_pdf_preserves_list_items():
    markdown = "# Risks\n\n- First risk\n- Second risk\n"
    text = _extract_text(pdf_render.render_markdown_to_pdf(markdown))
    assert "First risk" in text
    assert "Second risk" in text


def test_render_markdown_to_pdf_preserves_table_content():
    markdown = "| Metric | Value |\n|---|---|\n| Revenue | $7.2B |\n"
    text = _extract_text(pdf_render.render_markdown_to_pdf(markdown))
    assert "Revenue" in text
    assert "$7.2B" in text


def test_render_markdown_to_pdf_handles_empty_content():
    pdf_bytes = pdf_render.render_markdown_to_pdf("")
    assert pdf_bytes.startswith(b"%PDF")
