"""
Renders a markdown memo to a formatted PDF, server-side — no AI/model
involvement, so none of the reliability issues that hit send_report/
save_final's base64 path apply here. Used by save_final (see
app/routers/bridge.py) so a saved memo lands in Dropbox as something
the user can actually read, not raw markdown text.

Built directly on reportlab's platypus API (not xhtml2pdf/HTML) so real
per-page footnotes are possible: citation markers like [3] in the body
get a matching footnote drawn at the bottom of whichever page they
actually land on, which requires knowing pagination in advance —
something no single-pass HTML-to-PDF conversion can give you. This
renders in two passes: pass 1 lays out the real content to learn which
page each block of text lands on (using the exact same frame geometry
both times, so this prediction is exact, not an estimate); pass 2 does
the real render, now knowing which footnotes belong on which page.
"""

import re
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

_NAVY = colors.HexColor("#1a3a5c")
_REC_LABEL_BG = colors.HexColor("#2f5233")
_REC_VALUE_BG = colors.HexColor("#eef2ea")
_GRAY = colors.HexColor("#666666")
_RULE_GRAY = colors.HexColor("#cccccc")
_ROW_SHADE = colors.HexColor("#f4f6f8")
_TABLE_BORDER = colors.HexColor("#999999")
_INK = colors.HexColor("#1a1a1a")

PAGE_W, PAGE_H = letter
_MARGIN_X = 0.85 * inch
_BODY_TOP = PAGE_H - 1.05 * inch
_FOOTNOTE_HEIGHT = 1.3 * inch
_FOOTER_HEIGHT = 0.35 * inch
_BODY_BOTTOM = _FOOTNOTE_HEIGHT + _FOOTER_HEIGHT + 0.15 * inch
_BODY_HEIGHT = _BODY_TOP - _BODY_BOTTOM
_BODY_WIDTH = PAGE_W - 2 * _MARGIN_X

_STYLE_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=8, textColor=_INK)
_STYLE_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=_NAVY)
_STYLE_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=_NAVY)
_STYLE_H3 = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11.5, leading=15, textColor=_NAVY, spaceBefore=12, spaceAfter=4)
_STYLE_BULLET = ParagraphStyle("bullet", parent=_STYLE_BODY, leftIndent=14, spaceAfter=3)
_STYLE_TABLE_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=9.5, leading=12, textColor=_INK)
_STYLE_TABLE_HEAD = ParagraphStyle("cellhead", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=colors.white)
_STYLE_REC_LABEL = ParagraphStyle("reclabel", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=colors.white)
_STYLE_REC_VALUE = ParagraphStyle("recvalue", fontName="Helvetica", fontSize=9.5, leading=13, textColor=_INK)
_STYLE_FOOTNOTE = ParagraphStyle("fn", fontName="Helvetica", fontSize=7.8, leading=10.5, textColor=_GRAY)


