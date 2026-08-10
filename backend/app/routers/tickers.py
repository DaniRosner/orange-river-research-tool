# Endpoints for listing tickers and moving them between Active/Inactive.
# File-level endpoints (upload, search, per-ticker file listing, Needs
# Review assignment) live in files.py instead — this split roughly matches
# "things about a ticker/folder" vs. "things about an individual file".

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services import dropbox_client, ticker_registry

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
def move_ticker(ticker: str, target_status: str):
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
    known = ticker_registry.get_known_tickers()
    current_status = known.get(ticker)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    if current_status == target_status:
        return {"status": "unchanged", "ticker": ticker}

    from_path = f"{ticker_registry.folder_path_for_status(current_status)}/{ticker}"
    to_path = f"{ticker_registry.folder_path_for_status(target_status)}/{ticker}"
    dropbox_client.move(from_path, to_path)
    return {"status": "moved", "ticker": ticker, "new_status": target_status}


@router.delete("/{ticker}")
def delete_ticker(ticker: str):
    """
    Permanently delete an entire ticker's folder — and every file inside
    it — from Dropbox. This is the most destructive endpoint in the app;
    the frontend is expected to make the user explicitly confirm before
    ever calling this (see dropbox_client.delete()'s docstring for the
    same caveat about Dropbox's own recoverability window).
    """
    known = ticker_registry.get_known_tickers()
    status = known.get(ticker)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    path = f"{ticker_registry.folder_path_for_status(status)}/{ticker}"
    dropbox_client.delete(path)
    return {"status": "deleted", "ticker": ticker}
