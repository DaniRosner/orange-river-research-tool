# Endpoints for individual files: upload, search, per-ticker file listing,
# and Needs Review assignment. Ticker-folder-level endpoints (list
# Active/Inactive, move a ticker) live in tickers.py instead.
#
# The upload endpoint is the most complex thing in this app — it can pause
# mid-request and ask the caller (the frontend) a question before it'll
# actually file anything, rather than always either succeeding or failing
# outright. See its docstring below for the full decision tree.

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile

from app.config import settings
from app.services import activity_log, auth, category_routing, dropbox_client, email_render, eml_parse, thumbnails, ticker_registry
from app.services.sorting import (
    find_ambiguous_uppercase_mentions,
    find_known_ticker,
    find_ticker_mentioned_anywhere,
    parse_leading_token,
    parse_ticker,
)

router = APIRouter(prefix="/files", tags=["files"])

# Dropbox's limit for a single non-chunked upload call. Research files
# (PDFs, decks, models) are essentially never this large in practice, so a
# clear rejection is enough here — not worth building chunked/resumable
# upload sessions for a case this unlikely to come up.
MAX_SIMPLE_UPLOAD_BYTES = 150 * 1024 * 1024

# A `relative_path` of "" can't be used to mean "the root, deliberately"
# — FastAPI's `Form(None)` silently coerces an empty-string multipart
# field back to None (indistinguishable from the field being absent
# entirely), which would make a chosen root answer loop straight back
# into another choose_subfolder question. This sentinel is what
# "deliberately the root" is sent as instead — chosen because a real
# Dropbox folder can never be named "." (see UploadButton.jsx, which
# sends this both for a folder-drag's own root-level files and for the
# choose_subfolder dialog's root option).
ROOT_RELATIVE_PATH = "."


def _resolve_upload(
    folder: str,
    filename: str,
    content: bytes,
    on_duplicate: str | None,
    success_status: str,
    extra: dict,
    user: dict,
    log_ticker: str | None,
    log_relative_path: str = "",
) -> dict:
    """
    Check `folder` for an existing file named `filename` before uploading.

    `user`/`log_ticker`/`log_relative_path` are for activity_log only —
    this is the one place an upload actually happens (the
    dropbox_client.upload_file() call below), regardless of which of
    upload_file()'s many resolution paths got here, so it's the natural
    single choke point to log from rather than repeating that at every
    call site. `log_ticker` is the *real* folder name (a theme folder's
    own name, not the display-only base ticker upload_file() sometimes
    computes for it) — it has to match exactly what list_files_for_ticker()
    will later look activity up by.

    Three cases, distinguished using Dropbox's content hash (see
    compute_content_hash()) so we know *before* saying anything whether a
    same-named file is actually the same file or a coincidence:

    - No file with this name exists: upload normally.
    - A file with this name exists AND its content is byte-identical to
      what's being uploaded: this is the same file, not a real conflict —
      skip uploading and asking anything, and just say so via `note`. This
      is what stops re-uploading the same file twice from ever being
      confusing (previously we'd ask "replace or keep both?" even when the
      honest answer was "neither, it's already there").
    - A file with this name exists AND the content genuinely differs: this
      is a real conflict. If `on_duplicate` wasn't provided, filing is
      held and "duplicate_needs_confirmation" is returned instead of
      guessing — the caller must resubmit with `on_duplicate` set to
      "replace" (overwrite the existing file) or "keep_both" (upload
      alongside it under an auto-generated new name).

    Also checks whether `folder` itself is currently involved in a suffix
    collision (see category_routing.check_suffix_collision_for()) — if
    so, `suffix_warning` is added to whatever's returned, so the frontend
    can pop up an immediate, hard-to-miss warning right when it matters,
    rather than relying only on the separate periodic banner to catch it
    later. None in the overwhelming majority of uploads, since most
    folder names aren't suffix-shaped at all.
    """
    suffix_warning = category_routing.check_suffix_collision_for(folder.rsplit("/", 1)[-1])
    if suffix_warning:
        extra = {**extra, "suffix_warning": suffix_warning}

    existing = next(
        (
            entry
            for entry in dropbox_client.list_folder(folder)
            if entry["name"] == filename and not entry["is_folder"]
        ),
        None,
    )

    if existing is not None:
        if existing["content_hash"] == dropbox_client.compute_content_hash(content):
            return {
                "status": success_status,
                "filename": filename,
                "note": "This exact file is already there — nothing new was uploaded.",
                **extra,
            }

        if on_duplicate is None:
            return {"status": "duplicate_needs_confirmation", "filename": filename, **extra}

    actual_filename = dropbox_client.upload_file(
        f"{folder}/{filename}", content, overwrite=(on_duplicate == "replace")
    )
    activity_log.record(
        user, "uploaded", ticker=log_ticker, filename=actual_filename, relative_path=log_relative_path
    )
    return {"status": success_status, "filename": actual_filename, **extra}


