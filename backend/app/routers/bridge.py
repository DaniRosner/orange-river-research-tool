"""
MCP "bridge" letting ChatGPT and Claude hand a research report back and
forth on the user's behalf, and save a final version into a ticker's real
Dropbox folder. See the approved plan (project memory /
docs/sorting-behavior-notes.md context) for the full design rationale —
short version:

- Two separate MCPServer instances, one per AI identity, each mounted (in
  app/main.py) at its own secret URL path — the server infers *which AI is
  calling* purely from which URL was hit, so tool calls never need an
  explicit "from/to" argument the AI could get wrong. This also doubles as
  the auth boundary, since ChatGPT's custom-connector UI has no plain
  bearer-token option (only OAuth/No Auth/Mixed) — a header-based secret
  would be invisible from ChatGPT's side, so the secret lives in the URL
  instead, which works identically for both AIs.
- The MCP Python SDK serves its endpoint at /mcp by default (not /),
  requires its session manager's lifespan to be entered manually when
  mounted inside FastAPI (see app/main.py), and has DNS-rebinding
  protection that rejects any non-localhost Host header unless explicitly
  disabled — all three were real bugs discovered validating this approach
  before writing this file, designed around here from the start.
"""

import base64
import binascii

from fastapi import APIRouter, Form, UploadFile
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.services import (
    activity_log,
    bridge_store,
    document_text,
    dropbox_client,
    pdf_render,
    research_prompts,
    ticker_registry,
)

# "_test" identities are a fully separate pair (their own secret URLs, own
# send/receive queue via bridge_store, own Dropbox destination — see
# _save_bytes_to_dropbox) so Dani can keep testing indefinitely without
# ever mixing with or landing in the user's real chatgpt/claude queue or
# folders. Same tools, same behavior, just isolated data end to end.
_OTHER = {
    "chatgpt": "claude",
    "claude": "chatgpt",
    "chatgpt_test": "claude_test",
    "claude_test": "chatgpt_test",
}
_DISPLAY_NAME = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "chatgpt_test": "ChatGPT (Test)",
    "claude_test": "Claude (Test)",
}
_TEST_IDENTITIES = {"chatgpt_test", "claude_test"}

_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _addressing(identity: str, third_person: str, direct: str) -> str:
    """For text that gets shown to the person actually chatting, not just
    read by the AI as internal guidance (tool docstrings are the latter —
    they're never relayed verbatim, so third-person "the user" phrasing
    reads fine there regardless of identity). A returned error message IS
    relayed close to verbatim though, and on the real chatgpt/claude
    identities the person chatting IS the user — "ask the user to confirm"
    said back to the user himself reads as bizarre. Test identities are
    Dani testing on the user's behalf, where third-person phrasing is
    correct, so that's the default `third_person` covers."""
    return third_person if identity in _TEST_IDENTITIES else direct

# the user's standing content requirements for investment memos (confirmed
# directly with him — apply these by default in every memo unless he says
# otherwise). This text is duplicated into get_pending's and save_final's
# tool docstrings below, not just the server-level `instructions` string —
# real-world behavior showed a memo saved without either an AI having them
# repeated in-chat, which means the top-level `instructions` field (unlike
# per-tool docstrings) isn't reliably read by these clients. Per-tool
# docstrings are the one place this has actually been observed to work, so
# it lives there twice: once on get_pending (read early — it's the tool the
# top-level instructions already say to call at the start of any ticker
# discussion) and once on save_final (read right before anything is saved),
# hedging against whichever moment a given client actually loads tool
# schemas into context.
_CONTENT_REQUIREMENTS = (
    "the user's standing content requirements for investment memos "
    "(confirmed directly with him, apply these by default in every memo "
    "unless he says otherwise):\n"
    "1. When describing what the company does / its business segments, "
    "break out profitability by division/segment, not just revenue — and "
    "include an estimated value per division (a sum-of-the-parts style "
    "analysis), not just a company-wide valuation.\n"
    "2. When covering leadership/governance/insider ownership, clearly "
    "distinguish how much of each insider's stake came from free stock "
    "(grants/RSUs) versus actual open-market purchases — don't just report "
    "a total ownership number.\n"
    "3. Cite specific factual claims inline with bracketed reference "
    "numbers (e.g. 'revenue grew 93% YoY [3]'), and include a numbered "
    "'Sources:' or 'Source references:' list (or a Ref/Source appendix "
    "table) defining what each number is (e.g. '[3] Q2 2026 earnings "
    "release (SEC 8-K)'). This isn't just style — save_final's PDF "
    "renderer turns these markers into real per-page footnotes "
    "automatically, but only if they're there; a memo written without "
    "them gets no visible sourcing at all, no matter how well-researched "
    "it actually is."
)


