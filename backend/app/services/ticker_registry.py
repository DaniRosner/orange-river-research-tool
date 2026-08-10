"""
Ticker registry: maps every ticker that currently exists in Dropbox to its
status ('active', 'inactive', or 'historicals'), by combining all three
folder listings. Shared by the upload, ticker-detail, and move endpoints so
they all agree on what counts as a "known" ticker and where it currently
lives.
"""

from app.config import settings
from app.services import dropbox_client
from app.services.sorting import find_close_matches, find_known_ticker, is_category_folder_name

# Every valid status, and where each one's folder lives. The order here is
# also the scan order in get_known_tickers() — if a ticker somehow exists
# in more than one (see the OEC note below), whichever is scanned last
# wins.
STATUSES = ("active", "inactive", "historicals")


def _path_for_status(status: str) -> str:
    return {
        "active": settings.dropbox_active_path,
        "inactive": settings.dropbox_inactive_path,
        "historicals": settings.dropbox_historicals_path,
    }[status]


def get_known_folders() -> dict[str, str]:
    """
    Map EVERY existing folder — both real tickers AND theme folders like
    "Busted Biotechs (.BB)" — to its current status. Use this (not
    get_known_tickers() below) for "find and manage a specific,
    already-known folder" purposes: listing its files, deleting it,
    moving it, renaming it. A theme folder is just as legitimate a target
    for those as a real ticker is — someone can reasonably click into one
    from the tab list and expect it to work.

    KNOWN LIMITATION: if a folder exists in more than one status folder at
    once (this has actually happened in the real data — see the `OEC`
    duplicate noted for the client, currently in both Active and
    Inactive), this dict can only hold one status per name, and whichever
    status is scanned last (per STATUSES order above) silently wins.
    That's a real gap, not a deliberate design choice — flagged rather
    than fixed because whether that duplicate is even a mistake is itself
    a judgment call for the client.
    """
    folders: dict[str, str] = {}
    for status in STATUSES:
        for entry in dropbox_client.list_folder(_path_for_status(status)):
            if entry["is_folder"]:
                folders[entry["name"]] = status
    return folders


def get_known_tickers() -> dict[str, str]:
    """
    Same as get_known_folders(), but with theme folders (e.g. "Busted
    Biotechs (.BB)") excluded. Use this for ticker-MATCHING purposes
    specifically — typo-checking, resolving a candidate ticker/folder
    name — where a theme folder being included caused real bugs: a
    typo-check comparing an unrelated candidate against a theme folder's
    full name could produce a nonsensical suggestion just because both
    happened to share the same "(.SUFFIX)" marker text, not because the
    names were actually similar.
    """
    return {name: status for name, status in get_known_folders().items() if not is_category_folder_name(name)}


def folder_path_for_status(status: str) -> str:
    """Return the Dropbox folder path for a given status ('active',
    'inactive', or 'historicals')."""
    return _path_for_status(status)


def resolve_ticker(candidate: str, known_tickers: dict[str, str]) -> dict:
    """
    Decide what a candidate ticker name should resolve to, without
    touching Dropbox. Shared by the file-upload flow (resolving a ticker
    parsed from a filename) and the `/tickers/resolve` endpoint (resolving
    the name of a folder someone just dragged in), so both go through
    identical matching logic and never disagree with each other.

    Returns one of:
    - {"kind": "matched", "ticker": <real stored ticker>, "status": one of STATUSES}
      — a real, existing ticker (case-insensitively).
    - {"kind": "confirm_needed", "suggestions": [...]}
      — not a real ticker, but close enough to real one(s) that it's
      probably a typo; ask which (if any) was meant.
    - {"kind": "new_ticker_needs_status", "ticker": candidate}
      — doesn't match anything and isn't close to anything, but looks
      like a deliberate ticker (uppercase). This is a genuinely new
      ticker — the caller must ask which of the three statuses it
      belongs in rather than assuming.
    - {"kind": "not_a_ticker"}
      — doesn't match, isn't close, and isn't uppercase — more likely the
      start of an ordinary sentence than a deliberate new ticker.
    """
    matched = find_known_ticker(candidate, list(known_tickers.keys()))
    if matched:
        return {"kind": "matched", "ticker": matched, "status": known_tickers[matched]}

    suggestions = find_close_matches(candidate, list(known_tickers.keys()))
    if suggestions:
        return {"kind": "confirm_needed", "suggestions": suggestions}

    if candidate.isupper():
        return {"kind": "new_ticker_needs_status", "ticker": candidate}

    return {"kind": "not_a_ticker"}
