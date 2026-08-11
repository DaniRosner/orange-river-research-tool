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

## 4. Category-suffix (theme folder) routing requires a `(.SUFFIX)` marker — parentheses required

- A file destined for a theme folder (e.g. `Busted Biotechs (.BB)`) must carry the SAME
  `(.SUFFIX)` marker literally in its own filename, anywhere in it — `MCR (.TO) Notes.docx` and
  `MCR(.TO) Notes.docx` both route into a `Toronto Names (.TO)` theme folder. Scans the **whole
  filename**, not just the leading word, so a marker mid-sentence (`Key KPIs for ART (.BB)
  Q3.pdf`) or right before the extension (`my quality companies (.BB).pdf`) works identically to
  one glued to the front.
- **A bare `TICKER.SUFFIX` with no parentheses (e.g. `MCR.TO`) is NEVER treated as a
  category-suffix candidate, no matter what suffix is registered.** That plain-dot shape is
  reserved entirely for a real ticker's own exchange suffix (`ZRE.TO`, `ASCO.LN`, etc. — see #1).
  This is a deliberate, load-bearing distinction, not an arbitrary syntax choice — see the next
  point for why.
- **Why the parentheses are required:** before this rule existed, a bare dot-suffix was
  genuinely ambiguous between "someone's exchange-suffixed ticker" and "a deliberate theme-folder
  tag" — the two used identical syntax. The one gap that couldn't be closed by any confirmation
  prompt: a brand-new real ticker on some exchange, uploaded for the very first time, has no
  existing folder to protect it and nothing to compare it against, so it could silently get
  swept into a theme folder sharing that exchange's code (e.g. `.TO`) with zero warning. Every
  upload after that first one would've been safe automatically (it'd then be a real, existing
  ticker, and #1's exact-match check always wins), but that first upload had no safety net.
  Requiring the parenthesized form for category routing removes the ambiguity structurally
  instead of trying to guess around it — a plain ticker filename can basically never contain a
  literal `(.SUFFIX)` by coincidence, so there's nothing left to confirm or protect against. (This
  is also why the app no longer needs a suffix-typo confirmation dialog, or the old "suspicious
  extension" check for a bare trailing `.BB` — both existed only to manage the ambiguity that
  requiring parentheses now eliminates outright.)
- **A real, already-existing ticker still always wins over category routing** — the exact-match
  check (#1) runs first, unconditionally, before the marker is ever checked. If `MCR` (or
  `MCR.TO`) is already a real ticker, a file named `MCR (.TO) Notes.docx` still files under the
  real `MCR` ticker, not the theme folder — its leading word alone is enough to protect it,
  regardless of what marker appears later in the name.
