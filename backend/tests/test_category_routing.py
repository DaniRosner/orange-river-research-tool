from app.services.category_routing import find_category_tag

_CATEGORY_FOLDERS = {".BB": "/Active/Busted Biotechs (.BB)", ".TO": "/Active/Toronto Names (.TO)"}


def test_finds_tag_glued_to_leading_ticker():
    assert find_category_tag("MCR(.TO) Notes.docx", _CATEGORY_FOLDERS) == "/Active/Toronto Names (.TO)"


def test_finds_tag_with_a_space_before_it():
    assert find_category_tag("MCR (.TO) Notes.docx", _CATEGORY_FOLDERS) == "/Active/Toronto Names (.TO)"


def test_finds_tag_mid_sentence():
    assert find_category_tag("Key KPIs for ART (.BB) Q3.pdf", _CATEGORY_FOLDERS) == "/Active/Busted Biotechs (.BB)"


def test_finds_tag_right_before_extension():
    assert find_category_tag("my quality companies (.BB).pdf", _CATEGORY_FOLDERS) == "/Active/Busted Biotechs (.BB)"


def test_is_case_insensitive():
    assert find_category_tag("mcr (.to) notes.docx", _CATEGORY_FOLDERS) == "/Active/Toronto Names (.TO)"


def test_bare_dot_suffix_never_matches_without_parens():
    # This is the whole point of requiring the parenthesized form — a real
    # ticker's own exchange suffix (e.g. a brand-new "MCR.TO") must never
    # be swept into a theme folder just because ".TO" happens to also be a
    # registered category suffix.
    assert find_category_tag("MCR.TO Notes.docx", _CATEGORY_FOLDERS) is None
    assert find_category_tag("MCR.TO.pdf", _CATEGORY_FOLDERS) is None


def test_returns_none_when_suffix_is_unregistered():
    assert find_category_tag("Notes (.ZZ).pdf", _CATEGORY_FOLDERS) is None


def test_returns_none_when_no_tag_present():
    assert find_category_tag("random notes with no tag.pdf", _CATEGORY_FOLDERS) is None


def test_returns_none_when_two_different_tags_are_both_present():
    # Genuinely ambiguous — not this function's job to pick one.
    assert find_category_tag("Compare (.BB) and (.TO) names.pdf", _CATEGORY_FOLDERS) is None


def test_ignores_plain_parenthetical_text():
    # An ordinary parenthetical aside shouldn't accidentally look like a
    # tag — it has to be exactly "(.WORD)" to match at all.
    assert find_category_tag("ZBQ (Q3 Draft).docx", _CATEGORY_FOLDERS) is None
