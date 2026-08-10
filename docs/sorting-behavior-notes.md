# File-Sorting Behavior — Ambiguous Cases

Running reference for how the app decides where a file goes, ordered simplest to most
complicated. Every rule here reflects what's actually implemented and tested (see
`backend/app/services/sorting.py`, `category_routing.py`, `ticker_registry.py`, and
`backend/app/routers/files.py`'s `upload_file()` docstring for the authoritative code-level
version of this same order). This doc exists to explain the *why* in plain terms — update it
whenever the sorting logic changes.

---

## 1. The basics (no ambiguity at all)

- **Exact match**: `ZBQ Notes.docx` → the real ticker `ZBQ` already has a folder → files there.
- **Case doesn't matter**: `zbq notes.docx` matches the real `ZBQ` folder; `ZQRX notes.docx`
  matches the real (lowercase-stored) `zqrx` folder. Real tickers aren't always uppercase on
  disk, so matching is always case-insensitive.
- **Exchange suffixes are part of the ticker's identity**: `ZRE.TO`, `ASCO.LN`, `CTK.SA` etc.
  are matched as whole strings, dot included.
- **Separator can be a space, hyphen, or underscore**: `ZBQ Notes.docx`, `ZBQ-Notes.docx`,
  `ZBQ_Notes.docx` all work identically.

## 2. Typos of a real ticker

- If the front word isn't an exact match but is *close* to a real ticker (fuzzy text
  similarity, not a fixed "N letters off" rule), filing is held and the app asks "did you mean
  X?" instead of guessing.
- If it's not close to anything and doesn't look deliberate (see below), it's simply not a
  ticker at all.

## 3. What counts as "looks like a deliberate new ticker"

- The **front word must be fully uppercase** to be treated as a plausible brand-new ticker
  (prompts "which status?"). A lowercase or mixed-case front word (`newco update.docx`) is
  read as ordinary prose and goes straight to Needs Review instead — there's no reliable way
  to tell "someone typed a real lowercase ticker" apart from "the first word of a sentence,"
  so this errs toward not creating spurious ticker folders.

## 4. Category-suffix (theme folder) routing — the basics

- A suffix like `.BB` glued directly to the front word (`ZBQ.BB Notes.docx`, or even
  `ZBQ.BB.pdf` with no description at all) routes straight into the matching theme folder
  (e.g. `Busted Biotechs (.BB)`), no ticker matching involved.
- **A real, already-existing ticker always wins over suffix routing** — if `ZBQ.BB` itself is
  a real ticker (its own folder), an upload for `ZBQ.BB` files there, never into the theme
  folder, because the exact-match check runs first, unconditionally.
- This does **not** collaterally block other tickers from using the same suffix: if `ZRE.TO`
  is a real ticker but `RY.TO` isn't, `RY.TO Notes.docx` still correctly routes into a
  `Toronto Names (.TO)` theme folder — the protection is per-candidate, not suffix-wide.

## 5. Typo guardrail for suffixed tickers

- If the front word carries a suffix and is a near-typo of a *different* real ticker that
  shares that exact suffix (e.g. `KBS.BB` vs. the real `ZBQ.BB`), filing is held with three
  choices: the suggested real ticker, the theme folder the suffix points to, or "create a new
  ticker" for what was actually typed.
- Scoped deliberately narrow — comparison is only against real tickers sharing the *same*
  suffix, and the suffix is stripped off both sides before scoring similarity. Comparing
  against every real ticker generally (or leaving the suffix in) would misfire on the ordinary,
  intended case of routing an unrelated real ticker into a theme folder on purpose (e.g.
  `MRNA.BB` isn't a typo of anything just because `MRNA` happens to be a real ticker
  elsewhere).

## 6. A suffix doesn't have to be at the front

- The suffix-routing check scans the **whole filename**, not just the leading word — so
  `Key KPIs for MRNA.BB Q3.pdf` (suffix mid-sentence) and `my quality companies.BB.pdf`
  (suffix tacked onto the very end, right before the extension) both correctly route into the
  theme folder, exactly like a front-anchored `MRNA.BB Notes.docx` would.
- This is one unified, ambiguity-aware scan (not "check the front first, then check everywhere
  else as a weaker fallback") — see the next point for why that matters.

## 7. Two theme-folder mentions in one filename is ambiguous

- Because suffix-matching scans the whole filename in one pass, a filename mentioning *two*
  different theme-folder suffixes (e.g. `XYZ.BB and ABC.TO comparison.pdf`) is correctly
  treated as ambiguous and falls through to Needs Review — it does **not** pick whichever
  suffix happened to appear first in the string. (Earlier in development this used a
  fast-but-unchecked front-only path; that's been replaced with the unified scan specifically
  to fix this case.)

## 8. A real ticker mentioned anywhere always outranks a typo-guess about the front word

- If the front word isn't a match for anything, the app scans the whole filename for an
  *exact*, unambiguous mention of a real ticker before ever falling back to Needs Review —
  e.g. `quarterly notes on zbq.pdf` correctly files under `ZBQ` even though "quarterly" is the
  front word.
- **This check runs before the general typo-guess and new-ticker checks**, not after. Why:
  without that ordering, an ordinary word that happens to be a near-typo of some unrelated real
  ticker (e.g. "Notes" being fuzzy-close to a real ticker literally named `NOEXT`) could hijack
  the file into a typo-confirmation dialog before the app ever noticed a real, clearly-mentioned
  ticker elsewhere in the same name (e.g. `ZRE.TO` in `Notes on ZRE.TO for review.pdf`). An
  exact mention is always a stronger signal than a fuzzy guess, so it's checked first.
- This doesn't cost real typo-help elsewhere: a genuine typo (e.g. `zqrz` for the real
  lowercase ticker `zqrx`) is never an *exact* match, so it's never caught by this check either
  — it still correctly falls through to the typo-guess afterward.

