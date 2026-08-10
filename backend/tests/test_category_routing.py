from app.services.category_routing import find_category_suffix_mentioned_anywhere, split_category_suffix

_CATEGORY_FOLDERS = {".BB": "/Active/Busted Biotechs (.BB)", ".TO": "/Active/Toronto Names (.TO)"}


def test_split_category_suffix_matches_and_strips():
    assert split_category_suffix("ZBQ.BB", _CATEGORY_FOLDERS) == ("ZBQ", "/Active/Busted Biotechs (.BB)")


def test_split_category_suffix_is_case_insensitive():
    assert split_category_suffix("zbq.bb", _CATEGORY_FOLDERS) == ("zbq", "/Active/Busted Biotechs (.BB)")


def test_split_category_suffix_returns_none_when_no_suffix_matches():
    assert split_category_suffix("ZBQ.ZZ", _CATEGORY_FOLDERS) is None


def test_split_category_suffix_requires_something_before_the_suffix():
    # The bare suffix on its own isn't a valid candidate — there has to be
    # an actual base ticker/description in front of it.
    assert split_category_suffix(".BB", _CATEGORY_FOLDERS) is None


_KNOWN_TICKERS = ["ZBQ", "ZPAX", "ZRE.TO"]


def test_mentioned_suffix_found_mid_sentence():
    # "Key KPIs for ART.BB Q3.pdf" — the suffix is glued to "ART" mid-name,
    # not at the front and not right before the extension.
    assert find_category_suffix_mentioned_anywhere(
        "Key KPIs for ART.BB Q3.pdf", _KNOWN_TICKERS, _CATEGORY_FOLDERS
    ) == ("ART", "/Active/Busted Biotechs (.BB)")


def test_mentioned_suffix_found_right_before_extension():
    assert find_category_suffix_mentioned_anywhere(
        "my quality companies.BB.pdf", _KNOWN_TICKERS, _CATEGORY_FOLDERS
    ) == ("companies", "/Active/Busted Biotechs (.BB)")


def test_mentioned_suffix_defers_to_a_real_ticker_with_the_same_shape():
    # "ZRE.TO" is itself a real, existing ticker — even though it also
    # looks suffix-shaped, it should never be treated as a category-suffix
    # candidate (that's find_ticker_mentioned_anywhere()'s job instead).
    assert find_category_suffix_mentioned_anywhere("Notes on ZRE.TO.pdf", _KNOWN_TICKERS, _CATEGORY_FOLDERS) is None


def test_mentioned_suffix_returns_none_when_ambiguous():
    # Two different category folders mentioned — genuinely ambiguous.
    assert (
        find_category_suffix_mentioned_anywhere("XYZ.BB and ABC.TO notes.pdf", _KNOWN_TICKERS, _CATEGORY_FOLDERS)
        is None
    )


def test_mentioned_suffix_returns_none_when_absent():
    assert find_category_suffix_mentioned_anywhere("random notes with no suffix.pdf", _KNOWN_TICKERS, {}) is None


def test_mentioned_suffix_ignores_plain_words_without_a_dot():
    assert find_category_suffix_mentioned_anywhere("BB is not a suffix here.pdf", _KNOWN_TICKERS, _CATEGORY_FOLDERS) is None