def _maybe_ask_subfolder(folder: str, relative_path: str | None) -> dict | None:
    """
    Before actually filing into `folder`, check whether it already has
    subfolders (e.g. a ticker with "Old Models", "Q3 2024") — if so, and
    the caller hasn't already answered this via `relative_path`, pause and
    ask which one the file should go into rather than silently dropping
    it at the folder's root. Most real ticker/theme folders in practice do
    have their own internal structure, so filing everything flat would
    just mean a manual move afterward every time.

    `relative_path is not None` (including "", meaning "root,
    deliberately") means this has already been answered for this upload —
    skip straight through. A folder with no subfolders (including one
    that doesn't exist yet, e.g. a brand-new ticker — see
    dropbox_client.list_folder()) skips this too, since there's nothing to
    choose between.

    Deliberately NOT applied to a whole dropped *folder's* contents (see
    UploadButton.jsx) — there, `relative_path` is always already set
    (possibly to "") from the dropped folder's own structure, so this
    never fires for that flow; asking per-file would fight the "preserve
    the folder's existing layout" behavior that already exists for it.
    """
    if relative_path is not None:
        return None
    subfolders = sorted(entry["name"] for entry in dropbox_client.list_folder(folder) if entry["is_folder"])
    if not subfolders:
        return None
    return {"status": "choose_subfolder", "folder_label": folder.rsplit("/", 1)[-1], "subfolders": subfolders}