## 9. Two real tickers mentioned is ambiguous — but sometimes worth asking about

- `Comparing ZBQ to ZPAX.pdf` mentions two different real tickers. By default this is
  ambiguous and goes to Needs Review rather than guessing.
- **Exception**: if the ambiguous mentions were written in all-caps in the filename itself
  (not just case-insensitively matching a real ticker), the app holds and asks "which one?"
  instead — a deliberate, visibly-capitalized double reference is a strong enough signal to
  interrupt for. A lowercase collision (`Comparing ZBQ to zpax.pdf`, one lowercase) stays
  silent and goes straight to Needs Review — far more likely to be coincidental, since plenty
  of real tickers are also ordinary English words (`ALL`, `IT`, `ON`, ...), and prompting for
  every lowercase collision would turn this safety net into a nuisance.

## 10. A suffix can hide inside what looks like a file extension

- `ZBQ Q3 Numbers.BB` — no real file extension, just `.BB` sitting where one normally would.
  This is genuinely ambiguous (attempted suffix tag in the wrong spot, vs. just a missing/odd
  extension) and can't be confidently resolved either way — so it's sent to Needs Review with
  an explicit reason, **bypassing every other check**, including what would otherwise be a
  perfectly valid real-ticker match on the front word (`ZBQ` here is a real ticker, but the
  app deliberately doesn't let that silently win and discard the `.BB`).

## 11. Two theme folders can't safely share the same suffix — and now it's actually checked

- Creating a new theme folder needs nothing from the app at all: rename (or name) a folder in
  Dropbox to end in `(.SUFFIX)`, e.g. `Japan (.JP)`. Picked up automatically on the next
  request — no deploy, no admin screen.
- But if two different folders ever end up with the *same* suffix (e.g. two folders both named
  `... (.BB)`), routing silently resolves it — whichever folder the scan happens to process
  last wins, and the other becomes permanently unreachable via suffix routing, with no error
  anywhere. The app now actively checks for this (`category_routing.find_duplicate_category_suffixes()`,
  exposed at `GET /tickers/category-suffix-warnings`) and shows a warning banner across the top
  of the app whenever it finds one — this should never fire in normal use, and if it does, the
  fix is just renaming one of the two folders to a different suffix.

## 12. Duplicate detection has its own edge case: identical content vs. case-only filename collisions

- Uploading a file whose name and content both exactly match something already there → no new
  file created, just a quiet "already saved" note.
- Uploading a file with the same name but genuinely different content → held for confirmation
  (replace / keep both), never silently overwritten.
- Dropbox itself treats filenames as case-insensitive but case-preserving. If a *new* filename
  differs from an existing one only by case (`Comparing ZBQ to zpax.pdf` vs. an existing
  `Comparing ZBQ to ZPAX.pdf`) **and** the content is identical, Dropbox silently treats it as
  the same file and hands back the original — no duplicate, no data loss, but our app's own
  duplicate-conflict messaging can misleadingly say "name conflict" for what's actually a
  no-op. Worth remembering when a test seems to silently "rename" a file you just uploaded.

## 13. Theme folders are never treated as tickers — including when resolving a dropped folder's name

- `ticker_registry.get_known_tickers()` deliberately excludes any folder ending in a
  `(.SUFFIX)` marker from the "known tickers" list. This has no effect on the tabs (Active/
  Inactive/Historicals still list theme folders normally, via a separate call) — it only
  affects ticker *matching/resolution* logic.
- Why this matters: before this exclusion existed, dragging in an unrelated folder whose name
  happened to end in a marker too (e.g. a folder literally named `Total Oasis (.TO)`) could get
  fuzzy-matched against a real theme folder's full name (`Toronto Names (.TO)`) and produce a
  nonsensical "did you mean Toronto Names (.TO)?" suggestion — purely because both names shared
  the literal `(.TO)` text, nothing to do with the names actually being similar.

## 14. "Known tickers" and "known folders" are two different lists now

- `ticker_registry.get_known_folders()` — every real folder, tickers and theme folders alike.
  Used anywhere the app needs to find and manage a *specific, already-known* folder (view its
  files, delete it, move it, rename it) — a theme folder is just as legitimate a target for
  those as a real ticker.
- `ticker_registry.get_known_tickers()` — the same list with theme folders filtered out. Used
  only for ticker-*matching* purposes (typo-checking, resolving a candidate name), where
  including theme folders caused real bugs (see #13).
- The app can now rename a ticker or theme folder in place (`POST /tickers/{ticker}/rename`,
  exposed as a "Rename" button on the ticker detail page) — meant specifically for resolving a
  suffix collision (#11) from inside the app, without needing to go into Dropbox directly.

## 15. Dragging in a genuinely empty folder still creates the ticker

- If a dropped folder resolves to a brand-new ticker (not a real, already-existing one) but has
  zero files inside it, the app still creates the empty ticker folder — it doesn't silently do
  nothing just because there was nothing to upload alongside it.
- If the folder resolves to a ticker that already exists, this stays a true no-op — there's
  nothing to create, and dragging an empty folder shouldn't move or otherwise touch a real
  ticker that's already there.
