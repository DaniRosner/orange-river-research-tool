from app.services.sorting import (
    find_ambiguous_uppercase_mentions,
    find_close_matches,
    find_known_ticker,
    find_ticker_mentioned_anywhere,
    find_ticker_mentioned_in_text,
    parse_leading_token,
    parse_ticker,
    tokenize_filename,
)


def test_plain_ticker():
    assert parse_ticker("ZBQ Annual Report Notes.docx") == "ZBQ"


def test_ticker_with_digits():
    assert parse_ticker("3M Q3 Notes.pdf") == "3M"


def test_exchange_suffixed_tickers():
    assert parse_ticker("ZRE.TO Some Notes.pdf") == "ZRE.TO"
    assert parse_ticker("ASCO.LN Earnings Call.docx") == "ASCO.LN"
    assert parse_ticker("CTK.SA Model.xlsx") == "CTK.SA"
    assert parse_ticker("NEC.AU Update.pdf") == "NEC.AU"


def test_lowercase_ticker_is_still_extracted():
    # Real tickers aren't always uppercase in Dropbox (e.g. a genuine
    # lowercase folder name) — extraction itself doesn't judge case. It's
    # the caller's job to decide what to do with an unmatched lowercase
    # candidate.
    assert parse_ticker("zqrx Notes.pdf") == "zqrx"


def test_no_space_separator_is_unmatched():
    assert parse_ticker("readme.txt") is None
    assert parse_ticker("ZBQ.docx") is None


def test_hidden_and_junk_files_are_unmatched():
    assert parse_ticker(".DS_Store") is None


def test_hyphen_and_underscore_separators_are_matched():
    assert parse_ticker("ZBQ-Annual Report.docx") == "ZBQ"
    assert parse_ticker("ZBQ_Notes.docx") == "ZBQ"
    assert parse_ticker("ZRE.TO-Some Notes.pdf") == "ZRE.TO"


def test_leading_token_does_not_require_a_separator():
    # Unlike parse_ticker(), this is used for category-suffix routing,
    # which doesn't need a description attached at all.
    assert parse_leading_token("ZBQ.BB.pdf") == "ZBQ.BB"
    assert parse_leading_token("ZBQ Notes.docx") == "ZBQ"
    assert parse_leading_token("ZBQ") == "ZBQ"


def test_leading_token_ignores_the_extension():
    assert parse_leading_token("ZBQ.pdf") == "ZBQ"


def test_leading_token_is_none_for_junk_files():
    assert parse_leading_token(".DS_Store") is None


_KNOWN_TICKERS = ["ZBQ", "ZPAX", "ZSPQ", "ZTQZ", "ZNZQ", "ZCIQ", "ZRE.TO", "zqrx"]


def test_find_known_ticker_exact_match():
    assert find_known_ticker("ZBQ", _KNOWN_TICKERS) == "ZBQ"


def test_find_known_ticker_is_case_insensitive():
    assert find_known_ticker("zbq", _KNOWN_TICKERS) == "ZBQ"
    assert find_known_ticker("ZQRX", _KNOWN_TICKERS) == "zqrx"


def test_find_known_ticker_returns_none_when_absent():
    assert find_known_ticker("ZZZZ", _KNOWN_TICKERS) is None


def test_close_match_suggests_typo_correction():
    assert find_close_matches("ZBP", _KNOWN_TICKERS) == ["ZBQ"]


def test_close_match_is_case_insensitive():
    known = ["ZBQ", "ZPAX", "ZSPQ"]
    assert find_close_matches("zbp", known) == ["ZBQ"]


def test_close_match_returns_nothing_for_unrelated_ticker():
    # A genuinely new ticker shouldn't get bogus suggestions just because
    # *something* in the known list is the closest of a bad lot.
    assert find_close_matches("ZZZZ", _KNOWN_TICKERS) == []


