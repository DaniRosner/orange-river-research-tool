"""
Real preview thumbnails for files that Dropbox's own thumbnail API doesn't
cover natively — see dropbox_client.get_image_thumbnail() for the ones it
does (actual image files only).

PDF, Word, and PowerPoint-family documents get a real thumbnail here by
rendering the first page of Dropbox's own rendered preview (a PDF — see
dropbox_client.get_preview()) down to a small JPEG. An actual .pdf file
skips get_preview() entirely and is rendered directly — there's nothing to
convert.

Excel/CSV files are deliberately NOT handled here at all: Dropbox's own
preview for those is raw HTML, not a PDF or an image, and turning that
into a visual thumbnail would need a headless browser screenshotting it —
real infrastructure that isn't worth building for an app this size. Those
(and anything else unhandled) simply return None, and the frontend shows
a plain file-type icon instead of treating that as an error.
"""

import atexit
import concurrent.futures
import io

import pypdfium2 as pdfium

from app.services import dropbox_client

# Extensions whose Dropbox preview is rendered as a PDF (per
# files/get_preview's documented behavior) — first page of that PDF is
# what gets thumbnailed. A real .pdf file is handled separately (see
# get_thumbnail() below) since it doesn't need get_preview() at all.
_PDF_PREVIEWABLE_EXTENSIONS = {
    ".doc",
    ".docm",
    ".docx",
    ".ppt",
    ".pptm",
    ".pptx",
    ".rtf",
    ".odt",
    ".odp",
    ".pps",
    ".ppsm",
    ".ppsx",
}

# Dropbox's own image-thumbnail API's supported formats (see
# dropbox_client.get_image_thumbnail()) — gated here too so a file type we
# already know it can't handle (e.g. .xlsx) never costs a wasted API call.
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".gif", ".webp", ".ppm", ".bmp"}

THUMBNAIL_WIDTH_PX = 200


def _extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def _render_pdf_first_page(pdf_bytes: bytes) -> bytes:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page = pdf[0]
        scale = THUMBNAIL_WIDTH_PX / page.get_size()[0]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=78)
        return buf.getvalue()
    finally:
        pdf.close()


# PDFium's native code can SIGABRT the whole process on certain PDFs — a
# heap-corruption bug in its macOS font-mapper (CFX_FolderFontInfo) when a
# page needs a substitute font. That's a hard crash, not a Python
# exception, so no try/except in this process can catch it. Rendering
# always happens in a throwaway subprocess instead: if PDFium aborts,
# only that worker dies and get_thumbnail() below sees it as a normal
# failure, instead of the crash taking down the whole API server.
_pdf_render_pool: concurrent.futures.ProcessPoolExecutor | None = None


def _get_pdf_render_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _pdf_render_pool
    if _pdf_render_pool is None:
        _pdf_render_pool = concurrent.futures.ProcessPoolExecutor(max_workers=2)
        atexit.register(_pdf_render_pool.shutdown, wait=False, cancel_futures=True)
    return _pdf_render_pool


def _render_pdf_first_page_isolated(pdf_bytes: bytes) -> bytes:
    global _pdf_render_pool
    pool = _get_pdf_render_pool()
    try:
        return pool.submit(_render_pdf_first_page, pdf_bytes).result(timeout=20)
    except (concurrent.futures.process.BrokenProcessPool, concurrent.futures.TimeoutError):
        # A crashed or hung worker leaves the pool unusable — rebuild it
        # fresh for the next call rather than let every future request
        # fail forever.
        pool.shutdown(wait=False, cancel_futures=True)
        _pdf_render_pool = None
        raise


def get_thumbnail(path: str, filename: str) -> bytes | None:
    """
    Best-effort JPEG thumbnail for the file at `path` (already known to
    exist there), or None if this file's type isn't one a real preview can
    be generated for — callers should treat None as "show the generic
    file-type icon instead," not as an error. Never raises: any failure
    talking to Dropbox or rendering the page is treated the same as
    "no thumbnail available."
    """
    ext = _extension_of(filename)

    try:
        if ext == ".pdf":
            return _render_pdf_first_page_isolated(dropbox_client.download(path))

        if ext in _PDF_PREVIEWABLE_EXTENSIONS:
            return _render_pdf_first_page_isolated(dropbox_client.get_preview(path))
    except Exception:
        return None

    if ext in _IMAGE_EXTENSIONS:
        return dropbox_client.get_image_thumbnail(path)

    return None
