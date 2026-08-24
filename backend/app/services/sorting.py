"""
Ticker-sorting logic: given a filename, determine which ticker it belongs
to.

Real naming convention (per the existing Dropbox folder structure):
`TICKER Description.ext` — a ticker prefix, a separator (space, hyphen, or
underscore), then a free-form description. Tickers may carry an exchange
suffix separated by a dot, e.g. `ZRE.TO`, `ASCO.LN`, `CTK.SA`, `NEC.AU`.
Existing tickers aren't always uppercase (e.g. the real `zqrx` folder), so
extraction itself doesn't judge case — callers match case-insensitively
against the real known-ticker list via find_known_ticker()/find_close_matches(),
and decide separately whether an unmatched candidate looks like a
deliberate new ticker (uppercase) or just the first word of an ordinary
sentence (lowercase) before creating a new folder for it.

Anything that doesn't match this shape at all (no separator followed by a
description) returns no ticker, so callers can route the file to Needs
Review instead of guessing.

Deliberately kept free of any Dropbox/API code — everything here just
takes plain strings in and returns plain strings/lists out, which is what
makes it possible to unit-test (see backend/tests/test_sorting.py) without
a real Dropbox connection.
"""

import difflib
import re

# Matches: one or more letters/digits, optionally followed by a dot and
# more letters/digits (the exchange suffix, e.g. ".TO"), then a required
# separator (a space, hyphen, or underscore), then anything at all (the
# free-form description). Group 1 is the ticker candidate we return.
_TICKER_PATTERN = re.compile(r"^([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)?)[ _-].+$")

# Same leading shape, but without requiring a separator+description to
# follow — used only for category-suffix routing (see
# parse_leading_token()), where a file destined for a themed folder
# doesn't need a human-readable description attached at all.
_LEADING_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)?")

# Same shape as a ticker candidate (letters/digits, optional dot suffix),
# but unanchored — used to pull every word-like token out of a filename,
# not just the leading one. See find_ticker_mentioned_anywhere().
_TICKER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)?")

# Matches a trailing "(.SUFFIX)" marker in a FOLDER name, e.g. "Busted
# Biotechs (.BB)" -> captures "BB". Lives here (rather than in
# category_routing.py, which is where it's actually *used* to register a
# suffix) specifically so ticker_registry.py can also import it without a
# circular dependency (category_routing.py already imports
# ticker_registry.py) — see is_category_folder_name().
CATEGORY_FOLDER_MARKER = re.compile(r"\(\.(\w+)\)\s*$")


def is_category_folder_name(name: str) -> bool:
    """
    True if `name` (a Dropbox folder name) ends with a `(.SUFFIX)`
    theme-folder marker — used by ticker_registry.get_known_tickers() to
    exclude theme folders from the "known tickers" list. A theme folder
    was never meant to be treated as a ticker, and leaving it in caused
    real bugs: resolving an unrelated dropped folder's name against it
    could produce a nonsensical "did you mean Toronto Names (.TO)?"
    suggestion for something like "Total Oasis (.TO)", purely because
    both names happen to share the literal "(.TO)" marker text — nothing
    to do with either name actually being similar.
    """
    return bool(CATEGORY_FOLDER_MARKER.search(name))


def _strip_extension(filename: str) -> str:
    """Drop the real file extension (the part after the last dot), unless
    the dot is the very first character (a dotfile like ".DS_Store" has no
    "extension" in the meaningful sense)."""
    return filename.rsplit(".", 1)[0] if "." in filename[1:] else filename


def parse_ticker(filename: str) -> str | None:
    """Return the ticker prefix of a filename, or None if it doesn't match
    the `TICKER Description.ext` convention."""
    match = _TICKER_PATTERN.match(filename)
    if not match:
        return None
    return match.group(1)


def parse_leading_token(filename: str) -> str | None:
    """
    Return the leading ticker-shaped token (letters/digits, optionally a
    dot suffix) from the start of a filename, real extension stripped —
    or None if the filename doesn't even start with a letter/digit (e.g.
    ".DS_Store").

    Unlike parse_ticker(), this does NOT require a separator and
    description to follow — used for the ordinary "does the leading word
    exactly match a real, existing ticker" check, which shouldn't require
    a description any more than parse_ticker() itself conceptually needs
    one (a bare "ZBQ.pdf" is just as much "about ZBQ" as "ZBQ Notes.pdf"
    is, even though only the latter matches parse_ticker()'s stricter
    shape).
    """
    match = _LEADING_TOKEN_PATTERN.match(_strip_extension(filename))
    if not match:
        return None
    return match.group(0)


def find_known_ticker(candidate: str, known_tickers: list[str]) -> str | None:
    """Case-insensitively look up `candidate` against known tickers,
    returning the ticker's real stored casing if found (e.g. matching the
    real `zqrx` folder regardless of how the candidate was typed/cased)."""
    for ticker in known_tickers:
        if ticker.upper() == candidate.upper():
            return ticker
    return None


def tokenize_filename(filename: str) -> list[str]:
    """Split a filename (real extension stripped) into word-like tokens —
    each one shaped like a ticker candidate (letters/digits, optionally
    one dot-suffix glued directly onto it with no space). Shared by
    find_ticker_mentioned_anywhere() and find_ambiguous_uppercase_mentions(),
    which scan the same tokens for two different kinds of match."""
    return _TICKER_TOKEN_PATTERN.findall(_strip_extension(filename))


