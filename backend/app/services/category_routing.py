"""
Category-suffix routing: some non-ticker folders (e.g. `Busted Biotechs`,
`Japan`, `SPAC`) sit inside Active/Inactive/Historicals alongside real
tickers, but aren't tickers themselves — they're thematic buckets. Per the
client, each one can be given a custom suffix (the same idea as an
exchange suffix like `.TO`/`.LN`), by renaming the folder to end with a
`(.SUFFIX)` marker, e.g. `Busted Biotechs (.BB)`. An upload whose ticker
carries that suffix (e.g. `ZBQ.BB Notes.docx`) then files directly into
that folder instead of becoming/joining a literal ticker called `ZBQ.BB`.

Deliberately NOT a config file or database — the mapping is discovered
purely by scanning folder names each time it's needed. This means
assigning a new suffix to a folder is just renaming it in Dropbox, which
the client can already do himself with no code change and no separate
tool to learn. (A real admin UI backed by Dropbox's metadata/properties
API was considered and rejected for now — see the "the user open questions"
notes for the reasoning; revisit if this becomes a frequent, high-volume
workflow.)
"""

import re

from app.services import dropbox_client, ticker_registry
from app.services.sorting import find_known_ticker, tokenize_filename

# Matches a trailing "(.SUFFIX)" marker in a folder name, e.g. "Busted
# Biotechs (.BB)" -> captures "BB". Requires at least one word character
# inside the parens so an empty "()" or stray "(.)" doesn't match.
_SUFFIX_MARKER = re.compile(r"\(\.(\w+)\)\s*$")


def get_category_folders() -> dict[str, str]:
    """
    Scan every status folder (Active/Inactive/Historicals) for subfolders
    whose name ends with a `(.SUFFIX)` marker, returning a map of
    uppercase suffix (e.g. ".BB") to that folder's full Dropbox path.

    Deliberately does no filtering based on real tickers — a real exchange
    suffix (e.g. ".TO") and a category suffix can look identical, but
    protecting an *established* ticker from ever being hijacked by this is
    the caller's job: check whether the exact candidate is already a real
    ticker BEFORE calling split_category_suffix() at all, and skip this
    entirely if so (see files.py). That per-candidate check is enough on
    its own — it protects every real ticker that could ever collide,
    without also blocking *other*, unrelated tickers that happen to share
    the same suffix (e.g. a real ".TO" category folder should still be
    usable for "RY.TO" even though "ZRE.TO" already exists as its own
    real ticker).
    """
    mapping: dict[str, str] = {}
    for status in ticker_registry.STATUSES:
        base_path = ticker_registry.folder_path_for_status(status)
        for entry in dropbox_client.list_folder(base_path):
            if not entry["is_folder"]:
                continue
            match = _SUFFIX_MARKER.search(entry["name"])
            if match:
                suffix = f".{match.group(1).upper()}"
                mapping[suffix] = f"{base_path}/{entry['name']}"

    return mapping


def split_category_suffix(candidate: str, category_folders: dict[str, str]) -> tuple[str, str] | None:
    """
    If `candidate` (a parsed ticker-like string, e.g. "ZBQ.BB") ends with a
    registered category suffix, return (base_ticker, folder_path) — e.g.
    ("ZBQ", ".../Active/Busted Biotechs (.BB)"). Returns None if it
    doesn't match any registered suffix, meaning this isn't category
    routing and normal ticker resolution should proceed instead.

    Checked case-insensitively, consistent with how ticker matching works
    everywhere else in the app.
    """
    upper = candidate.upper()
    for suffix, folder_path in category_folders.items():
        if upper.endswith(suffix) and len(candidate) > len(suffix):
            return candidate[: -len(suffix)], folder_path
    return None


# Real file types this app expects to see as an actual extension. Used
# only by has_suspicious_extension() below, to tell "this file's
# extension is just unusual" apart from "this file's extension slot is
# actually an attempted category suffix" — not an exhaustive list of
# every real file type, just enough to cover ordinary research files.
_KNOWN_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "xlsm", "csv", "ppt", "pptx",
    "txt", "rtf", "msg", "eml", "png", "jpg", "jpeg", "gif", "zip",
    "key", "pages", "numbers", "mp3", "mp4", "mov", "wav", "m4a",
}  # fmt: skip


def has_suspicious_extension(filename: str, category_folders: dict[str, str]) -> bool:
    """
    True if filename's real trailing dot-segment (whatever's after the
    last dot) ISN'T a recognized file extension, but DOES exactly match a
    registered category suffix — e.g. "ZBQ Q3 Numbers.BB", where ".BB"
    sits in the position a real extension normally would, but isn't one.

    This is genuinely ambiguous, not confidently resolvable either way:
    it might be an attempted category-suffix tag that ended up in the
    wrong spot (should route to that theme folder), or it might just be a
    file with a missing/unusual real extension that coincidentally
    matches a suffix (should be filed normally, ".BB" ignored). Callers
    should send this to Needs Review rather than guessing — deliberately
    overriding even an otherwise-exact real-ticker match on the leading
    word, since that match doesn't actually resolve what the trailing
    segment means; see upload_file()'s use of this, checked before
    anything else.
    """
    if "." not in filename[1:]:
        return False
    trailing = filename.rsplit(".", 1)[-1]
    if trailing.lower() in _KNOWN_EXTENSIONS:
        return False
    return f".{trailing.upper()}" in category_folders


def find_category_suffix_mentioned_anywhere(
    filename: str, known_tickers: list[str], category_folders: dict[str, str]
) -> tuple[str, str] | None:
    """
    Companion to sorting.find_ticker_mentioned_anywhere(), but for a
    category suffix instead of a real ticker — and, unlike that function,
    used as the PRIMARY category-suffix check in upload_file(), not just a
    last-resort fallback. Scanning the whole filename rather than just the
    leading word is what correctly covers every shape a suffix can show
    up in: glued to a leading ticker-like word with a description
    following ("ZBQ.BB Notes.docx"), glued to a leading word with nothing
    else following at all ("ZBQ.BB.pdf"), sitting mid-sentence ("Key KPIs
    for ART.BB Q3.pdf"), or tacked onto the very end right before the
    extension ("my quality companies.BB.pdf") — one check handles all of
    these instead of needing a separate leading-word-only fast path.

    Scans the same word-like tokens as find_ticker_mentioned_anywhere(),
    but only considers ones containing a dot (a plain word can never be a
    suffix match) and skips any token that's itself an exact match for a
    real, existing ticker — that's find_ticker_mentioned_anywhere()'s
    territory instead, protecting e.g. a real "ZBQ.BB" ticker from ever
    being treated as a category-suffix candidate. This check is done
    internally per token, so it holds regardless of whether the caller
    happens to run this before or after find_ticker_mentioned_anywhere().

    Only ever acts when exactly one distinct category folder is matched
    across the whole filename — same reasoning as
    find_ticker_mentioned_anywhere(): a filename that mentions two
    different theme folders (e.g. "XYZ.BB and ABC.TO comparison.pdf") is
    genuinely ambiguous, not this function's job to guess between, even
    if one of them happens to sit at the very front.
    """
    matches: dict[str, tuple[str, str]] = {}
    for token in tokenize_filename(filename):
        if "." not in token:
            continue
        if find_known_ticker(token, known_tickers):
            continue
        result = split_category_suffix(token, category_folders)
        if result:
            matches[result[1]] = result

    if len(matches) == 1:
        return next(iter(matches.values()))
    return None