def _inline(text: str) -> str:
    """Converts a subset of markdown inline syntax (bold/italic) into
    reportlab's own mini-markup, after escaping the text for XML safety —
    reportlab's Paragraph markup is XML-like, so raw &/</> in the source
    text has to be escaped before any of our own tags are inserted."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if text.count("**") % 2 == 1:
        # An odd number of "**" markers means one is unpaired — in every
        # real case seen so far it's a stray trailing closer the AI left
        # in by mistake (e.g. a line ending "...conservatively.**" with no
        # opening "**" anywhere earlier in it), which would otherwise
        # print as literal asterisks instead of turning anything bold.
        # Dropping the last occurrence is a defensive backstop, not a
        # substitute for the AI proofreading its own markdown — it just
        # means a missed proofread doesn't leak into the final PDF.
        idx = text.rfind("**")
        text = text[:idx] + text[idx + 2 :]
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    return text


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_BULLET_RE = re.compile(r"^[•\-]\s+(.*)$")
_REC_LINE_RE = re.compile(r"^\**Recommendation\**\s*:\**\s*", re.IGNORECASE | re.MULTILINE)
_SOURCE_RE = re.compile(r"^\**(?:Sources|Source references)\**\s*:", re.IGNORECASE)
_SOURCE_HEADING_RE = re.compile(
    r"^\**(?:sources?|source references|source notes|appendix:\s*source notes)\**\s*:?\s*$",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"\[(\d+)\]")
_LEAKED_TOOL_CITATION_RE = re.compile(r"\s*cite(?:turn\d+(?:search|news)\d+)+")


def _strip_leaked_tool_citations(markdown_text: str) -> str:
    """ChatGPT's web-search tool sometimes leaks its own internal citation
    tokens straight into the visible text it hands back — e.g. a Sources
    line ending in "...Form 10-K citeturn0search51turn1search2". That's
    ChatGPT's own browsing-citation bookkeeping, never meant to be read by
    a person, and it has nothing to do with our [N]/Sources footnote
    system — left in, it shows up as ugly trailing garbage in the actual
    per-page footnotes. Stripped here, before any other parsing, so it
    can't corrupt a citation definition or leave stray junk in body text
    either."""
    return _LEAKED_TOOL_CITATION_RE.sub("", markdown_text)


def _ensure_title_heading(markdown_text: str) -> str:
    """If the document doesn't open with a markdown heading, the AI wrote
    the title as a plain line — promote it to an H1 so it actually gets
    the big bold title treatment, instead of blending into the body as
    undifferentiated paragraph text."""
    lines = markdown_text.lstrip("\n").split("\n", 1)
    first_line = lines[0].strip()
    if not first_line or first_line.startswith("#"):
        return markdown_text
    rest = lines[1] if len(lines) > 1 else ""
    return f"# {first_line}\n{rest}"


def _parse_blocks(markdown_text: str) -> list[tuple[str, object]]:
    """A pragmatic block-level markdown parser covering what these memos
    actually use: #/##/### headings, paragraphs (blank-line separated,
    internal line breaks preserved), bullet lists (-/•), and pipe
    tables. Not a full CommonMark implementation — just enough to drive
    reportlab flowables directly, which is what makes real per-page
    footnotes possible (see module docstring)."""
    lines = markdown_text.split("\n")
    blocks: list[tuple[str, object]] = []
    para_buf: list[str] = []
    i, n = 0, len(lines)

    def flush_para():
        if para_buf:
            text = "\n".join(line.strip() for line in para_buf if line.strip())
            if text:
                blocks.append(("p", text))
            para_buf.clear()

    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_para()
            blocks.append((f"h{len(heading.group(1))}", heading.group(2).strip()))
            i += 1
            continue
        if _TABLE_ROW_RE.match(stripped) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            flush_para()
            rows = [[c.strip() for c in stripped.strip("|").split("|")]]
            i += 2
            while i < n and _TABLE_ROW_RE.match(lines[i].strip()):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(("table", rows))
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            flush_para()
            items = [bullet.group(1).strip()]
            i += 1
            while i < n:
                nxt = _BULLET_RE.match(lines[i].strip())
                if not nxt:
                    break
                items.append(nxt.group(1).strip())
                i += 1
            blocks.append(("ul", items))
            continue
        para_buf.append(lines[i])
        i += 1
    flush_para()
    return blocks


def _extract_citation_defs(text: str) -> dict[str, str]:
    """Pulls `[N] description` pairs out of a block of source-definition
    text — this is where citation numbers get their definitions, even
    though the text itself is never shown in the body anymore (it's
    replaced by real per-page footnotes). Each entry runs up to the next
    `[N]` marker or the end of the text, not up to a semicolon — some AIs
    write one source per line (newline-separated, no semicolons at all),
    which a semicolon-bounded match would silently merge into one giant
    definition for citation 1."""
    text = re.sub(r"\s*\n\s*", " ", text)
    text = _SOURCE_RE.sub("", text).strip()
    text = re.sub(r"\.\s*Full source list on final page\.?\s*$", "", text, flags=re.IGNORECASE)
    defs = {}
    for match in re.finditer(r"\[(\d+)\]\s*(.*?)(?=\s*\[\d+\]|$)", text):
        definition = match.group(2).strip().rstrip(".").rstrip(";").strip()
        if definition:
            defs[match.group(1)] = definition
    return defs


def _extract_citation_defs_from_table(rows: list[list[str]]) -> dict[str, str]:
    """A 'Ref | Source | Items used' appendix table also defines
    citations — usually more completely than the inline Sources lines,
    so this is applied after them and naturally wins on overlap."""
    if len(rows) < 2 or "ref" not in rows[0][0].lower():
        return {}
    defs = {}
    for row in rows[1:]:
        match = re.search(r"\[?(\d+)\]?", row[0])
        if not match:
            continue
        defs[match.group(1)] = " — ".join(cell for cell in row[1:] if cell)
    return defs


def _flatten_text(kind: str, content: object) -> str:
    if kind == "table":
        return " ".join(cell for row in content for cell in row)
    if kind == "ul":
        return " ".join(content)
    return str(content)


def _ruled_heading(text: str, style: ParagraphStyle, rule_color, rule_width: float) -> Table:
    table = Table([[Paragraph(_inline(text), style)]], colWidths=[_BODY_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), rule_width, rule_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _build_table(rows: list[list[str]]) -> Table:
    header = [Paragraph(_inline(c), _STYLE_TABLE_HEAD) for c in rows[0]]
    body = [[Paragraph(_inline(c), _STYLE_TABLE_CELL) for c in row] for row in rows[1:]]
    data = [header] + body
    ncols = len(rows[0])
    col_width = _BODY_WIDTH / ncols
    table = Table(data, colWidths=[col_width] * ncols, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, _TABLE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_idx in range(1, len(data), 2):
        style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), _ROW_SHADE))
    table.setStyle(TableStyle(style))
    return table


def _build_rec_box(value_text: str) -> Table:
    value = re.sub(r"\s*\n\s*", " ", value_text).strip()
    label = Paragraph("Recommendation", _STYLE_REC_LABEL)
    value_p = Paragraph(_inline(value), _STYLE_REC_VALUE)
    table = Table([[label, value_p]], colWidths=[110, _BODY_WIDTH - 110])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), _REC_LABEL_BG),
                ("BACKGROUND", (1, 0), (1, -1), _REC_VALUE_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _flowables_for_block(kind: str, content: object) -> list:
    if kind == "h1":
        return [_ruled_heading(content, _STYLE_H1, _NAVY, 1.5)]
    if kind == "h2":
        return [_ruled_heading(content, _STYLE_H2, _RULE_GRAY, 0.5)]
    if kind == "h3":
        return [Paragraph(_inline(content), _STYLE_H3)]
    if kind == "p":
        text = "<br/>".join(_inline(line) for line in content.split("\n"))
        return [Paragraph(text, _STYLE_BODY)]
    if kind == "ul":
        return [Paragraph("•&nbsp;&nbsp;" + _inline(item), _STYLE_BULLET) for item in content]
    if kind == "table":
        return [_build_table(content)]
    if kind == "rec":
        return [_build_rec_box(content)]
    return []


def _classify_blocks(
    raw_blocks: list[tuple[str, object]],
) -> tuple[list[tuple[str, object]], dict[str, str], set[int]]:
    """Splits parsed blocks into what actually renders in the body vs.
    what only defines citations — a 'Recommendation' paragraph becomes
    a real callout block, and 'Sources'/'Source references' paragraphs
    are consumed as citation definitions rather than shown as body text
    at all, since they're now real page-bottom footnotes instead.

    An appendix Ref/Source table, if present, still gets both: it stays
    in the body (the user sees the full source list as a real table) AND
    feeds citation_defs — but its own index is recorded in
    ref_table_idxs so the caller can skip it when scanning for which
    page each citation number was actually *used* on. Without that
    exclusion, a ref table's own "[1]" cell reads as a use of citation 1
    on whatever page the appendix lands on, which reprints the entire
    footnote list a second time right where the source table already is.

    A bare "Sources"/"Source Notes"/etc. heading (no colon, no inline
    prefix — just its own heading line, e.g. "## Sources") starts a
    source section: every block after it, of any kind, is consumed for
    citation definitions and dropped from the body entirely, the same as
    a "Sources:"-prefixed paragraph. Some AIs write the sources list this
    way (a heading followed by one definition per line) rather than a
    single "Sources:" paragraph or a Ref/Source table — without this,
    those definitions were silently never extracted at all, which meant
    zero footnotes anywhere in the memo despite the body being full of
    [N] markers."""
    blocks: list[tuple[str, object]] = []
    citation_defs: dict[str, str] = {}
    ref_table_idxs: set[int] = set()
    in_source_section = False
    for kind, content in raw_blocks:
        if not in_source_section and kind in ("h1", "h2", "h3") and _SOURCE_HEADING_RE.match(content.strip()):
            in_source_section = True
            continue
        if in_source_section:
            if kind == "p":
                citation_defs.update(_extract_citation_defs(content))
            elif kind == "table":
                citation_defs.update(_extract_citation_defs_from_table(content))
            elif kind == "ul":
                citation_defs.update(_extract_citation_defs(" ".join(content)))
            continue
        if kind == "p":
            rec_split = _REC_LINE_RE.search(content)
            if rec_split:
                # "Recommendation:" can be its own whole paragraph, or one
                # line inside a bigger header-block paragraph (e.g. "Date:
                # ...\nPrepared by: ...\nRecommendation: ..." with no blank
                # line separating it) — split at the match either way, so
                # the recommendation always becomes its own colored
                # callout box, not plain text buried in a header block.
                # The value itself only runs to the end of THAT line, not
                # the rest of the paragraph — a recommendation is normally
                # continuous prose with no embedded line break, so a line
                # break right after it means a new field (e.g. "Investment
                # horizon: ...") started, not a continuation of the value.
                before = content[: rec_split.start()].rstrip()
                after = content[rec_split.end() :]
                newline_pos = after.find("\n")
                if newline_pos == -1:
                    rec_value, trailing = after.strip(), ""
                else:
                    rec_value, trailing = after[:newline_pos].strip(), after[newline_pos:].strip()
                if before:
                    blocks.append(("p", before))
                if rec_value:
                    blocks.append(("rec", rec_value))
                if trailing:
                    blocks.append(("p", trailing))
                continue
            if _SOURCE_RE.match(content):
                citation_defs.update(_extract_citation_defs(content))
                continue
        if kind == "table":
            table_defs = _extract_citation_defs_from_table(content)
            if table_defs:
                citation_defs.update(table_defs)
                ref_table_idxs.add(len(blocks))
        blocks.append((kind, content))
    return blocks, citation_defs, ref_table_idxs


def _build_story(blocks: list[tuple[str, object]], tag: bool) -> list:
    """A heading is never allowed to be the last thing on a page (bare, or
    with a single line under it before everything else spills over) — it's
    grouped with the block right after it into one KeepTogether unit, so
    the pair moves to the next page together if it can't both fit. Any
    consecutive run of headings (e.g. an h1 immediately followed by an h2)
    is absorbed into the same group along with the one real content block
    that follows them. If that content block is itself too long to ever
    fit on a single page (a big table), reportlab still splits it rather
    than forcing an empty gap — KeepTogether only pins things together
    when there's room to."""
    story = []
    n = len(blocks)
    i = 0
    while i < n:
        kind, content = blocks[i]
        if kind not in ("h1", "h2", "h3"):
            flowables = _flowables_for_block(kind, content)
            if tag:
                for flowable in flowables:
                    flowable.block_idx = i
            story.extend(flowables)
            if kind in ("table", "rec"):
                story.append(Spacer(1, 6))
            i += 1
            continue

        group_flowables = []
        j = i
        while j < n and blocks[j][0] in ("h1", "h2", "h3"):
            fls = _flowables_for_block(*blocks[j])
            if tag:
                for f in fls:
                    f.block_idx = j
            group_flowables.extend(fls)
            j += 1
        if j < n:
            next_kind, next_content = blocks[j]
            fls = _flowables_for_block(next_kind, next_content)
            if tag:
                for f in fls:
                    f.block_idx = j
            group_flowables.extend(fls)
            if next_kind in ("table", "rec"):
                group_flowables.append(Spacer(1, 6))
            j += 1

        story.append(KeepTogether(group_flowables))
        i = j
    return story or [Spacer(1, 1)]