def find_ticker_mentioned_anywhere(filename: str, known_tickers: list[str]) -> str | None:
    """
    Last-resort fallback for a filename that never resolved to a real
    ticker via its leading word (e.g. "quarterly notes on zbq.pdf" —
    "quarterly" isn't a ticker, but "zbq" clearly is). Scans every
    word-like token in the filename (extension stripped) for an exact,
    case-insensitive match against a ticker that ALREADY EXISTS as a real
    folder.

    Deliberately NOT fuzzy, and deliberately incapable of proposing a
    brand-new ticker — unlike the leading-word check, which only ever
    treats an uppercase leading word as a plausible new ticker. Applying
    that same leniency to every word in a whole sentence would be a much
    bigger false-positive risk, since plenty of real tickers are also
    ordinary English words (e.g. "ALL", "IT", "ON", "AT", "F"). Matching
    only tickers that are already provably real keeps this safe.

    Returns None — deferring to Needs Review, same as if this fallback
    didn't exist — if no real ticker is mentioned, or if more than one
    distinct real ticker is mentioned. A filename mentioning two different
    real tickers is genuinely ambiguous; guessing between them isn't this
    function's job.
    """
    matches = set()
    for token in tokenize_filename(filename):
        found = find_known_ticker(token, known_tickers)
        if found:
            matches.add(found)

    if len(matches) == 1:
        return matches.pop()
    return None


def find_ticker_mentioned_in_text(text: str, known_tickers: list[str]) -> str | None:
    """
    Same contract as find_ticker_mentioned_anywhere() above (a single
    unambiguous real-ticker match, or None) but for arbitrary prose text
    — e.g. an email Subject line — rather than a filename. Deliberately
    NOT built on top of find_ticker_mentioned_anywhere() itself: that
    function runs _strip_extension() first, which blindly splits on the
    last "." in the string. That's correct for a filename (real
    extension), but would silently corrupt an ordinary sentence with a
    period in it — "Notes on ZBQ earnings. Thoughts inside." would get
    truncated to "Notes on ZBQ earnings" before ever being tokenized,
    which happens to still work for that particular example but is the
    wrong operation to be doing to prose at all. This tokenizes the raw
    text directly instead.

    Same safety scope as its filename sibling: only ever matches a
    ticker that's already a real, existing folder, never proposes a new
    one, and returns None (defer to Needs Review) the moment more than
    one distinct real ticker is mentioned — genuinely ambiguous text
    isn't this function's job to resolve.
    """
    matches = set()
    for token in _TICKER_TOKEN_PATTERN.findall(text):
        found = find_known_ticker(token, known_tickers)
        if found:
            matches.add(found)

    if len(matches) == 1:
        return matches.pop()
    return None


def find_ambiguous_uppercase_mentions(filename: str, known_tickers: list[str]) -> list[str]:
    """
    Meant to be tried after find_ticker_mentioned_anywhere() has already
    come back empty (no single unambiguous real ticker mentioned) — checks
    whether the ambiguity is worth actually asking the user about, rather
    than silently deferring to Needs Review the way that function's own
    ambiguous case does.

    Restricted to tokens written in all-caps IN THE FILENAME ITSELF, not
    just ones that case-insensitively match a real ticker — the same
    "uppercase = deliberate" signal used everywhere else in this app (see
    resolve_ticker()'s new-ticker check). The reasoning: a filename that
    visibly, deliberately names two real tickers side by side in capitals
    (e.g. "Comparing ZBQ to ZPAX.pdf") is a strong, human-legible signal
    worth interrupting the upload for. A lowercase collision is far more
    likely to be coincidental — plenty of real tickers are also ordinary
    English words (e.g. "ALL", "IT", "ON") — and prompting for those would
    turn this safety net into a nuisance instead of a help.

    Returns the sorted list of distinct real tickers mentioned via an
    all-caps token, if there are at least two of them (genuinely worth
    asking about). Returns [] if fewer than two — either nothing was
    mentioned in caps, or only one was, which isn't ambiguous on its own
    (it just wasn't find_ticker_mentioned_anywhere()'s sole match because
    something else, case-insensitively, also matched elsewhere).
    """
    matches = set()
    for token in tokenize_filename(filename):
        if not token.isupper():
            continue
        found = find_known_ticker(token, known_tickers)
        if found:
            matches.add(found)
    return sorted(matches) if len(matches) >= 2 else []


def find_close_matches(ticker: str, known_tickers: list[str], limit: int = 5) -> list[str]:
    """Return known tickers that closely resemble `ticker`, case-insensitively,
    for suggesting corrections when a candidate isn't a recognized ticker.

    Uses Python's built-in difflib, which scores textual similarity from 0
    (nothing alike) to 1 (identical) based on shared character sequences —
    e.g. "ZBP" vs "ZBQ" scores high (one letter off), "ZBP" vs "ZPAX"
    scores near zero. `cutoff=0.6` discards anything below that similarity,
    so a genuinely new/unrelated ticker correctly gets no suggestions
    rather than a bad guess. `limit` caps how many suggestions come back
    even if more tickers are tied at the same score (see the the user notes —
    this was raised from 3 to 5 after real testing showed ties were common
    enough to sometimes hide the intended match)."""
    upper_to_real = {known.upper(): known for known in known_tickers}
    close_upper = difflib.get_close_matches(ticker.upper(), list(upper_to_real.keys()), n=limit, cutoff=0.6)
    return [upper_to_real[match] for match in close_upper]
