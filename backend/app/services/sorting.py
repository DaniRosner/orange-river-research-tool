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
    description to follow. It exists specifically for category-suffix
    routing (see category_routing.split_category_suffix()): a file headed
    for a themed folder doesn't need its own free-form description the
    way a real ticker's research notes do — "ZBQ.BB.pdf" should route
    into the ".BB" folder just as readily as "ZBQ.BB Notes.pdf" does,
    even though the former has no separator/description at all and would
    fail parse_ticker() entirely.
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
    find_ticker_mentioned_anywhere() and
    category_routing.find_category_suffix_mentioned_anywhere(), which scan
    the same tokens for two different kinds of match."""
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


def find_close_suffixed_ticker_matches(candidate: str, known_tickers: list[str], limit: int = 5) -> list[str]:
    """
    Typo guardrail for a suffixed candidate (e.g. "KBS.BB") that's about
    to be routed into a category folder via its suffix. Before that
    happens, check whether it's actually a near-miss typo of a DIFFERENT
    real ticker that carries the exact same suffix (e.g. the real ticker
    "ZBQ.BB") — comparing only the base portion, with the shared suffix
    stripped off both sides first, so a suffix common to both strings
    can't artificially inflate how similar they look (e.g. "AB.BB" vs
    "XY.BB" would otherwise share ".BB" and look closer than "AB" and
    "XY" actually are on their own).

    Deliberately scoped to ONLY real tickers sharing this exact suffix —
    comparing against every real ticker generally would misfire on the
    ordinary, intended case of routing an unrelated real ticker's file
    into a themed folder on purpose (e.g. "MRNA.BB" shouldn't get flagged
    as "did you mean MRNA?" just because MRNA is a real ticker elsewhere;
    it has nothing to do with whatever suffix-sharing ticker this checks
    against).

    Returns [] if the candidate has no dot suffix at all, or no real
    ticker happens to share that exact suffix.
    """
    if "." not in candidate:
        return []

    candidate_suffix = f".{candidate.rsplit('.', 1)[-1]}"
    candidate_base = candidate[: -len(candidate_suffix)]

    same_suffix_bases: dict[str, str] = {}
    for ticker in known_tickers:
        if "." not in ticker:
            continue
        ticker_suffix = f".{ticker.rsplit('.', 1)[-1]}"
        if ticker_suffix.upper() != candidate_suffix.upper():
            continue
        same_suffix_bases[ticker[: -len(ticker_suffix)]] = ticker

    close_bases = find_close_matches(candidate_base, list(same_suffix_bases.keys()), limit=limit)
    return [same_suffix_bases[base] for base in close_bases]


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
