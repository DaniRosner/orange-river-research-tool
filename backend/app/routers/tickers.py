# Endpoints for listing tickers and moving them between Active/Inactive.
# File-level endpoints (upload, search, per-ticker file listing, Needs
# Review assignment) live in files.py instead — this split roughly matches
# "things about a ticker/folder" vs. "things about an individual file".

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.services import activity_log, auth, category_routing, dropbox_client, ticker_registry

router = APIRouter(prefix="/tickers", tags=["tickers"])


@router.get("/active")
def list_active_tickers():
    """List tickers currently filed under Active/ (each ticker is a subfolder)."""
    entries = dropbox_client.list_folder(settings.dropbox_active_path)
    return [entry["name"] for entry in entries if entry["is_folder"]]


@router.get("/inactive")
def list_inactive_tickers():
    """List tickers currently filed under Inactive/."""
    entries = dropbox_client.list_folder(settings.dropbox_inactive_path)
    return [entry["name"] for entry in entries if entry["is_folder"]]


@router.get("/historicals")
def list_historicals_tickers():
    """List tickers currently filed under Historicals/ — for tickers that
    don't cleanly fit Active or Inactive (per the client)."""
    entries = dropbox_client.list_folder(settings.dropbox_historicals_path)
    return [entry["name"] for entry in entries if entry["is_folder"]]


@router.get("/needs-review")
def list_needs_review():
    """List files that could not be matched to a ticker on upload. These
    are loose files directly in the Needs Review folder, not organized
    into ticker subfolders — that's the whole point of the bucket."""
    entries = dropbox_client.list_folder(settings.dropbox_needs_review_path)
    return [entry["name"] for entry in entries if not entry["is_folder"]]


@router.get("/category-suffix-warnings")
def list_category_suffix_warnings():
    """
    Lists any theme-folder suffix (e.g. ".BB") that's currently claimed by
    more than one folder — see
    category_routing.find_duplicate_category_suffixes() for why this
    matters: normal suffix routing silently resolves a collision like this
    (whichever folder is scanned last wins), so without this check, one of
    the two folders would just permanently stop receiving anything with no
    indication why. Meant to be polled by the frontend and shown as a
    warning banner — this only ever returns something if a real collision
    exists, which shouldn't happen in normal use.
    """
    return category_routing.find_duplicate_category_suffixes()


@router.get("/activity")
def list_ticker_activity():
    """
    Most recent *ticker-level* action (created/renamed/moved/deleted) for
    every known ticker/theme folder, in one batch query — see
    activity_log.py for why this exists (Dropbox's own "modified by" only
    reflects activity done directly in Dropbox, not through this app).
    Meant to be fetched once and merged into the ticker list client-side,
    rather than one request per ticker.
    """
    known = ticker_registry.get_known_folders()
    return activity_log.latest_for_tickers(list(known.keys()))


@router.post("/create")
def create_ticker(ticker: str, target_status: str, user: dict = Depends(auth.current_user)):
    """
    Create an empty ticker folder — no files. Only meaningful use case:
    someone drags in a whole folder with no files inside it at all, and
    still wants the corresponding ticker folder to exist rather than the
    drag being a silent no-op (every other ticker folder in the app gets
    created implicitly as a side effect of a real upload; there's nothing
    to upload here). If `ticker` already exists, this is a harmless no-op
    — dragging an empty folder for an already-real ticker shouldn't move
    it or do anything else surprising.
    """
    if target_status not in ticker_registry.STATUSES:
        raise HTTPException(
            status_code=400, detail=f"target_status must be one of: {', '.join(ticker_registry.STATUSES)}"
        )

    known = ticker_registry.get_known_folders()
    if ticker in known:
        return {"status": "already_exists", "ticker": ticker}

    path = f"{ticker_registry.folder_path_for_status(target_status)}/{ticker}"
    dropbox_client.create_folder(path)
    activity_log.record(user, "created", ticker=ticker, detail=f"status={target_status}")

    result = {"status": "created", "ticker": ticker, "target_status": target_status}
    # See category_routing.check_suffix_collision_for() — this is exactly
    # the shape of case it exists for: a new ticker created with a name
    # that happens to end in a "(.SUFFIX)" marker (e.g. dragging in a
    # folder literally named "Total Oasis (.TO)") can collide with an
    # existing theme folder the moment it's created.
    suffix_warning = category_routing.check_suffix_collision_for(ticker)
    if suffix_warning:
        result["suffix_warning"] = suffix_warning
    return result