def _bridge_user(identity: str) -> dict:
    """A synthetic activity_log actor for a bridge-driven save, distinct
    from any real signed-in team member (see activity_log.record())."""
    name = _DISPLAY_NAME[identity]
    return {"full_name": f"{name} (via Bridge)", "email": f"bridge-{identity}@yourfirm.local"}


def _decode_base64_or_none(content: str) -> tuple[bytes | None, str | None]:
    """Shared by send_report and save_final. Returns (decoded_bytes, None)
    on success or (None, error_message) on failure — decode errors are
    handed back as a normal tool error, not an exception, so the AI can
    relay it and retry rather than the call just failing opaquely."""
    try:
        return base64.b64decode(content, validate=True), None
    except (binascii.Error, ValueError):
        return None, "content isn't valid base64 — re-encode the raw file bytes."


def _normalize_ticker_label(ticker: str) -> str:
    """Best-effort canonicalize to the real known ticker's casing when it
    unambiguously matches one, without ever blocking — send_report/
    get_pending are just conversation labels, not Dropbox writes, and
    the user may be discussing a ticker that has no folder yet at all."""
    ticker = ticker.strip()
    if not ticker:
        return ticker
    resolution = ticker_registry.resolve_ticker(ticker, ticker_registry.get_known_tickers())
    return resolution["ticker"] if resolution["kind"] == "matched" else ticker


def _do_send_report(
    identity: str, other: str, ticker: str, content: str, note: str, encoding: str, filename: str | None
) -> dict:
    """Shared by the send_report tool and finish_upload — see either
    caller's docstring for the argument contract."""
    if encoding not in ("text", "base64"):
        return {"status": "error", "message": "encoding must be 'text' or 'base64'."}
    if encoding == "base64":
        if not filename:
            return {"status": "error", "message": "filename is required when encoding='base64'."}
        _, error = _decode_base64_or_none(content)
        if error:
            return {"status": "error", "message": error}

    label = _normalize_ticker_label(ticker)
    message_id = bridge_store.send(identity, other, label, content, note or None, encoding, filename)
    return {"status": "sent", "to": _DISPLAY_NAME[other], "ticker": label, "message_id": message_id}