class _MemoDoc(BaseDocTemplate):
    def __init__(self, buf, on_page):
        frame = Frame(
            _MARGIN_X, _BODY_BOTTOM, _BODY_WIDTH, _BODY_HEIGHT,
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="body",
        )
        template = PageTemplate(id="main", frames=[frame], onPage=on_page)
        super().__init__(
            buf, pagesize=letter, pageTemplates=[template],
            topMargin=0, bottomMargin=0, leftMargin=_MARGIN_X, rightMargin=_MARGIN_X,
        )
        self.block_pages: dict[int, int] = {}

    def afterFlowable(self, flowable):
        idx = getattr(flowable, "block_idx", None)
        if idx is not None:
            self.block_pages[idx] = self.page


def _draw_header_footer(canvas, title: str, date_str: str, page: int) -> None:
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_GRAY)
    canvas.drawString(_MARGIN_X, PAGE_H - 0.55 * inch, title)
    canvas.drawRightString(PAGE_W - _MARGIN_X, PAGE_H - 0.55 * inch, date_str)
    canvas.setStrokeColor(_RULE_GRAY)
    canvas.setLineWidth(0.5)
    canvas.line(_MARGIN_X, PAGE_H - 0.62 * inch, PAGE_W - _MARGIN_X, PAGE_H - 0.62 * inch)
    canvas.drawString(_MARGIN_X, 0.4 * inch, "Confidential — Your Firm Research")
    canvas.drawRightString(PAGE_W - _MARGIN_X, 0.4 * inch, f"Page {page}")