def _fallback_to_needs_review_or_mention(
    filename: str, content: bytes, on_duplicate: str | None, known: dict[str, str], reason: str, user: dict
) -> dict:
    """
    Last resort before giving up on a file entirely: an exact,
    unambiguous real-ticker mention anywhere in the filename has already
    been tried by this point (see upload_file(), where that check now
    runs BEFORE the general typo-guess and new-ticker checks, not after
    — so a real mention always wins over a coincidental typo-match on the
    leading word, e.g. "Notes" in "Notes on ZRE.TO for review.pdf" being
    mistaken for a typo of an unrelated ticker before ZRE.TO ever gets a
    chance). By the time this runs, that's already come up empty, so all
    that's left to check:

    Was the ambiguity — if find_ticker_mentioned_anywhere() found two-plus
    real tickers instead of zero — worth actually asking about, i.e. were
    at least two of those mentions written in all-caps in the filename
    itself (e.g. "Comparing ZBQ to ZPAX.pdf")? If so, filing is held
    ("ambiguous_mention") until the caller resubmits with
    `override_ticker` set to whichever one was meant. See
    find_ambiguous_uppercase_mentions() for why this is scoped to
    all-caps mentions specifically — a lowercase collision is far more
    likely to be coincidental prose than a deliberate reference.

    Falls through to the normal Needs Review filing if nothing above
    resolved it.
    """
    ambiguous = find_ambiguous_uppercase_mentions(filename, list(known.keys()))
    if ambiguous:
        return {"status": "ambiguous_mention", "filename": filename, "candidates": ambiguous}

    return _resolve_upload(
        settings.dropbox_needs_review_path,
        filename,
        content,
        on_duplicate,
        "needs_review",
        {"reason": reason},
        user,
        log_ticker=None,
    )


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    override_ticker: str | None = Form(None),
    target_status: str | None = Form(None),
    on_duplicate: str | None = Form(None),
    relative_path: str | None = Form(None),
    user: dict = Depends(auth.current_user),
):
    """
    Accept a research file upload and file it into Dropbox.

    `override_ticker`, if provided, is a resubmission confirming a prior
    `confirm_needed`/`new_ticker_needs_status` response below — it skips
    all analysis and files directly under that ticker (using its real
    existing status if it already exists, `target_status` otherwise).
    `relative_path`, if also provided, places the file inside a subfolder
    of the ticker folder (e.g. "Old Models") instead of directly inside
    it — used when uploading a whole dropped *folder's* contents (see
    `/tickers/resolve`), so any subfolders inside it are preserved rather
    than flattened. For a single (non-folder) upload, it instead carries
    the answer to a `choose_subfolder` pause (see `_maybe_ask_subfolder`)
    — "" means "file it directly in the root, deliberately," not "no
    answer yet."

    Once a destination ticker or theme folder has actually been resolved
    below (by any of steps 1–3, or an `override_ticker` resubmission) —
    but before a single loose file is ever filed into it — filing pauses
    with `{"status": "choose_subfolder", "subfolders": [...]}` if that
    folder already has its own subfolders and `relative_path` hasn't been
    provided yet. Most real ticker/theme folders do have internal
    structure (e.g. "Old Models", per-quarter breakdowns), so this stops
    every upload from defaulting to a flat root that then needs a manual
    move. Resubmit with `relative_path` set to one of the listed subfolder
    names, a new name to create one, or `""` for the root. Never fires for
    a whole dropped *folder's* contents, which already carries its own
    `relative_path` for every file (see UploadButton.jsx) — only for
    loose, individually selected/dropped files.

    Otherwise, in order:

    1. Does the filename's leading token (letters/digits plus an optional
       dot suffix, extracted WITHOUT requiring a separator+description to
       follow) exactly match a real, already-existing ticker? -> filed
       there. Checked first, no exceptions — an established ticker can
       never be hijacked by category routing.
    2. Not that, but does a `(.SUFFIX)` marker — parentheses required,
       e.g. `(.BB)` — appear ANYWHERE in the filename, matching a theme
       folder registered via category_routing.py (set up by renaming a
       non-ticker folder to end in that same marker, e.g.
       `Busted Biotechs (.BB)`)? -> filed directly into that folder, no
       further ticker matching/confirmation. The parenthesized form is
       required specifically so this can never collide with a real
       ticker's own bare exchange suffix (e.g. `MCR.TO`, no parens) — see
       category_routing.find_category_tag() for why that used to be a
       genuine ambiguity and no longer is. Only acts when exactly one
       distinct theme folder's marker is found anywhere in the name — a
       filename carrying two different theme folders' tags is genuinely
       ambiguous and falls through instead of picking whichever happened
       to come first.
    3. Not that, but does a real, already-existing ticker show up
       unambiguously ANYWHERE in the filename — not just the leading word
       (e.g. "zbq" in "quarterly notes on zbq.pdf", or "ZRE.TO" in "Notes
       on ZRE.TO for review.pdf")? -> filed there. Checked here,
       deliberately BEFORE steps 5–6 below — an exact mention elsewhere is
       a stronger signal than a fuzzy typo-guess about the leading word
       alone, which is what stops an ordinary phrase's leading word (e.g.
       "Notes") from being mistaken for a typo of some unrelated real
       ticker before a real mention elsewhere ever gets a chance. Only
       acts when exactly one real ticker is mentioned — two is genuinely
       ambiguous, see step 7. See find_ticker_mentioned_anywhere().
    4. Filename doesn't have the `TICKER Description.ext` shape at all
       (no space/hyphen/underscore-separated description) -> skip to
       step 7.
    5. Parsed ticker doesn't match an existing ticker, but looks like a
       typo of a real one -> filing is held; the caller must resubmit with
       `override_ticker` set to the confirmed/corrected ticker.
    6. Parsed ticker doesn't match, has no close look-alikes, and looks
       like a deliberate ticker (uppercase): this is a genuinely new
       ticker. Rather than assuming it belongs in Active, filing is held
       ("new_ticker_needs_status") until the caller resubmits with
       `override_ticker` set to that same ticker and `target_status` set
       to "active", "inactive", or "historicals" — the file might just as
       easily be someone archiving notes for a company that's already
       inactive, or one that doesn't fit either bucket cleanly.
    7. Last resort, reached either because step 4 found no pattern at all
       or because the step-5/6 checks came up empty: was step 3's mention
       check actually ambiguous (two-plus real tickers mentioned), and
       was that ambiguity written in all-caps (e.g. "Comparing ZBQ to
       ZPAX.pdf")? -> filing is held ("ambiguous_mention") until the
       caller resubmits with `override_ticker` set to whichever one was
       meant. Scoped to all-caps mentions specifically — a lowercase
       collision is far more likely to be coincidental prose than a
       deliberate reference. See find_ambiguous_uppercase_mentions().
    8. Nothing above resolved it -> Needs Review.

    If a file with the same name already exists wherever this one is
    about to be filed, filing is held ("duplicate_needs_confirmation")
    until the caller resubmits with `on_duplicate` set to "replace" or
    "keep_both" — see `_resolve_upload`.
    """
    content = await file.read()

    # A raw .eml (dragged straight out of a desktop mail client — see
    # TickerList.jsx's card drop, or UploadButton's generic drop zone)
    # isn't readable/openable through Dropbox's own preview, unlike a
    # PDF. Convert it server-side before anything else runs, reusing
    # the same eml_parse/email_render pair the email-intake webhook
    # already uses for a forwarded email's body. Only the extension is
    # swapped (.eml -> .pdf) here, not the base filename — every ticker-
    # resolution step below reads file.filename directly (leading
    # token, mentioned-anywhere, etc.), and an .eml's filename is
    # usually already the original subject line (Gmail names it that
    # way), which is exactly what those steps need to keep working
    # unchanged.
    if file.filename and file.filename.lower().endswith(".eml"):
        parsed = eml_parse.parse_eml(content)
        content = email_render.render_email_to_pdf(
            parsed["subject"], parsed["sender"], parsed["date_str"], parsed["body_text"]
        )
        file.filename = f"{file.filename[:-4]}.pdf"

    if len(content) > MAX_SIMPLE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"'{file.filename}' is too large to upload "
                f"({len(content) / 1_000_000:.0f} MB) — files over 150 MB aren't supported."
            ),
        )

    known = ticker_registry.get_known_tickers()

    if override_ticker:
        # If override_ticker is already a real ticker, always file it
        # where it actually lives (ignore target_status — that's only
        # for a ticker that doesn't exist yet). Otherwise this is
        # confirming a brand-new ticker, so target_status decides.
        is_new_ticker = override_ticker not in known
        status = known.get(
            override_ticker, target_status if target_status in ticker_registry.STATUSES else "active"
        )
        folder = f"{ticker_registry.folder_path_for_status(status)}/{override_ticker}"
        # A ticker created implicitly this way (the far more common path —
        # dragging/uploading straight into a brand-new name — vs. the
        # dedicated empty-folder "create" endpoint) previously logged only
        # the file-level "uploaded" entry, never a ticker-level "created"
        # one. That left latest_for_ticker() with nothing to show until
        # some later ticker-level action (move/rename/delete) happened —
        # and worse, if the ticker had been deleted and then recreated
        # this way, its card kept showing "Deleted by X" forever, since
        # recreation never overwrote that stale record. `known` is
        # re-fetched fresh per request, so this naturally fires only once
        # per new ticker — every subsequent file in the same folder-drop
        # batch sees the folder already exists and skips this.
        if is_new_ticker:
            activity_log.record(user, "created", ticker=override_ticker, detail=f"status={status}")
        subfolder_ask = _maybe_ask_subfolder(folder, relative_path)
        if subfolder_ask:
            return subfolder_ask
        if relative_path and relative_path != ROOT_RELATIVE_PATH:
            folder = f"{folder}/{relative_path}"
        return _resolve_upload(
            folder,
            file.filename,
            content,
            on_duplicate,
            "filed",
            {"ticker": override_ticker},
            user,
            log_ticker=override_ticker,
            log_relative_path=relative_path if relative_path and relative_path != ROOT_RELATIVE_PATH else "",
        )

    # The leading token (letters/digits plus an optional dot suffix,
    # extracted WITHOUT requiring a separator+description to follow — see
    # parse_leading_token()): does it exactly match a real, already-
    # existing ticker? Checked first, no exceptions — an established
    # ticker can never be hijacked by category routing, no matter what
    # suffix it carries.
    leading_token = parse_leading_token(file.filename)
    if leading_token:
        found = find_known_ticker(leading_token, list(known.keys()))
        if found:
            folder = f"{ticker_registry.folder_path_for_status(known[found])}/{found}"
            # `relative_path` is normally only ever paired with
            # `override_ticker` (see above) — this is defense-in-depth for
            # the same subfolder-preserving behavior in case a caller ever
            # reaches this ordinary ticker-match path with it set instead,
            # so a real ticker's subfolder structure is honored regardless
            # of which path resolved the ticker.
            subfolder_ask = _maybe_ask_subfolder(folder, relative_path)
            if subfolder_ask:
                return subfolder_ask
            if relative_path and relative_path != ROOT_RELATIVE_PATH:
                folder = f"{folder}/{relative_path}"
            return _resolve_upload(
                folder,
                file.filename,
                content,
                on_duplicate,
                "filed",
                {"ticker": found},
                user,
                log_ticker=found,
                log_relative_path=relative_path if relative_path and relative_path != ROOT_RELATIVE_PATH else "",
            )

    # Category-suffix routing is checked next: does a `(.SUFFIX)` marker —
    # parentheses required — appear ANYWHERE in the filename, matching a
    # registered theme folder? Checked this early specifically so the
    # deliberate "TICKER (.SUFFIX) Description.ext" shape still resolves
    # immediately without waiting on every other check to fail first. The
    # required parens are what make this unconditionally safe to check
    # with no typo-guardrail or "suspicious extension" ambiguity check in
    # front of it — a real ticker's own bare exchange suffix (e.g.
    # "MCR.TO") can never match this, parenthesized or not, so there's
    # nothing left to disambiguate. See category_routing.find_category_tag().
    category_folder = category_routing.find_category_tag(file.filename, category_routing.get_category_folders())
    if category_folder:
        category_name = category_folder.rsplit("/", 1)[-1]
        subfolder_ask = _maybe_ask_subfolder(category_folder, relative_path)
        if subfolder_ask:
            return subfolder_ask
        if relative_path and relative_path != ROOT_RELATIVE_PATH:
            category_folder = f"{category_folder}/{relative_path}"
        return _resolve_upload(
            category_folder,
            file.filename,
            content,
            on_duplicate,
            "filed_category",
            {"category_folder": category_name},
            user,
            log_ticker=category_name,
            log_relative_path=relative_path if relative_path and relative_path != ROOT_RELATIVE_PATH else "",
        )

    # Does a real, already-existing ticker show up unambiguously ANYWHERE
    # in the filename (not just the leading word)? Checked here,
    # deliberately BEFORE the general typo-guess and "is this a brand-new
    # ticker" checks below — an exact mention elsewhere is a stronger,
    # more certain signal than a fuzzy guess about the leading word alone,
    # so it should win first. This is what stops an ordinary phrase's
    # leading word (e.g. "Notes" in "Notes on ZRE.TO for review.pdf")
    # from being mistaken for a typo of some unrelated real ticker before
    # the genuinely-mentioned real ticker (ZRE.TO) ever gets a chance —
    # without needing to gate the typo-guess by case, which would also
    # have cost real typo-help for a genuinely lowercase ticker like the
    # real "zqrx" (e.g. someone typing "zqrz"). A typo is never an EXACT
    # match, so it's never found here either way, and still correctly
    # falls through to the typo-guess afterward.
    mentioned = find_ticker_mentioned_anywhere(file.filename, list(known.keys()))
    if mentioned:
        folder = f"{ticker_registry.folder_path_for_status(known[mentioned])}/{mentioned}"
        subfolder_ask = _maybe_ask_subfolder(folder, relative_path)
        if subfolder_ask:
            return subfolder_ask
        if relative_path and relative_path != ROOT_RELATIVE_PATH:
            folder = f"{folder}/{relative_path}"
        return _resolve_upload(
            folder,
            file.filename,
            content,
            on_duplicate,
            "filed_mentioned",
            {"ticker": mentioned},
            user,
            log_ticker=mentioned,
            log_relative_path=relative_path if relative_path and relative_path != ROOT_RELATIVE_PATH else "",
        )

    ticker = parse_ticker(file.filename)

    if ticker is None:
        return _fallback_to_needs_review_or_mention(
            file.filename,
            content,
            on_duplicate,
            known,
            "filename doesn't match the 'TICKER Description.ext' pattern",
            user,
        )

    # Note: resolution["kind"] can never be "matched" here — that's
    # already been ruled out above, since `ticker` (parse_ticker's result)
    # always equals `leading_token` whenever parse_ticker succeeds at all.
    resolution = ticker_registry.resolve_ticker(ticker, known)

    if resolution["kind"] == "confirm_needed":
        return {"status": "confirm_needed", "parsed_ticker": ticker, "suggestions": resolution["suggestions"]}

    if resolution["kind"] == "new_ticker_needs_status":
        return {"status": "new_ticker_needs_status", "parsed_ticker": ticker}

    return _fallback_to_needs_review_or_mention(
        file.filename,
        content,
        on_duplicate,
        known,
        "parsed prefix isn't a recognized ticker and doesn't look like a deliberate new one",
        user,
    )