def _resolve_ticker_for_save(
    identity: str, ticker: str, new_ticker_status: str | None, confirmed_new_ticker: bool = False
) -> tuple[str, str] | dict:
    """Shared by _do_save_final and the local-upload endpoint. Returns
    (real_ticker, target_status) on success, or an {"status": "error", ...}
    dict to return as-is on failure — same ticker-resolution rules
    save_final has always used, just factored out so a real file upload
    doesn't have to duplicate them.

    `confirmed_new_ticker=True` is the deliberate escape hatch for a
    ticker that fuzzy-matches an existing one (e.g. a real new "ZBP"
    landing close to an existing "ZBQ") but genuinely isn't a typo —
    without it, a `confirm_needed` result is a permanent dead end, since
    the fuzzy-match check runs identically on every retry of the same
    string. This flag exists specifically so a human confirmation (the user
    saying "no, that's not a typo, it's really new") can actually get
    threaded through, rather than requiring the folder to be created
    manually outside the bridge every time this happens.

    Test identities skip all of this and just use `ticker` as-is: the
    typo-guard/new-ticker-status dance below exists to protect the real
    Active/Inactive/Historicals structure, which test saves never touch
    (see _save_bytes_to_dropbox — they always land under
    dropbox_bridge_test_path regardless of target_status). Running it
    anyway made testing genuinely broken, not just noisy: any ticker that
    doesn't happen to already exist in the real registry — which is most
    of them, since test tickers are often made up — got asked to pick a
    real active/inactive/historicals bucket that would then be silently
    ignored, with no way to answer past it."""
    if identity in _TEST_IDENTITIES:
        real_ticker = ticker.strip().upper()
        if not real_ticker:
            return {"status": "error", "message": "ticker can't be empty."}
        return real_ticker, "active"

    known = ticker_registry.get_known_tickers()
    resolution = ticker_registry.resolve_ticker(ticker.strip(), known)

    if resolution["kind"] == "matched":
        return resolution["ticker"], resolution["status"]
    if resolution["kind"] == "new_ticker_needs_status":
        if new_ticker_status not in ticker_registry.STATUSES:
            return {
                "status": "error",
                "message": (
                    f"'{ticker}' looks like a new ticker, not one that already exists. "
                    + _addressing(
                        identity,
                        "Ask the user which bucket it belongs in",
                        "Which bucket does this belong in",
                    )
                    + " — active, inactive, or historicals — then try again with that as "
                    "new_ticker_status to create it."
                ),
            }
        return resolution["ticker"], new_ticker_status
    if resolution["kind"] == "confirm_needed":
        suggestions = ", ".join(resolution["suggestions"])
        if confirmed_new_ticker:
            if new_ticker_status not in ticker_registry.STATUSES:
                return {
                    "status": "error",
                    "message": (
                        f"'{ticker}' confirmed as a genuinely new ticker — now pass "
                        "new_ticker_status as active, inactive, or historicals to create it."
                    ),
                }
            return ticker.strip(), new_ticker_status
        return {
            "status": "error",
            "message": (
                f"'{ticker}' isn't a recognized ticker, but it's close to: {suggestions}. "
                + _addressing(
                    identity,
                    "Ask the user whether he meant one of those, or this is genuinely a new ticker",
                    "Did you mean one of those, or is this genuinely a new ticker",
                )
                + " — this tool won't guess. If the user confirms it's genuinely new (not a typo "
                "of any of those), call save_final again with confirmed_new_ticker=True and "
                "new_ticker_status set — never set confirmed_new_ticker=True without him "
                "having actually said so."
            ),
        }
    return {
        "status": "error",
        "message": f"'{ticker}' doesn't look like a real ticker. "
        + _addressing(identity, "Ask the user to confirm the exact ticker.", "Please confirm the exact ticker."),
    }


def _resolve_ticker_folder(identity: str, ticker: str) -> str | dict:
    """Shared by list_ticker_files and read_ticker_file — resolves
    `ticker` to the Dropbox folder it already lives in, read-only (no
    typo-guard/new-ticker-status dance, since there's nothing to create
    here). Test identities always look under dropbox_bridge_test_path,
    same as saves. Returns the folder path, or an {"status": "error", ...}
    dict to return as-is if the ticker doesn't resolve to an existing
    folder."""
    ticker_clean = ticker.strip()
    if not ticker_clean:
        return {"status": "error", "message": "ticker can't be empty."}
    if identity in _TEST_IDENTITIES:
        return f"{settings.dropbox_bridge_test_path}/{ticker_clean.upper()}"

    resolution = ticker_registry.resolve_ticker(ticker_clean, ticker_registry.get_known_tickers())
    if resolution["kind"] == "matched":
        return f"{ticker_registry.folder_path_for_status(resolution['status'])}/{resolution['ticker']}"
    if resolution["kind"] == "confirm_needed":
        suggestions = ", ".join(resolution["suggestions"])
        return {
            "status": "error",
            "message": f"'{ticker}' isn't a recognized ticker, but it's close to: {suggestions}. "
            + _addressing(identity, "Ask the user to confirm.", "Please confirm."),
        }
    return {"status": "error", "message": f"'{ticker}' doesn't have an existing folder yet."}


def _save_bytes_to_dropbox(
    identity: str, real_ticker: str, target_status: str, filename: str, file_bytes: bytes, overwrite: bool
) -> dict:
    """Shared final step: given fully-resolved bytes and a destination
    ticker/status, actually write to Dropbox and log it — used by both
    save_final (after decoding/rendering `content`) and the local-upload
    endpoint (which already has real bytes, no decoding needed). Test
    identities never touch the real Active/Inactive/Historicals folders —
    everything they save lands under settings.dropbox_bridge_test_path
    instead, regardless of target_status."""
    if identity in _TEST_IDENTITIES:
        folder = f"{settings.dropbox_bridge_test_path}/{real_ticker}"
    else:
        folder = f"{ticker_registry.folder_path_for_status(target_status)}/{real_ticker}"
    full_path = f"{folder}/{filename}"
    actual_filename = dropbox_client.upload_file(full_path, file_bytes, overwrite=overwrite)
    activity_log.record(_bridge_user(identity), "uploaded", ticker=real_ticker, filename=actual_filename)
    preview_url = dropbox_client.get_shareable_link(f"{folder}/{actual_filename}")
    return {"status": "saved", "ticker": real_ticker, "filename": actual_filename, "preview_url": preview_url}