def test_close_match_respects_limit():
    known = ["ABCD", "ABCE", "ABCF", "ABCG"]
    assert len(find_close_matches("ABCX", known, limit=2)) == 2


def test_mentioned_ticker_found_in_prose_filename():
    assert find_ticker_mentioned_anywhere("quarterly notes on zbq.pdf", _KNOWN_TICKERS) == "ZBQ"


def test_mentioned_ticker_is_case_insensitive():
    assert find_ticker_mentioned_anywhere("Notes on ZQRX earnings.docx", _KNOWN_TICKERS) == "zqrx"


def test_mentioned_ticker_returns_none_when_absent():
    assert find_ticker_mentioned_anywhere("random file with no tickers.pdf", _KNOWN_TICKERS) is None


def test_mentioned_ticker_returns_none_when_ambiguous():
    # Two different real tickers mentioned — genuinely ambiguous, not this
    # function's job to guess between.
    assert find_ticker_mentioned_anywhere("Comparing ZBQ to ZPAX.pdf", _KNOWN_TICKERS) is None


def test_mentioned_ticker_ignores_the_extension():
    # A ticker named "PDF" would be a wild coincidence, but make sure the
    # extension itself is never accidentally treated as a mention.
    assert find_ticker_mentioned_anywhere("random notes.pdf", ["PDF"]) is None


def test_mentioned_in_text_finds_single_real_ticker():
    assert find_ticker_mentioned_in_text("Quick notes on ZBQ ahead of earnings", _KNOWN_TICKERS) == "ZBQ"


def test_mentioned_in_text_survives_punctuation_that_would_break_the_filename_variant():
    # The whole reason this is a separate function from
    # find_ticker_mentioned_anywhere(): that one runs _strip_extension()
    # first, which blindly splits on the last "." in the string — fine
    # for a real filename, but would truncate this sentence right after
    # "earnings" before ever tokenizing "Thoughts". Prose has periods;
    # this function must not treat them as a file extension.
    text = "Notes on ZBQ earnings. Thoughts inside."
    assert find_ticker_mentioned_in_text(text, _KNOWN_TICKERS) == "ZBQ"


def test_mentioned_in_text_returns_none_when_ambiguous():
    text = "Comparing ZBQ against ZPAX this quarter"
    assert find_ticker_mentioned_in_text(text, _KNOWN_TICKERS) is None


def test_mentioned_in_text_returns_none_when_absent():
    assert find_ticker_mentioned_in_text("Just some notes with no company mentioned", _KNOWN_TICKERS) is None


def test_ambiguous_uppercase_mentions_found():
    assert find_ambiguous_uppercase_mentions("Comparing ZBQ to ZPAX.pdf", _KNOWN_TICKERS) == ["ZBQ", "ZPAX"]


def test_ambiguous_uppercase_mentions_ignores_lowercase_collisions():
    # Only "ZBQ" is in caps — "zpax" is lowercase, more likely coincidental
    # prose than a deliberate second reference, so this isn't ambiguous.
    assert find_ambiguous_uppercase_mentions("Comparing ZBQ to zpax.pdf", _KNOWN_TICKERS) == []


def test_ambiguous_uppercase_mentions_returns_empty_when_nothing_mentioned():
    assert find_ambiguous_uppercase_mentions("random notes.pdf", _KNOWN_TICKERS) == []


def test_ambiguous_uppercase_mentions_returns_empty_for_a_single_mention():
    assert find_ambiguous_uppercase_mentions("Notes about ZBQ only.pdf", _KNOWN_TICKERS) == []


def test_tokenize_filename_glues_suffix_to_the_word_before_it():
    # No space between "companies" and ".BB" — one token, not two.
    assert tokenize_filename("my quality companies.BB.pdf") == ["my", "quality", "companies.BB"]


def test_tokenize_filename_keeps_separate_words_separate():
    assert tokenize_filename("Key KPIs for ART.BB Q3.pdf") == ["Key", "KPIs", "for", "ART.BB", "Q3"]