def _make_on_page(title: str, date_str: str, page_footnotes: dict[int, list[tuple[str, str]]]):
    def on_page(canvas, doc):
        canvas.saveState()
        _draw_header_footer(canvas, title, date_str, doc.page)
        notes = page_footnotes.get(doc.page, [])
        if notes:
            y = _BODY_BOTTOM - 0.12 * inch
            canvas.setStrokeColor(_RULE_GRAY)
            canvas.setLineWidth(0.5)
            canvas.line(_MARGIN_X, y, _MARGIN_X + 1.6 * inch, y)
            y -= 0.18 * inch
            min_y = _FOOTER_HEIGHT + 0.15 * inch
            for num, definition in notes:
                para = Paragraph(f"[{num}] {_inline(definition)}", _STYLE_FOOTNOTE)
                _, h = para.wrap(_BODY_WIDTH, _FOOTNOTE_HEIGHT)
                if y - h < min_y:
                    break
                y -= h
                para.drawOn(canvas, _MARGIN_X, y)
                y -= 2
        canvas.restoreState()

    return on_page


def render_markdown_to_pdf(markdown_text: str, ticker: str | None = None) -> bytes:
    """Converts markdown text to a formatted PDF — navy headings, styled
    tables, a two-column recommendation callout, a running header/footer,
    and real per-page footnotes: any [N] citation in the body gets its
    definition (pulled from 'Sources:'/'Source references:' lines or an
    appendix Ref/Source table) drawn at the bottom of whichever page it
    actually appears on. Raises ValueError with a short message on
    failure — callers should surface that as a normal tool error, not
    let it propagate as a raw exception."""
    try:
        markdown_text = _strip_leaked_tool_citations(markdown_text)
        markdown_text = _ensure_title_heading(markdown_text)
        raw_blocks = _parse_blocks(markdown_text)
        blocks, citation_defs, ref_table_idxs = _classify_blocks(raw_blocks)

        block_citations: dict[int, list[str]] = {}
        for idx, (kind, content) in enumerate(blocks):
            if idx in ref_table_idxs:
                continue
            nums = sorted(set(_CITATION_RE.findall(_flatten_text(kind, content))), key=int)
            if nums:
                block_citations[idx] = nums

        title = f"{ticker} Investment Memo" if ticker else "Investment Memo"
        date_str = date.today().strftime("%B %-d, %Y")

        pass1 = _MemoDoc(BytesIO(), on_page=lambda c, d: None)
        pass1.build(_build_story(blocks, tag=True))

        page_footnotes: dict[int, list[tuple[str, str]]] = {}
        for idx, nums in block_citations.items():
            page = pass1.block_pages.get(idx)
            if page is None:
                continue
            entries = page_footnotes.setdefault(page, [])
            for num in nums:
                definition = citation_defs.get(num)
                if definition and (num, definition) not in entries:
                    entries.append((num, definition))
        for entries in page_footnotes.values():
            entries.sort(key=lambda pair: int(pair[0]))

        output = BytesIO()
        doc2 = _MemoDoc(output, on_page=_make_on_page(title, date_str, page_footnotes))
        doc2.build(_build_story(blocks, tag=False))
        return output.getvalue()
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - convert any reportlab/layout failure to a normal tool error
        raise ValueError(f"PDF rendering failed ({error}) — check the markdown for malformed content.") from error