def _do_save_final(
    identity: str,
    ticker: str,
    filename: str,
    content: str,
    encoding: str,
    new_ticker_status: str | None,
    overwrite: bool,
    confirmed_new_ticker: bool = False,
) -> dict:
    """Shared by the save_final tool and finish_upload — see either
    caller's docstring for the argument contract."""
    if encoding not in ("text", "base64"):
        return {"status": "error", "message": "encoding must be 'text' or 'base64'."}

    resolved = _resolve_ticker_for_save(identity, ticker, new_ticker_status, confirmed_new_ticker)
    if isinstance(resolved, dict):
        return resolved
    real_ticker, target_status = resolved

    if encoding == "base64":
        file_bytes, error = _decode_base64_or_none(content)
        if error:
            return {"status": "error", "message": error}
    else:
        try:
            file_bytes = pdf_render.render_markdown_to_pdf(content, ticker=real_ticker)
        except ValueError as error:
            return {"status": "error", "message": str(error)}
        filename = f"{filename.rsplit('.', 1)[0] if '.' in filename else filename}.pdf"

    return _save_bytes_to_dropbox(identity, real_ticker, target_status, filename, file_bytes, overwrite)


def _build_server(identity: str) -> MCPServer:
    other = _OTHER[identity]
    server = MCPServer(
        name=f"research-tool-bridge-{identity}",
        instructions=(
            "Tools for handing a research report back and forth between ChatGPT and "
            f"Claude on the user's behalf. This connector is the {_DISPLAY_NAME[identity]} "
            f"side: send_report always sends TO {_DISPLAY_NAME[other]}, get_pending "
            f"always checks what {_DISPLAY_NAME[other]} sent. Whenever the user starts "
            "discussing a specific ticker, call get_pending for that ticker before "
            "responding, so anything waiting surfaces without them having to ask "
            "explicitly.\n\n"
            "IMPORTANT — never call send_report or save_final as part of the same turn "
            "you draft or revise something. When asked to write or update a report, "
            "just show it in the chat and stop there — do not send it anywhere on your "
            "own initiative. Only call send_report after the user has seen that exact "
            f"content and explicitly said to send it to {_DISPLAY_NAME[other]} (e.g. "
            "\"send that to Claude\", \"looks good, send it\"). Only call save_final "
            "after the user has explicitly approved a specific version as final. A request "
            "to write/draft/revise a report is never by itself a request to send or "
            "save it.\n\n"
            "IMPORTANT — sharing a complex file (PDF, spreadsheet, etc.) with the other "
            "AI: do NOT try to transfer the real file via encoding='base64'. Instead, "
            "convert it — write out its actual substance as plain text/markdown "
            "(the full analysis, thesis, key figures, tables reformatted as markdown, "
            "assumptions) and send that with encoding='text'. The goal of sending "
            f"something to {_DISPLAY_NAME[other]} is for it to have everything it needs "
            "to critique the work and draft its own independent memo — it needs the "
            "substance, not a byte-identical copy of your file. encoding='text' has no "
            "size limit and is completely reliable; encoding='base64' is fragile at "
            "real file sizes (see below) and should essentially never be used for "
            "AI-to-AI sharing.\n\n"
            "IMPORTANT — a real file's base64 form can be tens of thousands of "
            "characters, and reading that much of it back into your own context in one "
            "step risks truncation, which would silently corrupt it — that's a real "
            "limit of your own tools, not something this server can fix. Only use "
            "encoding='base64' (on send_report or save_final) for files you're "
            "confident are small (roughly under 20K base64 characters). For anything "
            "larger, or you're unsure, tell the user to download the file himself from "
            "this chat and use the app's own Upload button instead.\n\n"
            "IMPORTANT — save_final's default text path (encoding='text') "
            "auto-renders your markdown into a real formatted PDF server-side — "
            "headings, styled tables, bold, lists — with no size limit and no "
            "reliability risk at all. This is the preferred way to save a finished "
            "memo. Only reach for encoding='base64' on save_final when the user "
            "specifically needs a real spreadsheet with live formulas or another "
            "format text genuinely can't represent — not just because the content is "
            "long or has tables (tables render fine as markdown).\n\n"
            "IMPORTANT — " + _CONTENT_REQUIREMENTS
        ),
    )

    @server.tool()
    def send_report(
        ticker: str, content: str, note: str = "", encoding: str = "text", filename: str | None = None
    ) -> dict:
        """Send a draft/revision to the other AI for it to review before
        anything is finalized. ONLY call this after the user has already
        seen this exact content and explicitly asked for it to be sent —
        writing or revising something is never on its own a request to
        send it. If you just drafted or updated something, show it and
        stop; wait for the user to say to send it before calling this.
        `ticker` labels which company this belongs to (best-effort
        matched to a real ticker's casing, but not required to exist
        yet). `encoding` should almost always be "text" (default —
        `content` is plain text/markdown): if what you have is a complex
        file (PDF, spreadsheet), convert it — write out its actual
        substance as markdown (analysis, thesis, key figures, tables)
        rather than attempting encoding='base64', which is unreliable at
        real file sizes and shouldn't be used for AI-to-AI sharing (see
        this connector's top-level instructions). `note` is an optional
        short message about what changed."""
        return _do_send_report(identity, other, ticker, content, note, encoding, filename)

    @server.tool()
    def get_pending(ticker: str | None = None, include_delivered: bool = True) -> dict:
        """Check whether the other AI has sent anything on this ticker —
        by default this returns its FULL history for `ticker` (every
        message ever sent, not just new/undelivered ones), so calling
        this once is always enough — you never need to ask the user "is this
        pending or older" or call it a second time to see something he
        already looked at before. Each message includes `timestamp_et`
        (already converted to the user's Eastern time, human-readable —
        use this one, not the raw UTC `timestamp` field) — always mention
        when you tell the user what you found (e.g. "ChatGPT sent this Aug 19
        at 4:03 PM ET"), don't just paste the content with no sense of
        when it arrived. Check
        each message's `encoding`/`filename` too ("base64" means `content`
        is a real file's bytes, not text to read). Pass `ticker` to scope
        to one company, omit it to check across all of them. Pass
        include_delivered=False only if you specifically want to know
        what's brand-new since the last check, not the full picture.

        This only covers messages sent through this bridge (send_report/
        save_final). For a file already sitting in a ticker's Dropbox
        folder that was never sent through the bridge (a prior memo, an
        existing model), use list_ticker_files / read_ticker_file
        instead.

        IMPORTANT — the user's standing content requirements for investment
        memos (confirmed directly with him, apply these by default in
        every memo unless he says otherwise), relevant to the rest of
        this conversation about this ticker, not just this call:
        0. Every memo needs an explicit action recommendation near the
        top — a single clear tag (BUY / HOLD / SELL / WATCH, etc.) with a
        one-line actionable takeaway (position sizing, entry range), not
        just a qualitative "Valuation Stance" or "Investment Snapshot"
        description with no tag attached. Write it as its own line
        starting with "Recommendation:" (e.g. "Recommendation: HOLD —
        don't initiate a full position here; add below $280") — save_
        final's renderer turns a line like that into a highlighted
        callout box automatically, so this is free once you write it in
        that exact form.
        1. If the company reports distinct business segments (e.g. PLTR's
        Government/Commercial, AAPL's Products/Services) break out
        profitability BY segment, not just revenue — and include an
        estimated value per segment (a real sum-of-the-parts table:
        segment, multiple, implied value), not just a company-wide
        valuation. If it genuinely has one reportable segment, say so
        explicitly rather than forcing a breakdown that doesn't exist.
        2. When covering leadership/governance/insider ownership, clearly
        distinguish how much of each insider's stake came from free stock
        (grants/RSUs) versus actual open-market purchases — don't just
        report a total ownership number.
        3. Cite specific factual claims inline with bracketed reference
        numbers (e.g. "revenue grew 93% YoY [3]"), and include a numbered
        "Sources:" or "Source references:" list (or a Ref/Source appendix
        table) defining what each number is. save_final's PDF renderer
        turns these into real per-page footnotes automatically, but only
        if they're actually there — a memo without them gets no visible
        sourcing at all, regardless of how well-researched it is. Every
        [N] used in the body needs a matching definition — no orphaned
        citation numbers."""
        label = _normalize_ticker_label(ticker) if ticker else None
        messages = bridge_store.fetch_pending(identity, label, include_delivered)
        return {"count": len(messages), "messages": messages}

    @server.tool()
    def start_research(ticker: str, report_type: str) -> dict:
        """Call this the moment the user asks for one of his standard report
        types on a ticker (e.g. "build me an investment memo on X", "give
        me a competitive landscape analysis on Y") — it returns the user's
        own research brief for that report type as `prompt`, with the
        ticker already filled in. Follow that brief to draft the report
        in this chat; it already includes his standing content/citation
        requirements and the save_final workflow at the end, so you don't
        need to look anything else up. `report_type` must be exactly one
        of:
        - "industry_deep_dive" — a primer on the industry/sector the
          ticker operates in (market size, dynamics, competitive
          structure, regulatory environment, outlook)
        - "investment_research_report" — the full investment memo: moat,
          financials, recent developments, M&A history, management/
          ownership, risks, valuation, bull/bear/crux, open questions
        - "competitive_landscape_analysis" — the ticker vs. 5-8 named
          competitors: positioning, profiles, dynamics, opportunity/risk
          matrix, moat comparison
        - "last_4_quarters_summary" — a dedicated section per each of the
          last 4 reported quarters plus a trend/outlook wrap-up
        - "quarterly_earnings_digest" — a tight 2-3 page digest of the
          most recent single quarter for a PM skimming in a few minutes
        Returns status='error' with the valid list if `report_type`
        doesn't match one of these — never guess which one the user meant."""
        prompt = research_prompts.get_prompt(report_type, ticker)
        if prompt is None:
            return {
                "status": "error",
                "message": (
                    f"'{report_type}' isn't a recognized report_type. Valid options: "
                    + ", ".join(research_prompts.PROMPTS.keys())
                ),
            }
        return {"status": "ok", "ticker": ticker.strip().upper(), "report_type": report_type, "prompt": prompt}

    @server.tool()
    def list_tickers(status: str | None = None) -> dict:
        """List every ticker the user currently has a folder for — use this
        for "what's in Active/Inactive/Historicals" or "what tickers do
        we have" type questions. This is different from
        list_ticker_files: that one lists files INSIDE one already-known
        ticker's folder, this one lists the tickers THEMSELVES across the
        whole structure. Pass status as "active", "inactive", or
        "historicals" to scope to one bucket, or omit it to list all
        three together. Returns status='error' if `status` is passed but
        isn't one of those three values."""
        if identity in _TEST_IDENTITIES:
            entries = dropbox_client.list_folder(settings.dropbox_bridge_test_path)
            tickers = sorted(entry["name"] for entry in entries if entry["is_folder"])
            return {"status": "ok", "tickers": tickers}
        if status is not None and status not in ticker_registry.STATUSES:
            return {
                "status": "error",
                "message": f"status must be one of {', '.join(ticker_registry.STATUSES)}, or omitted to list all.",
            }
        known = ticker_registry.get_known_tickers()
        if status:
            tickers = sorted(ticker for ticker, ticker_status in known.items() if ticker_status == status)
        else:
            tickers = sorted(known.keys())
        return {"status": "ok", "status_filter": status, "count": len(tickers), "tickers": tickers}

    @server.tool()
    def list_ticker_files(ticker: str) -> dict:
        """List the files already sitting in a ticker's real Dropbox
        folder — prior memos, models, anything saved there before, not
        just things sent through this bridge. Use this when the user
        references something that already exists (e.g. "compare this to
        the prior quarter's digest") so you know what's actually there
        before asking him to attach it manually. Returns status='error'
        if the ticker doesn't have an existing folder (see the message
        for why — unrecognized vs. a likely typo). Follow up with
        read_ticker_file on whichever filename is relevant."""
        folder = _resolve_ticker_folder(identity, ticker)
        if isinstance(folder, dict):
            return folder
        entries = dropbox_client.list_folder(folder)
        files = [entry["name"] for entry in entries if not entry["is_folder"]]
        return {"status": "ok", "ticker": ticker.strip().upper(), "files": files}

    @server.tool()
    def read_ticker_file(ticker: str, filename: str) -> dict:
        """Read the actual text content of a file already sitting in a
        ticker's real Dropbox folder (get the exact filename from
        list_ticker_files first — this won't guess). Works for PDFs
        (text is extracted automatically) and plain text/markdown files.
        For a format this can't extract text from (e.g. an Excel model),
        returns status='error' with a Dropbox link instead — share that
        with the user or ask him to describe what's in it rather than
        guessing at its contents."""
        folder = _resolve_ticker_folder(identity, ticker)
        if isinstance(folder, dict):
            return folder
        full_path = f"{folder}/{filename}"
        try:
            data = dropbox_client.download(full_path)
        except Exception as error:  # noqa: BLE001 - surface as a normal tool error, not a crash
            return {"status": "error", "message": f"couldn't read '{filename}': {error}"}
        text = document_text.extract_text(filename, data)
        if text is None:
            preview_url = dropbox_client.get_shareable_link(full_path)
            return {
                "status": "error",
                "message": (
                    f"'{filename}' isn't a format this can extract text from (PDF/txt/md only). "
                    + _addressing(
                        identity,
                        f"Share this link with the user, or ask him what's in it: {preview_url}",
                        f"Here's the link, take a look: {preview_url}",
                    )
                ),
            }
        return {"status": "ok", "ticker": ticker.strip().upper(), "filename": filename, "text": text}

    @server.tool()
    def save_final(
        ticker: str,
        filename: str,
        content: str,
        encoding: str = "text",
        new_ticker_status: str | None = None,
        overwrite: bool = False,
        confirmed_new_ticker: bool = False,
    ) -> dict:
        """Save a the user-approved final version to a ticker's real Dropbox
        folder — a text report (memo) or a real binary file (e.g. a
        financial model), sorted into the ticker's folder exactly like a
        manual upload.

        If `ticker` already exists (Active/Inactive/Historicals), this
        saves into its existing folder and `new_ticker_status` is ignored.

        If `ticker` looks like a genuinely new one (not a near-miss of an
        existing ticker), this CAN create it — pass `new_ticker_status` as
        "active", "inactive", or "historicals" to say which bucket it
        belongs in. If `new_ticker_status` is omitted for a new ticker,
        this returns status='error' asking you to confirm with the user which
        bucket it belongs in, then call save_final again with that status
        — never guess a bucket yourself.

        If `ticker` looks like a likely typo of an existing ticker, this
        refuses (status='error') and asks the user to confirm the exact
        ticker — it will not silently create a near-duplicate of a real
        one. If the user explicitly confirms it's genuinely a different,
        new ticker (not a typo), call save_final again with
        confirmed_new_ticker=True and new_ticker_status set, to actually
        create it — never set confirmed_new_ticker=True without him
        having said so; guessing defeats the whole point of asking.

        `encoding` should almost always be "text" (default — `content` is
        plain markdown; it's automatically rendered into a real formatted
        PDF before saving — real headings, styled tables, bold, lists —
        so the user gets something readable and well-formatted, not raw
        markdown; `filename`'s extension is replaced with .pdf
        regardless of what you pass). This is the preferred way to save
        any finished memo, including ones with tables of figures — tables
        render fine as markdown, and this path has no size limit and no
        reliability risk. Only use "base64" (`content` a base64-encoded
        binary file, `filename` with its real extension e.g. "ZBQ
        Model.xlsx") when the user specifically needs a real spreadsheet
        with live formulas or another format text genuinely can't
        represent — and only for files small enough to encode safely,
        see this connector's top-level instructions.

        IMPORTANT — what the user approved in this chat is NOT what he'll
        get. This tool re-renders `content` into this system's own
        formatted PDF, which looks different from whatever you showed
        him here (different fonts, colors, layout) — so his earlier
        "looks good, save it" was approving the content, not the actual
        rendered file. The response includes `preview_url` — a real
        Dropbox link that opens the saved file directly in Dropbox's own
        preview. ALWAYS share this link right after saving and say
        something like "here's how it actually rendered — take a look"
        (don't just say "saved" — paste the link). Treat this as a
        draft until the user has looked at `preview_url` specifically and
        confirmed he's happy with THAT — not with what you showed him
        in chat. If he asks for changes, save again with
        overwrite=True and repeat.

        IMPORTANT — before calling this tool, run this exact audit against
        `content`, one line at a time — don't eyeball it and move on; a
        past run skimmed this list, saw the word "segment" appear
        somewhere, decided item 1 was "basically covered," and saved a
        memo that was actually missing half of it:
        0. Find a line starting with "Recommendation:" near the top of
        `content`, with an explicit tag (BUY/HOLD/SELL/WATCH/etc.) and a
        one-line actionable takeaway. A "Valuation Stance" or "Investment
        Snapshot" description alone does NOT satisfy this — those are
        commentary, not a tag. If there's no such line, add one now
        rather than saving without a clear recommendation the user would
        otherwise have to ask for explicitly.
        1a. List every business segment `content` names (e.g. PLTR:
        Government, Commercial. AAPL: Products, Services). If that list
        has 0 or 1 entries, item 1 doesn't apply — skip to item 2.
        1b. For EACH segment on that list, find the specific sentence or
        table row giving that segment's profitability/margin. If any
        segment is missing one, add it now.
        1c. For EACH segment on that list, separately find a specific
        estimated VALUE for that segment alone — a dollar amount or
        implied EV from a named multiple (e.g. "$70B" or "10-16x revenue
        → ~$70B"), not a margin, not a qualitative comment. If you find
        yourself with commentary about SOTP methodology but no actual per
        segment number, that step is NOT done — go back and add real
        numbers, or a numbered range, for every segment on the 1a list.
        2. Find the sentence(s) covering insider ownership. Confirm they
        state, per insider (not as one blended figure), how much came
        from grants/RSUs vs. actual open-market cash purchases.
        3. Find every [N] marker used anywhere in the body, and every "[N]
        ..." definition in your Sources list. The two sets must match
        exactly — a body [N] with no matching definition, or a defined
        source never actually cited, is a bug to fix now, not later.
        4. Scan `content` for every `**` and `*` marker and confirm each
        one has a matching close on the same line/field it opens on — a
        lone unmatched `**` (e.g. a line ending "...toward $270 or
        below**" with no opening `**` earlier in that line) prints as
        literal, ugly asterisks in the final PDF; this renderer converts
        markdown exactly as written, it doesn't guess at what you meant.

        Do this as an actual line-by-line pass over the text you're about
        to save, not a summary judgment of whether it "looks complete" —
        the same way you'd proofread a real document before handing it to
        someone, checking specific claims rather than skimming for the
        general shape of one."""
        return _do_save_final(
            identity, ticker, filename, content, encoding, new_ticker_status, overwrite, confirmed_new_ticker
        )

    return server