- A filename carrying **two different theme folders' markers** (e.g. `Compare (.BB) and (.TO)
  names.pdf`) is genuinely ambiguous and falls through instead of picking whichever came first.

## 5. A real ticker mentioned anywhere always outranks a typo-guess about the front word

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

## 6. Two real tickers mentioned is ambiguous — but sometimes worth asking about

- `Comparing ZBQ to ZPAX.pdf` mentions two different real tickers. By default this is
  ambiguous and goes to Needs Review rather than guessing.
- **Exception**: if the ambiguous mentions were written in all-caps in the filename itself
  (not just case-insensitively matching a real ticker), the app holds and asks "which one?"
  instead — a deliberate, visibly-capitalized double reference is a strong enough signal to
  interrupt for. A lowercase collision (`Comparing ZBQ to zpax.pdf`, one lowercase) stays
  silent and goes straight to Needs Review — far more likely to be coincidental, since plenty
  of real tickers are also ordinary English words (`ALL`, `IT`, `ON`, ...), and prompting for
  every lowercase collision would turn this safety net into a nuisance.

## 7. Two theme folders can't safely share the same suffix — and now it's actually checked

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

## 8. Duplicate detection has its own edge case: identical content vs. case-only filename collisions

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

## 9. Theme folders are never treated as tickers — including when resolving a dropped folder's name

- `ticker_registry.get_known_tickers()` deliberately excludes any folder ending in a
  `(.SUFFIX)` marker from the "known tickers" list. This has no effect on the tabs (Active/
  Inactive/Historicals still list theme folders normally, via a separate call) — it only
  affects ticker *matching/resolution* logic.
- Why this matters: before this exclusion existed, dragging in an unrelated folder whose name
  happened to end in a marker too (e.g. a folder literally named `Total Oasis (.TO)`) could get
  fuzzy-matched against a real theme folder's full name (`Toronto Names (.TO)`) and produce a
  nonsensical "did you mean Toronto Names (.TO)?" suggestion — purely because both names shared
  the literal `(.TO)` text, nothing to do with the names actually being similar.

## 10. "Known tickers" and "known folders" are two different lists now

- `ticker_registry.get_known_folders()` — every real folder, tickers and theme folders alike.
  Used anywhere the app needs to find and manage a *specific, already-known* folder (view its
  files, delete it, move it, rename it) — a theme folder is just as legitimate a target for
  those as a real ticker.
- `ticker_registry.get_known_tickers()` — the same list with theme folders filtered out. Used
  only for ticker-*matching* purposes (typo-checking, resolving a candidate name), where
  including theme folders caused real bugs (see #9).
- The app can now rename a ticker or theme folder in place (`POST /tickers/{ticker}/rename`,
  exposed as a "Rename" button on the ticker detail page) — meant specifically for resolving a
  suffix collision (#7) from inside the app, without needing to go into Dropbox directly.

## 11. Dragging in a genuinely empty folder still creates the ticker

- If a dropped folder resolves to a brand-new ticker (not a real, already-existing one) but has
  zero files inside it, the app still creates the empty ticker folder — it doesn't silently do
  nothing just because there was nothing to upload alongside it.
- If the folder resolves to a ticker that already exists, this stays a true no-op — there's
  nothing to create, and dragging an empty folder shouldn't move or otherwise touch a real
  ticker that's already there.

## 12. Uploading a single file into a folder that already has subfolders pauses to ask which one

- Once a single (non-folder-drag) upload has resolved to a destination — a ticker match, a
  mention-anywhere match, or a theme folder via suffix routing — the app checks whether that
  folder already has subfolders of its own (e.g. a ticker with "Old Models", "Q3 2024"; a theme
  folder like `Busted Biotechs (.BB)` with its own internal breakdown). If it does, filing pauses
  ("choose_subfolder") and asks: file it directly in the root, into one of the existing
  subfolders, or into a brand-new one (typed on the spot — Dropbox creates it automatically,
  nothing has to exist ahead of time). A folder with no subfolders at all (including a
  brand-new ticker, which never has any) skips this entirely and files at the root exactly as
  before — this only changes behavior for destinations that already have real structure.
- This is what answers the open question from #10/#11 about whether theme-folder suffix routing
  should be able to reach into a theme folder's own sub-portfolio structure: rather than trying
  to guess or hard-code a taxonomy (which would need the user's input on exactly how each theme
  folder is organized internally, and would break the moment that organization changes), the app
  just asks at upload time. Applies identically to ticker folders and theme folders — whichever
  the upload resolved to.
- Deliberately does **not** apply to uploading a whole dropped *folder's* contents — that flow
  already preserves the dropped folder's own subfolder structure one-for-one (see `relative_path`
  elsewhere in this doc), so asking per-file on top of that would just fight it. This is scoped
  to individually selected/dropped loose files only.
- Implementation note: a resubmission answering "the root, deliberately" can't actually send an
  empty string — FastAPI's form handling collapses an empty field back to "not answered," which
  would loop the question forever. `relative_path="."` is the sentinel used instead (a real
  Dropbox folder can never be named `.`), for both this dialog's root option and the whole-folder
  upload flow's own root-level files.
