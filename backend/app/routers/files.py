# Endpoints for individual files: upload, search, per-ticker file listing,
# and Needs Review assignment. Ticker-folder-level endpoints (list
# Active/Inactive, move a ticker) live in tickers.py instead.
#
# The upload endpoint is the most complex thing in this app — it can pause
# mid-request and ask the caller (the frontend) a question before it'll
# actually file anything, rather than always either succeeding or failing
# outright. See its docstring below for the full decision tree.

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.config import settings
from app.services import category_routing, dropbox_client, ticker_registry
from app.services.sorting import (
    find_ambiguous_uppercase_mentions,
    find_close_suffixed_ticker_matches,
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


def _resolve_upload(
    folder: str,
    filename: str,
    content: bytes,
    on_duplicate: str | None,
    success_status: str,
    extra: dict,
) -> dict:
    """
    Check `folder` for an existing file named `filename` before uploading.

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
    return {"status": success_status, "filename": actual_filename, **extra}


def _fallback_to_needs_review_or_mention(
    filename: str, content: bytes, on_duplicate: str | None, known: dict[str, str], reason: str
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
        settings.dropbox_needs_review_path, filename, content, on_duplicate, "needs_review", {"reason": reason}
    )


def _category_alternative_for(candidate: str) -> dict:
    """
    When the suffix-typo guardrail (find_close_suffixed_ticker_matches)
    holds a candidate like "KBS.BB" for confirmation, it's not the only
    real possibility — "KBS.BB" also carries a suffix that matches a
    theme folder, so filing it there instead of correcting the typo is
    just as plausible. This computes that alternative (if there is one)
    so the caller can offer it as a third option alongside the typo
    suggestion(s) and "create a new ticker," rather than only ever
    presenting the typo correction. Returns `{}` (nothing extra) if the
    candidate's suffix doesn't match a registered theme folder.
    """
    category_match = category_routing.split_category_suffix(candidate, category_routing.get_category_folders())
    if not category_match:
        return {}
    base, category_folder = category_match
    return {"category_folder": category_folder.rsplit("/", 1)[-1], "category_base": base}


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    override_ticker: str | None = Form(None),
    target_status: str | None = Form(None),
    on_duplicate: str | None = Form(None),
    relative_path: str | None = Form(None),
    confirm_category: bool = Form(False),
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
    than flattened.

    `confirm_category`, if provided instead, is a resubmission confirming
    the OTHER kind of answer a `confirm_needed` response can offer: when
    it includes a `category_folder` (see step 2 below), the caller can
    resubmit with this set to true to file into that theme folder instead
    of correcting the typo or creating a new ticker.

    Otherwise, in order:

    1. Does the filename's leading token (letters/digits plus an optional
       dot suffix, extracted WITHOUT requiring a separator+description to
       follow) exactly match a real, already-existing ticker? -> filed
       there. Checked first, no exceptions — an established ticker can
       never be hijacked by category routing.
    2. Not that, but is the leading token a likely typo of a DIFFERENT
       real ticker sharing its exact suffix (e.g. "KBS.BB" vs the real
       "ZBQ.BB")? -> filing is held ("confirm_needed") rather than
       silently swallowed by category routing. If the candidate's suffix
       ALSO matches a theme folder (it usually will, since that's the
       whole reason this check exists), the response includes
       `category_folder` too — the caller can offer three choices: accept
       a suggestion (`override_ticker`), file into the theme folder
       (`confirm_category`), or create a new ticker for what was typed
       (`override_ticker` + `target_status`). See
       find_close_suffixed_ticker_matches() for why this is scoped to
       same-suffix tickers only.
    3. Not that, but is a category suffix matched ANYWHERE in the
       filename — not just the leading token — that's registered to a
       theme folder (e.g. `.BB` for `Busted Biotechs (.BB)`, set up by
       renaming a non-ticker folder — see category_routing.py)? -> filed
       directly into that folder, no further ticker matching/confirmation.
       Covers the deliberate "TICKER.SUFFIX Description.ext" shape
       (doesn't even require a separator/description — "ZBQ.BB.pdf"
       matches just as readily as "ZBQ.BB Notes.pdf") as well as a suffix
       mentioned mid-sentence or tacked onto the very end. Only acts when
       exactly one distinct theme folder is matched anywhere in the name —
       a filename mentioning two different theme folders is genuinely
       ambiguous and falls through instead of picking whichever happened
       to come first. See
       category_routing.find_category_suffix_mentioned_anywhere().
    4. Does a real, already-existing ticker show up unambiguously
       ANYWHERE in the filename — not just the leading word (e.g. "zbq"
       in "quarterly notes on zbq.pdf", or "ZRE.TO" in "Notes on ZRE.TO
       for review.pdf")? -> filed there. Checked here, deliberately
       BEFORE steps 6–7 below — an exact mention elsewhere is a stronger
       signal than a fuzzy typo-guess about the leading word alone, which
       is what stops an ordinary phrase's leading word (e.g. "Notes")
       from being mistaken for a typo of some unrelated real ticker
       before a real mention elsewhere ever gets a chance. Only acts when
       exactly one real ticker is mentioned — two is genuinely ambiguous,
       see step 8. See find_ticker_mentioned_anywhere().
    5. Filename doesn't have the `TICKER Description.ext` shape at all
       (no space/hyphen/underscore-separated description) -> skip to
       step 8.
    6. Parsed ticker doesn't match an existing ticker, but looks like a
       typo of a real one (checked against every real ticker generally,
       not suffix-scoped this time) -> filing is held; the caller must
       resubmit with `override_ticker` set to the confirmed/corrected
       ticker.
    7. Parsed ticker doesn't match, has no close look-alikes, and looks
       like a deliberate ticker (uppercase): this is a genuinely new
       ticker. Rather than assuming it belongs in Active, filing is held
       ("new_ticker_needs_status") until the caller resubmits with
       `override_ticker` set to that same ticker and `target_status` set
       to "active", "inactive", or "historicals" — the file might just as
       easily be someone archiving notes for a company that's already
       inactive, or one that doesn't fit either bucket cleanly.
    8. Last resort, reached either because step 5 found no pattern at all
       or because the step-6/7 checks came up empty: was step 4's mention
       check actually ambiguous (two-plus real tickers mentioned), and
       was that ambiguity written in all-caps (e.g. "Comparing ZBQ to
       ZPAX.pdf")? -> filing is held ("ambiguous_mention") until the
       caller resubmits with `override_ticker` set to whichever one was
       meant. Scoped to all-caps mentions specifically — a lowercase
       collision is far more likely to be coincidental prose than a
       deliberate reference. See find_ambiguous_uppercase_mentions().
    9. Nothing above resolved it -> Needs Review.

    If a file with the same name already exists wherever this one is
    about to be filed, filing is held ("duplicate_needs_confirmation")
    until the caller resubmits with `on_duplicate` set to "replace" or
    "keep_both" — see `_resolve_upload`.
    """
    content = await file.read()

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
        status = known.get(
            override_ticker, target_status if target_status in ticker_registry.STATUSES else "active"
        )
        folder = f"{ticker_registry.folder_path_for_status(status)}/{override_ticker}"
        if relative_path:
            folder = f"{folder}/{relative_path}"
        return _resolve_upload(folder, file.filename, content, on_duplicate, "filed", {"ticker": override_ticker})

    if confirm_category:
        # Resubmission confirming the theme-folder option offered
        # alongside a suffix-typo suggestion (see step 2's confirm_needed
        # response) — re-derive the same leading token and file straight
        # into whatever theme folder its suffix matches, same as it would
        # have without the typo guardrail catching it first.
        leading_token = parse_leading_token(file.filename)
        category_match = leading_token and category_routing.split_category_suffix(
            leading_token, category_routing.get_category_folders()
        )
        if category_match:
            base_ticker, category_folder = category_match
            category_name = category_folder.rsplit("/", 1)[-1]
            return _resolve_upload(
                category_folder,
                file.filename,
                content,
                on_duplicate,
                "filed_category",
                {"ticker": base_ticker, "category_folder": category_name},
            )

    # Checked before anything else: does this filename's real extension
    # slot hold something that isn't a real file type, but IS a
    # registered category suffix (e.g. "ZBQ Q3 Numbers.BB")? That's
    # genuinely ambiguous — it might be an attempted suffix tag in the
    # wrong position, or just an unusual/missing extension — so it goes
    # straight to Needs Review, bypassing every other check below
    # (including the mention-anywhere fallbacks — deliberately, since
    # "ZBQ" would otherwise still get rediscovered there and this check
    # would accomplish nothing) rather than letting whatever the leading
    # word happens to be (even a real ticker like "ZBQ" here) silently
    # win while discarding the ".BB". See
    # category_routing.has_suspicious_extension() for the full reasoning.
    if category_routing.has_suspicious_extension(file.filename, category_routing.get_category_folders()):
        return _resolve_upload(
            settings.dropbox_needs_review_path,
            file.filename,
            content,
            on_duplicate,
            "needs_review",
            {
                "reason": "filename's extension isn't a recognized file type, and matches a registered category "
                "suffix instead — unclear whether that's deliberate"
            },
        )

    # The leading token (letters/digits plus an optional dot suffix,
    # extracted WITHOUT requiring a separator+description to follow — see
    # parse_leading_token()) gets two checks before anything else below:
    #
    # 1. Does it exactly match a real, already-existing ticker? Checked
    #    first, no exceptions — an established ticker can never be
    #    hijacked by category routing, no matter what suffix it carries.
    # 2. If not, is it a likely typo of a DIFFERENT real ticker that
    #    shares its exact suffix (e.g. "KBS.BB" vs the real "ZBQ.BB")?
    #    Held for confirmation rather than silently swallowed by category
    #    routing — see find_close_suffixed_ticker_matches() for why this
    #    is scoped to same-suffix tickers only (checking against every
    #    real ticker generally would misfire on the ordinary, intended
    #    case of routing an unrelated real ticker into a themed folder on
    #    purpose, e.g. "MRNA.BB").
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
            if relative_path:
                folder = f"{folder}/{relative_path}"
            return _resolve_upload(folder, file.filename, content, on_duplicate, "filed", {"ticker": found})

        close_suffixed = find_close_suffixed_ticker_matches(leading_token, list(known.keys()))
        if close_suffixed:
            return {
                "status": "confirm_needed",
                "parsed_ticker": leading_token,
                "suggestions": close_suffixed,
                **_category_alternative_for(leading_token),
            }

    # Category-suffix routing is checked next, scanning the WHOLE filename
    # (not just the leading token) for a suffix match — this is the same
    # function used later as a last-resort fallback for a real ticker
    # mention (find_ticker_mentioned_anywhere), applied here to suffixes
    # instead, and checked this early specifically so the common,
    # deliberate "TICKER.SUFFIX Description.ext" shape still resolves
    # immediately without waiting on every other check to fail first.
    # Scanning the whole filename rather than just the leading token means
    # a filename that mentions TWO different theme folders (e.g. "XYZ.BB
    # and ABC.TO comparison.pdf") is correctly treated as ambiguous and
    # falls through instead of the front one winning just because it
    # happened to be first — same "only act when unambiguous" reasoning as
    # every other mention-based match in this app.
    category_match = category_routing.find_category_suffix_mentioned_anywhere(
        file.filename, list(known.keys()), category_routing.get_category_folders()
    )
    if category_match:
        base_ticker, category_folder = category_match
        category_name = category_folder.rsplit("/", 1)[-1]
        return _resolve_upload(
            category_folder,
            file.filename,
            content,
            on_duplicate,
            "filed_category",
            {"ticker": base_ticker, "category_folder": category_name},
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
        return _resolve_upload(folder, file.filename, content, on_duplicate, "filed_mentioned", {"ticker": mentioned})

    ticker = parse_ticker(file.filename)

    if ticker is None:
        return _fallback_to_needs_review_or_mention(
            file.filename, content, on_duplicate, known, "filename doesn't match the 'TICKER Description.ext' pattern"
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
    rather than silently hiding everything inside them — see
    dropbox_client.list_folder_recursive().
    """
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    folder = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    entries = dropbox_client.list_folder_recursive(folder)
    return [{"name": entry["name"], "relative_path": entry["relative_path"]} for entry in entries]


@router.delete("/ticker/{ticker}/{filename}")
def delete_ticker_file(ticker: str, filename: str, relative_path: str | None = None):
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
    return {"status": "deleted", "filename": filename}


@router.delete("/needs-review/{filename}")
def delete_needs_review_file(filename: str):
    """Permanently delete a single file from Needs Review."""
    path = f"{settings.dropbox_needs_review_path}/{filename}"
    dropbox_client.delete(path)
    return {"status": "deleted", "filename": filename}


@router.post("/needs-review/{filename}/assign")
def assign_needs_review_file(
    filename: str, ticker: str, target_status: str | None = None, force: bool = False, skip_category: bool = False
):
    """
    Manually assign a Needs Review file to a ticker.

    If the typed value ends with a registered category suffix (see
    category_routing.py), it's routed straight into that category folder
    — same as the upload flow — with no ticker matching/confirmation.
    `skip_category=true` bypasses this check specifically — used when the
    caller has already been offered this as one option (alongside a typo
    suggestion and "create a new ticker anyway" — see the suffix-typo
    guardrail below) and explicitly chose to create a literal new ticker
    instead, so a suffix match can't silently steer that choice back into
    the theme folder.

    If the typed ticker case-insensitively matches a real existing ticker,
    it's normalized to that ticker's real casing and assigned directly. If
    it doesn't match but has close look-alikes, assignment is held and the
    caller must resubmit with `force=true` to confirm creating/using that
    ticker anyway — the same typo safety net the upload flow gets, since a
    manually-typed ticker is just as easy to fat-finger as a filename. If
    the candidate also carries a suffix matching a theme folder, the
    response includes `category_folder` too, so the caller can offer that
    as a third choice alongside the typo suggestion(s) and "create new."

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
    # ruled out is the suffix checked on its own, free to match regardless
    # of what other tickers might share it (see category_routing.py and
    # upload_file()'s matching comment for the full reasoning).
    resolution = ticker_registry.resolve_ticker(ticker, known)
    if resolution["kind"] == "matched":
        ticker = resolution["ticker"]
        status = known[ticker]
        from_path = f"{settings.dropbox_needs_review_path}/{filename}"
        to_path = f"{ticker_registry.folder_path_for_status(status)}/{ticker}/{filename}"
        dropbox_client.move(from_path, to_path)
        return {"status": "assigned", "ticker": ticker}

    # Typo guardrail for a suffixed ticker (e.g. typing "KBS.BB" when the
    # real ticker is "ZBQ.BB") — same reasoning as upload_file(), and
    # skippable with force=true just like the general typo/new-ticker
    # checks below.
    if not force:
        close_suffixed = find_close_suffixed_ticker_matches(ticker, list(known.keys()))
        if close_suffixed:
            return {
                "status": "confirm_needed",
                "requested_ticker": ticker,
                "suggestions": close_suffixed,
                **_category_alternative_for(ticker),
            }

    if not skip_category:
        category_match = category_routing.split_category_suffix(ticker, category_routing.get_category_folders())
        if category_match:
            base_ticker, category_folder = category_match
            from_path = f"{settings.dropbox_needs_review_path}/{filename}"
            to_path = f"{category_folder}/{filename}"
            dropbox_client.move(from_path, to_path)
            return {
                "status": "assigned_category",
                "ticker": base_ticker,
                "category_folder": category_folder.rsplit("/", 1)[-1],
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
    return {"status": "assigned", "ticker": ticker}