@router.get("/search")
def search_files(q: str):
    """
    Search filenames across every ticker (Active + Inactive + Historicals)
    and Needs Review. Each match reports which ticker/status it belongs to
    (or `needs_review` for unfiled matches), so the frontend can jump
    straight to the right place. Matches outside these folders (e.g. in
    unrelated Dropbox folders once this points at real data) are ignored.

    dropbox_client.search_files() uses Dropbox's own search API, which
    does fuzzy/relevance matching rather than exact substring matching —
    left alone, a short query like "KBS" comes back with everything
    Dropbox judged "close enough" (ZBP, ZBQ, ZBQ.BB, ...), which is far
    too noisy to be useful as a "find this specific file" tool,
    especially with short ticker symbols where fuzzy matching has the
    least signal to work with. Filtered here to only keep matches whose
    filename actually contains the query, case-insensitively — Dropbox's
    search is still doing the real work of finding candidates efficiently
    across the whole account, this is just a precision pass on top.
    """
    query = q.strip()
    if not query:
        return []

    results = []
    for match in dropbox_client.search_files(query):
        if query.lower() not in match["name"].lower():
            continue
        path = match["path"]

        # Figure out which of our folders (if any) this match actually
        # lives in by checking its real path, rather than trusting
        # Dropbox's search-path scoping — see the long comment on
        # dropbox_client.search_files() for why that's necessary.
        matched_status = None
        for status in ticker_registry.STATUSES:
            base_path = ticker_registry.folder_path_for_status(status)
            if path.startswith(base_path + "/"):
                # e.g. ".../Active/ZBQ/Notes.docx" minus ".../Active/"
                # leaves "ZBQ/Notes.docx" — the first path segment is the
                # ticker.
                relative = path[len(base_path) :].strip("/")
                ticker = relative.split("/")[0]
                results.append({"filename": match["name"], "ticker": ticker, "status": status})
                matched_status = status
                break

        if matched_status is None and path.startswith(settings.dropbox_needs_review_path + "/"):
            results.append({"filename": match["name"], "ticker": None, "status": "needs_review"})

    return results