def _build_upload_router(identity: str) -> APIRouter:
    """A plain REST endpoint (not an MCP tool) for the local upload MCP
    server (see local-tools/research-tool_upload_mcp.py) to call. Exists
    because that local server already has a real file's bytes sitting on
    disk — routing them through here as a normal multipart upload avoids
    ever re-encoding them as base64/JSON, sidestepping the whole
    reliability problem the AI-facing base64 path has for large files.
    Reuses save_final's exact ticker-resolution/typo-guard/audit-log
    logic via _resolve_ticker_for_save/_save_bytes_to_dropbox."""
    router = APIRouter()

    @router.post("/upload-file")
    async def upload_file(
        file: UploadFile,
        ticker: str = Form(...),
        new_ticker_status: str | None = Form(None),
        overwrite: bool = Form(False),
        confirmed_new_ticker: bool = Form(False),
    ) -> dict:
        resolved = _resolve_ticker_for_save(identity, ticker, new_ticker_status, confirmed_new_ticker)
        if isinstance(resolved, dict):
            return resolved
        real_ticker, target_status = resolved

        file_bytes = await file.read()
        return _save_bytes_to_dropbox(identity, real_ticker, target_status, file.filename, file_bytes, overwrite)

    return router


chatgpt_server = _build_server("chatgpt")
claude_server = _build_server("claude")
chatgpt_test_server = _build_server("chatgpt_test")
claude_test_server = _build_server("claude_test")

chatgpt_app = chatgpt_server.streamable_http_app(transport_security=_security)
claude_app = claude_server.streamable_http_app(transport_security=_security)
chatgpt_test_app = chatgpt_test_server.streamable_http_app(transport_security=_security)
claude_test_app = claude_test_server.streamable_http_app(transport_security=_security)

chatgpt_upload_router = _build_upload_router("chatgpt")
claude_upload_router = _build_upload_router("claude")
chatgpt_test_upload_router = _build_upload_router("chatgpt_test")
claude_test_upload_router = _build_upload_router("claude_test")
