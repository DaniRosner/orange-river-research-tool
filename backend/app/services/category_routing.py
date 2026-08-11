"""
Category-suffix routing: some non-ticker folders (e.g. `Busted Biotechs`,
`Japan`, `SPAC`) sit inside Active/Inactive/Historicals alongside real
tickers, but aren't tickers themselves — they're thematic buckets. Per the
client, each one can be given a custom suffix (the same idea as an
exchange suffix like `.TO`/`.LN`), by renaming the folder to end with a
`(.SUFFIX)` marker, e.g. `Busted Biotechs (.BB)`. A file destined for that
folder carries the SAME `(.SUFFIX)` marker literally in its own filename,
anywhere in it, e.g. `MCR (.TO) Notes.docx` or `MCR(.TO) Notes.docx` for a
theme folder tagged `(.TO)` — see find_category_tag().

The parenthesized form is deliberate and required: a bare `TICKER.SUFFIX`
(no parens, e.g. `MCR.TO`) is NEVER treated as a category-suffix
candidate, no matter what suffix is registered — that shape is reserved
entirely for a real ticker's own exchange suffix (`ZRE.TO`, `ASCO.LN`,
etc., see sorting.py). Before this distinction existed, the two were
genuinely ambiguous: a brand-new real ticker on some exchange, uploaded
for the first time, had no existing folder to protect it, and could get
silently swept into a theme folder sharing that same exchange code purely
because there was nothing yet to compare it against. Requiring the
parenthesized form for category routing removes that ambiguity
structurally — a plain filename can never accidentally look like a
deliberate theme-folder tag, so there's nothing left to guess or confirm.

Deliberately NOT a config file or database — the folder-side mapping is
discovered purely by scanning folder names each time it's needed. This
means assigning a new suffix to a folder is just renaming it in Dropbox,
which the client can already do himself with no code change and no
separate tool to learn. (A real admin UI backed by Dropbox's
metadata/properties API was considered and rejected for now — see the
"the user open questions" notes for the reasoning; revisit if this becomes a
frequent, high-volume workflow.)
"""

import re

from app.services import dropbox_client, ticker_registry
from app.services.sorting import CATEGORY_FOLDER_MARKER

# Alias kept for readability in this file — same pattern as
# sorting.CATEGORY_FOLDER_MARKER, shared from there specifically so
# ticker_registry.py can also use it without a circular import.
_SUFFIX_MARKER = CATEGORY_FOLDER_MARKER

# Matches a "(.SUFFIX)" marker ANYWHERE in a filename (unanchored, unlike
# CATEGORY_FOLDER_MARKER which only matches a FOLDER name's own trailing
# marker) — this is the file-side half of the same convention. See
# find_category_tag().
_FILE_CATEGORY_TAG_PATTERN = re.compile(r"\(\.(\w+)\)")


def _scan_category_folder_candidates() -> list[tuple[str, str]]:
    """
    Every (suffix, folder_path) pair found by scanning Active/Inactive/
    Historicals for subfolders whose name ends with a `(.SUFFIX)` marker
    — INCLUDING duplicates, if more than one folder happens to claim the
    same suffix (e.g. two folders both named "... (.BB)"). Shared by
    get_category_folders() (which needs one folder per suffix — last one
    scanned wins, same as always) and find_duplicate_category_suffixes()
    (which needs to see every collision instead of that silent
    resolution) so both stay in sync with a single scan.
    """
    candidates: list[tuple[str, str]] = []
    for status in ticker_registry.STATUSES:
        base_path = ticker_registry.folder_path_for_status(status)
        for entry in dropbox_client.list_folder(base_path):
            if not entry["is_folder"]:
                continue
            match = _SUFFIX_MARKER.search(entry["name"])
            if match:
                suffix = f".{match.group(1).upper()}"
                candidates.append((suffix, f"{base_path}/{entry['name']}"))
    return candidates