@router.get("/ticker/{ticker}")
def list_files_for_ticker(ticker: str):
    """
    List every file stored under Active/{ticker}/ or Inactive/{ticker}/,
    at any depth — not just files sitting directly in the ticker's root.
    Each entry includes `relative_path` (empty string for a file directly
    in the ticker folder, e.g. "Old Models" for one nested inside that
    subfolder) so the frontend can render subfolders as their own group
    rather than silently hiding everything inside them, `size` and
    `modified` (an ISO 8601 timestamp) so the frontend can offer sorting
    by either, and `last_action`/`last_action_by`/`last_action_at` — who
    did what to this file *through this app* (upload/delete/etc, see
    activity_log.py), all `None` if nothing's been logged for it yet
    (e.g. it predates this feature, or was only ever touched directly in
    Dropbox).

    Returns `{"files": [...], "folders": [...]}` — `folders` is every
    subfolder's relative_path at any depth, including ones with nothing
    in them (see dropbox_client.list_folder_recursive()) — a folder that
    already has a file inside it is also discoverable from that file's
    own relative_path, but a genuinely empty one (e.g. just created via
    POST .../folder below) has no file to infer it from.
    """
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    listing = dropbox_client.list_folder_recursive(folder)
    entries = listing["files"]
    activity = activity_log.latest_for_files(ticker, [entry["name"] for entry in entries])
    result = []
    for entry in entries:
        entry_activity = activity.get((entry["name"], entry["relative_path"]))
        result.append(
            {
                "name": entry["name"],
                "relative_path": entry["relative_path"],
                "size": entry["size"],
                "modified": entry["modified"],
                "last_action": entry_activity["action"] if entry_activity else None,
                "last_action_by": entry_activity["user_name"] if entry_activity else None,
                "last_action_at": entry_activity["timestamp"] if entry_activity else None,
            }
        )
    return {"files": result, "folders": listing["folders"]}