@router.get("/resolve")
def resolve_ticker_name(name: str):
    """
    Resolve a candidate ticker name against real known tickers, without
    uploading or moving anything. Used by the frontend before uploading a
    whole dropped *folder's* contents — the folder's name is resolved once
    here, up front, rather than asking the same "is this a real ticker?"
    question once per file inside it. See ticker_registry.resolve_ticker()
    for the possible outcomes.
    """
    known = ticker_registry.get_known_tickers()
    return ticker_registry.resolve_ticker(name, known)


@router.post("/{ticker}/move")
def move_ticker(ticker: str, target_status: str, user: dict = Depends(auth.current_user)):
    """
    Move a ticker's whole folder between Active, Inactive, and Historicals
    in Dropbox.

    `target_status` is read as a query parameter (e.g.
    `POST /tickers/ZBQ/move?target_status=inactive`), not a request body —
    it's a single simple value, so a body felt like overkill.
    """
    if target_status not in ticker_registry.STATUSES:
        raise HTTPException(
            status_code=400, detail=f"target_status must be one of: {', '.join(ticker_registry.STATUSES)}"
        )

    # Look up where the ticker actually lives right now (rather than
    # trusting the frontend to know) so we always move from its real
    # current location, and so a request for a nonexistent ticker fails
    # clearly instead of silently creating a new empty folder.
    known = ticker_registry.get_known_folders()
    current_status = known.get(ticker)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    if current_status == target_status:
        return {"status": "unchanged", "ticker": ticker}

    from_path = f"{ticker_registry.folder_path_for_status(current_status)}/{ticker}"
    to_path = f"{ticker_registry.folder_path_for_status(target_status)}/{ticker}"
    dropbox_client.move(from_path, to_path)
    activity_log.record(user, "moved", ticker=ticker, detail=f"to {target_status}")
    return {"status": "moved", "ticker": ticker, "new_status": target_status}


@router.post("/{ticker}/rename")
def rename_ticker(ticker: str, new_name: str, user: dict = Depends(auth.current_user)):
    """
    Rename a ticker or theme folder in place — same status/parent folder,
    just a different name. Works on theme folders too (see
    ticker_registry.get_known_folders()), specifically so a suffix
    collision between two theme folders (or a ticker that accidentally
    ends up claiming an already-used suffix) can be resolved from inside
    this app — no need to go into Dropbox directly for something this
    simple, e.g. renaming "Toronto Names (.TO)" to "Toronto Names (.TOR)".
    """
    if not new_name or not new_name.strip():
        raise HTTPException(status_code=400, detail="new_name can't be empty")

    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    if new_name in known:
        raise HTTPException(status_code=400, detail=f'"{new_name}" already exists')

    base_path = ticker_registry.folder_path_for_status(status)
    dropbox_client.move(f"{base_path}/{ticker}", f"{base_path}/{new_name}")
    # Logged under the *new* name — that's what future lookups (list
    # views, file activity) will actually query by, since the old name no
    # longer exists as a real folder.
    activity_log.record(user, "renamed", ticker=new_name, detail=f"from {ticker}")
    return {"status": "renamed", "old_name": ticker, "new_name": new_name}


@router.delete("/{ticker}")
def delete_ticker(ticker: str, user: dict = Depends(auth.current_user)):
    """
    Permanently delete an entire ticker's folder — and every file inside
    it — from Dropbox. This is the most destructive endpoint in the app;
    the frontend is expected to make the user explicitly confirm before
    ever calling this (see dropbox_client.delete()'s docstring for the
    same caveat about Dropbox's own recoverability window).
    """
    known = ticker_registry.get_known_folders()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    path = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    dropbox_client.delete(path)
    activity_log.record(user, "deleted", ticker=ticker)
    return {"status": "deleted", "ticker": ticker}