def get_category_folders() -> dict[str, str]:
    """
    Scan every status folder (Active/Inactive/Historicals) for subfolders
    whose name ends with a `(.SUFFIX)` marker, returning a map of
    uppercase suffix (e.g. ".BB") to that folder's full Dropbox path.

    No filtering based on real tickers happens here — that's unnecessary
    now that category routing only ever triggers on the parenthesized
    `(.SUFFIX)` form (see find_category_tag()), which a real ticker's own
    bare-dot-suffix filename (e.g. "RY.TO") can never match in the first
    place.

    If two different folders both claim the same suffix, whichever is
    scanned last silently wins here — see find_duplicate_category_suffixes()
    for surfacing that instead of leaving it silent.
    """
    mapping: dict[str, str] = {}
    for suffix, path in _scan_category_folder_candidates():
        mapping[suffix] = path
    return mapping


def find_duplicate_category_suffixes() -> dict[str, list[str]]:
    """
    Returns every suffix that's claimed by MORE THAN ONE folder (e.g. two
    folders both named "... (.BB)"), mapped to all the folder paths
    involved. get_category_folders() resolves a collision like this
    silently — whichever folder is scanned last wins, and the other
    becomes permanently unreachable via suffix routing with no
    indication anything's wrong. This is the check that catches it, meant
    to be surfaced somewhere visible (e.g. a warning banner in the UI)
    rather than left to go unnoticed indefinitely.

    Returns {} when there's no collision, which should be the normal case
    — this only ever finds something if a client picks a suffix for a new
    theme folder that's already in use by a different one.
    """
    by_suffix: dict[str, list[str]] = {}
    for suffix, path in _scan_category_folder_candidates():
        by_suffix.setdefault(suffix, []).append(path)
    return {suffix: paths for suffix, paths in by_suffix.items() if len(paths) > 1}


def check_suffix_collision_for(folder_name: str) -> str | None:
    """
    If `folder_name` (a ticker or theme folder someone just uploaded to or
    created) is currently involved in a suffix collision — its name ends
    in a `(.SUFFIX)` marker that's ALSO claimed by a different folder —
    return a human-readable warning message. Returns None otherwise, which
    is the overwhelmingly common case, since most folder names aren't
    suffix-shaped at all.

    Meant to be checked at the exact moment a folder is touched (uploaded
    to, created), so a collision can be surfaced as an immediate pop-up
    right when it matters, rather than relying solely on the passive
    periodic banner (find_duplicate_category_suffixes(), checked
    app-wide) to eventually catch it.
    """
    match = CATEGORY_FOLDER_MARKER.search(folder_name)
    if not match:
        return None
    suffix = f".{match.group(1).upper()}"
    duplicates = find_duplicate_category_suffixes()
    if suffix not in duplicates:
        return None
    others = [path.rsplit("/", 1)[-1] for path in duplicates[suffix] if path.rsplit("/", 1)[-1] != folder_name]
    return (
        f'"{folder_name}" shares the suffix "{suffix}" with {", ".join(others)} — '
        "only one of them will actually receive files. Rename one of them to fix this."
    )


def find_category_tag(filename: str, category_folders: dict[str, str]) -> str | None:
    """
    Scans the WHOLE filename for a literal "(.SUFFIX)" marker — anywhere
    in it, not just at the front — matching one of the registered category
    folders. Returns that folder's full Dropbox path, or None if no
    registered marker appears anywhere.

    This is the ONLY thing that triggers category routing. A bare
    "TICKER.SUFFIX" with no parens (e.g. "MCR.TO") never matches here, no
    matter what suffix is registered — see this module's docstring for why
    that's deliberate: the parenthesized form is something a real ticker's
    filename can basically never contain by coincidence, unlike a bare
    dot-suffix, which is indistinguishable from a genuine exchange suffix.
    That's what makes this check safe to run unconditionally, with no
    typo-guardrail or "suspicious extension" ambiguity check needed in
    front of it the way the old bare-suffix version required — there's
    nothing left to disambiguate.

    Only ever matches when exactly one DISTINCT theme folder's marker is
    found — a filename carrying two different theme folders' tags (e.g.
    "Compare (.BB) and (.TO) names.pdf") is genuinely ambiguous, not this
    function's job to guess between.
    """
    matched_paths = {
        category_folders[f".{suffix.upper()}"]
        for suffix in _FILE_CATEGORY_TAG_PATTERN.findall(filename)
        if f".{suffix.upper()}" in category_folders
    }
    return matched_paths.pop() if len(matched_paths) == 1 else None