@router.post("/ticker/{ticker}/folder")
def create_subfolder(
    ticker: str, name: str, relative_path: str | None = None, user: dict = Depends(auth.current_user)
):
    """
    Create an empty subfolder inside a ticker's folder — at the root, or
    nested inside an existing subfolder if `relative_path` is given (same
    convention as everywhere else `relative_path` is used). Lets someone
    set up a ticker's structure (e.g. "Old Models") ahead of time, without
    needing a file in hand the way the upload flow's choose_subfolder
    "New folder" option requires.
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name can't be empty.")
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    if relative_path:
        folder = f"{folder}/{relative_path}"
    dropbox_client.create_folder(f"{folder}/{name}")
    return {"status": "created", "name": name}


@router.delete("/ticker/{ticker}/folder")
def delete_subfolder(ticker: str, relative_path: str, user: dict = Depends(auth.current_user)):
    """
    Permanently delete a subfolder — and everything inside it — from a
    ticker's folder. `relative_path` is the subfolder's own path relative
    to the ticker root (e.g. "Old Filings", or "Old Filings/2023" for one
    nested inside that) — same convention as everywhere else
    `relative_path` is used, except here it identifies the folder being
    deleted itself, not a file's containing folder. The frontend is
    expected to confirm with the user before ever calling this — see
    dropbox_client.delete().
    """
    if not relative_path:
        raise HTTPException(status_code=400, detail="relative_path is required.")
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}/{relative_path}"
    dropbox_client.delete(folder)
    return {"status": "deleted", "relative_path": relative_path}


@router.post("/ticker/{ticker}/{filename}/move")
def move_ticker_file(
    ticker: str,
    filename: str,
    target_ticker: str,
    relative_path: str | None = None,
    target_relative_path: str | None = None,
    user: dict = Depends(auth.current_user),
):
    """
    Moves a single file to a different folder — a different subfolder of
    the same ticker (pass the same `target_ticker` as `ticker`), or into a
    different ticker entirely (its root, or one of its own subfolders).
    `relative_path` is the file's current containing subfolder;
    `target_relative_path` is the destination's — same convention as
    everywhere else `relative_path` is used, empty/omitted meaning the
    ticker's own root either way.

    Auto-renames on a naming conflict at the destination rather than
    failing outright (see dropbox_client.move()); the destination
    subfolder is created first if it doesn't already exist, since unlike
    upload, a plain move doesn't create missing parent folders on its own.
    """
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    target_status = known.get(target_ticker)
    if target_status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {target_ticker}")

    from_folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    if relative_path:
        from_folder = f"{from_folder}/{relative_path}"
    to_folder = f"{ticker_registry.folder_path_for_status(target_status)}/{target_ticker}"
    if target_relative_path:
        to_folder = f"{to_folder}/{target_relative_path}"
        dropbox_client.ensure_folder(to_folder)

    dropbox_client.move(f"{from_folder}/{filename}", f"{to_folder}/{filename}")
    # Logged under the destination — same reasoning as rename_ticker
    # logging under the new name: that's what future lookups (the file
    # list, activity captions) will actually query by.
    from_label = f"{ticker}/{relative_path}" if relative_path else ticker
    activity_log.record(
        user, "moved", ticker=target_ticker, filename=filename, relative_path=target_relative_path or "",
        detail=f"from {from_label}",
    )
    return {"status": "moved", "filename": filename, "target_ticker": target_ticker}


@router.delete("/ticker/{ticker}/{filename}")
def delete_ticker_file(
    ticker: str, filename: str, relative_path: str | None = None, user: dict = Depends(auth.current_user)
):
    """Permanently delete a single file from a ticker's folder, or from a
    subfolder inside it if `relative_path` is given (e.g. "Old Models" —
    same convention as everywhere else `relative_path` is used). The
    frontend is expected to confirm with the user before ever calling
    this — see dropbox_client.delete()."""
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    if relative_path:
        folder = f"{folder}/{relative_path}"
    dropbox_client.delete(f"{folder}/{filename}")
    activity_log.record(user, "deleted", ticker=ticker, filename=filename, relative_path=relative_path or "")
    return {"status": "deleted", "filename": filename}


@router.get("/ticker/{ticker}/{filename}/thumbnail")
def get_ticker_file_thumbnail(ticker: str, filename: str, relative_path: str | None = None):
    """
    Best-effort real preview thumbnail for a file inside a ticker/theme
    folder — PDF, Word, PowerPoint-family documents, and actual image
    files get a real JPEG (see thumbnails.get_thumbnail()); everything
    else (notably Excel/CSV, where Dropbox's own preview is raw HTML
    rather than something image-able) returns 404. The frontend treats
    that 404 as "show the plain file-type icon instead," not an error —
    see FileThumbnail.jsx.
    """
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    if relative_path:
        folder = f"{folder}/{relative_path}"
    thumbnail = thumbnails.get_thumbnail(f"{folder}/{filename}", filename)
    if thumbnail is None:
        raise HTTPException(status_code=404, detail="No preview available for this file type")
    return Response(content=thumbnail, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/ticker/{ticker}/{filename}/open-link")
def get_ticker_file_open_link(ticker: str, filename: str, relative_path: str | None = None):
    """A Dropbox web URL that opens this file in Dropbox's own preview UI
    — see dropbox_client.get_shareable_link()."""
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    if relative_path:
        folder = f"{folder}/{relative_path}"
    return {"url": dropbox_client.get_shareable_link(f"{folder}/{filename}")}


@router.delete("/needs-review/{filename}")
def delete_needs_review_file(filename: str, user: dict = Depends(auth.current_user)):
    """Permanently delete a single file from Needs Review."""
    path = f"{settings.dropbox_needs_review_path}/{filename}"
    dropbox_client.delete(path)
    activity_log.record(user, "deleted", ticker=None, filename=filename)
    return {"status": "deleted", "filename": filename}


@router.get("/needs-review/{filename}/thumbnail")
def get_needs_review_thumbnail(filename: str):
    """Same as get_ticker_file_thumbnail() above, for a file still sitting
    in Needs Review rather than filed under a ticker."""
    path = f"{settings.dropbox_needs_review_path}/{filename}"
    thumbnail = thumbnails.get_thumbnail(path, filename)
    if thumbnail is None:
        raise HTTPException(status_code=404, detail="No preview available for this file type")
    return Response(content=thumbnail, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/needs-review/{filename}/open-link")
def get_needs_review_open_link(filename: str):
    """Same as get_ticker_file_open_link() above, for a file still sitting
    in Needs Review rather than filed under a ticker."""
    path = f"{settings.dropbox_needs_review_path}/{filename}"
    return {"url": dropbox_client.get_shareable_link(path)}


@router.post("/needs-review/{filename}/assign")
def assign_needs_review_file(
    filename: str,
    ticker: str,
    target_status: str | None = None,
    force: bool = False,
    skip_category: bool = False,
    user: dict = Depends(auth.current_user),
):
    """
    Manually assign a Needs Review file to a ticker.

    If the typed value contains a `(.SUFFIX)` marker — parentheses
    required, e.g. "MCR (.TO)" — matching a registered theme folder (see
    category_routing.py), it's routed straight into that category folder,
    same as the upload flow, with no ticker matching/confirmation. A bare
    "MCR.TO" (no parens) is NEVER treated as a category candidate here
    either, for the same reason as the upload flow — it's indistinguishable
    from a real ticker's own exchange suffix. `skip_category=true` bypasses
    this check specifically — used when the caller has already been
    offered "create a literal new ticker anyway" (see the new-ticker branch
    below) and explicitly chose that, so a marker match can't silently
    steer that choice back into the theme folder.

    If the typed ticker case-insensitively matches a real existing ticker,
    it's normalized to that ticker's real casing and assigned directly. If
    it doesn't match but has close look-alikes, assignment is held and the
    caller must resubmit with `force=true` to confirm creating/using that
    ticker anyway — the same typo safety net the upload flow gets, since a
    manually-typed ticker is just as easy to fat-finger as a filename.

    If the typed ticker doesn't match anything real and isn't close to
    anything either, it's still a genuinely new ticker — but rather than
    assuming Active (a person manually filing this could just as easily
    be archiving something old, or something that doesn't fit either
    bucket), assignment is held ("new_ticker_needs_status") until the
    caller resubmits with `target_status` set to "active", "inactive", or
    "historicals". Unlike the upload flow's automatic filename parsing, a
    manually-typed ticker gets this treatment even if it's lowercase —
    someone typing it into this box is always a deliberate action, not a
    guess from a sentence.
    """
    known = ticker_registry.get_known_tickers()

    # Real-ticker match is checked first, no exceptions — an established
    # ticker can never be hijacked by category routing. Only once that's
    # ruled out is the `(.SUFFIX)` marker checked on its own (see
    # category_routing.find_category_tag() and upload_file()'s matching
    # comment for the full reasoning).
    resolution = ticker_registry.resolve_ticker(ticker, known)
    if resolution["kind"] == "matched":
        ticker = resolution["ticker"]
        status = known[ticker]
        from_path = f"{settings.dropbox_needs_review_path}/{filename}"
        to_path = f"{ticker_registry.folder_path_for_status(status)}/{ticker}/{filename}"
        dropbox_client.move(from_path, to_path)
        activity_log.record(user, "assigned", ticker=ticker, filename=filename)
        return {"status": "assigned", "ticker": ticker}

    if not skip_category:
        category_folder = category_routing.find_category_tag(ticker, category_routing.get_category_folders())
        if category_folder:
            from_path = f"{settings.dropbox_needs_review_path}/{filename}"
            to_path = f"{category_folder}/{filename}"
            dropbox_client.move(from_path, to_path)
            category_name = category_folder.rsplit("/", 1)[-1]
            activity_log.record(user, "assigned", ticker=category_name, filename=filename)
            return {
                "status": "assigned_category",
                "category_folder": category_name,
            }

    if not force:
        if resolution["kind"] == "confirm_needed":
            return {"status": "confirm_needed", "requested_ticker": ticker, "suggestions": resolution["suggestions"]}
        elif target_status not in ticker_registry.STATUSES:
            return {"status": "new_ticker_needs_status", "requested_ticker": ticker}

    status = known.get(ticker, target_status if target_status in ticker_registry.STATUSES else "active")
    from_path = f"{settings.dropbox_needs_review_path}/{filename}"
    to_path = f"{ticker_registry.folder_path_for_status(status)}/{ticker}/{filename}"
    dropbox_client.move(from_path, to_path)
    activity_log.record(user, "assigned", ticker=ticker, filename=filename)
    return {"status": "assigned", "ticker": ticker}
