# Project Brief: Research File Organizer (Phase 1)

## Context
This is a contracted freelance build for a hedge fund client (Your Firm
Management). I am not an employee — this tool must be fully transferable to
the client at project end. **Ownership and transferability are hard
requirements, not nice-to-haves.** Every architecture decision below should
be evaluated against: "can the client run this independently, under their
own accounts, with zero dependency on me, once I hand it off?"

## What we're building
A web app that automatically organizes research files the client saves from
AI chat sessions (ChatGPT/Claude) into a structured Dropbox folder system,
with a browser-based interface to view, search, and manage them.

## Core flow
**Single intake path**: the app itself. No Dropbox folder-watching —
Dropbox is purely the storage/organization backend where sorted files
live, not something the system scans for new files.

1. Client uploads a research file directly through the web app's UI
2. Backend parses the ticker from the filename (pattern: `TICKER
   Description.ext`, matching the real convention already in use — see
   "Existing folder structure" below) and files it into
   `Active/{TICKER}/` (default) via the Dropbox API
3. Anything that doesn't match lands in `Needs Review` instead of being
   silently dropped or misfiled
4. Front-end shows: Active tickers list, Inactive tickers list, a
   Needs Review bucket, and a per-ticker file view
5. Client can: move a ticker between Active/Inactive, manually assign a
   ticker to a Needs Review file, search across files

## Account access
This is being built against a **company Dropbox account** (not a
personal one), so the Dropbox API app should be registered directly
under that account from the start. No transfer-of-ownership step should
be needed for Dropbox specifically — confirm the access level actually
granted (full account vs. shared folder vs. team seat) supports
registering an API app before building the OAuth flow.

## Existing folder structure (already in production use — do not design against assumptions)
Inspected the live `Shared/` folder in the company Dropbox. Key findings
that change earlier assumptions:

- **Active/Inactive folders already exist** and are already organized
  one-subfolder-per-ticker (e.g. `Active/ZBQ/`, `Active/ZPAX/`). The app
  must work with this existing structure, not impose a new one.
- **File naming convention is `TICKER Description.ext`** (ticker prefix
  followed by a space, e.g. `ZBQ Annual Report Notes.docx`) — NOT
  underscore-separated as originally assumed. Sorting regex must match
  this real pattern.
- **Tickers include exchange suffixes**: `ZRE.TO`, `ASCO.LN`, `CTK.SA`,
  `NEC.AU`, etc. Ticker-matching logic must handle dots/suffixes, not
  assume plain alphanumeric US tickers.
- **Non-ticker folders exist inside Active**, mixed in with real
  tickers: `Busted Biotechs`, `Japan`, `SPAC`, `zqrx` (lowercase), and
  at least one junk folder (`New folder`). **Do not assume these are
  errors or auto-migrate/rename them** — how the app should treat these
  is an open question for the client (see below).
- **A third folder, `Passed On`, exists** alongside Active/Inactive,
  currently containing one entry. Whether this is a real third status
  the app needs to support, or should just be folded into Inactive, is
  an open question for the client.
- **`OEC` currently appears in both `Active/` and `Inactive/`** —
  possibly a stale leftover. Do not silently resolve this; flag it for
  the client.
- Misc junk in these folders: `.DS_Store`, `.lnk` shortcut files —
  filter these out of any folder listing logic, they are not real files.

### Open questions for the client (confirm before finalizing the sorting/status model)
1. Are `Busted Biotechs` / `Japan` / `SPAC` (etc.) real categories to
   preserve and support, or should the app just ignore/leave them alone?
2. Is `Passed On` a status in active use that needs a third bucket in
   the app, or should it be treated as equivalent to Inactive?
3. Is the `OEC` duplicate (in both Active and Inactive) intentional or
   a cleanup item?
4. Confirm it's fine to ignore system/junk files (`.DS_Store`, `.lnk`
   shortcuts, empty `New folder`) when the app scans folder contents.

## Explicitly out of scope for Phase 1
- **Automatic file capture directly from the ChatGPT/Claude interface**
  (no manual save step at all) — this is Phase 2, and depends on
  investigating what those platforms actually expose (browser
  extension, export API, etc.). Don't build toward this yet.
- Multi-agent research generation (ChatGPT + Claude cross-critique
  workflow) — Phase 2, separate track from file capture.
- Bloomberg / AlphaSense / Tenzing integrations — Phase 2.
- Any database beyond what's needed for Phase 1's own state (folder
  structure in Dropbox is the source of truth for organization; avoid
  introducing a database unless something genuinely can't live as a file/folder)

**Note on future phases:** Phase 2 will likely add automatic file
capture (removing the manual save/upload step) and possibly a
multi-agent research workflow. Do not build toward these now, and do
not add speculative abstraction for them. Just keep the sorting logic
and API layer reasonably modular, so a new file-intake method can be
added later without a rewrite of the core filing/organizing logic.

## Tech stack
- **Backend**: Python, FastAPI
- **Frontend**: React (single-page app, kept simple — no need for a
  heavyweight framework at this scale)
- **Storage**: Client's own Dropbox account, via Dropbox API
- **Auth**: Dropbox OAuth, scoped to the client's account
- **Hosting**: Render (or Railway) — cheap, simple deploys, easy to
  transfer account ownership later

## Ownership & transferability requirements (critical)
- **Dropbox app registration**: Register the Dropbox API app directly
  under the company Dropbox account (access already granted). Do not
  build against my personal Dropbox developer account as a permanent
  dependency — there should be no transfer-of-ownership step needed
  for Dropbox specifically, since it's already company infrastructure.
- **Hosting account**: Deploy under an account structure that can be
  transferred or re-created under the client's own billing — e.g. use
  a hosting account with the client's email as owner/co-owner from day
  one if possible, rather than my personal account with them as a guest.
- **No hardcoded secrets**: All credentials (Dropbox API keys, OAuth
  tokens, etc.) go in environment variables / a `.env` file, never in
  code. Include a `.env.example` with placeholder keys and clear setup
  instructions.
- **Clean, documented codebase**: Assume another developer (or the
  client themselves) needs to understand and maintain this with no
  access to me. Comment non-obvious logic. Avoid cleverness for its
  own sake.
- **README.md must include**: setup instructions from scratch, how to
  rotate/replace API credentials, how to redeploy, how to hand off
  Dropbox app ownership, and a plain-English description of the
  folder-sorting logic.
- **No personal-account lock-in**: Nothing in the architecture should
  require my continued involvement, my accounts, or my credentials to
  keep running after handoff.

## Suggested build order / milestones
1. **Repo scaffold**: FastAPI backend + React frontend skeleton, git
   initialized, `.env.example`, README stub
2. **Dropbox OAuth**: Connect to a Dropbox account, list folder contents
   — prove the connection works end-to-end
3. **Sorting logic**: On in-app upload, regex-match ticker prefix
   (pattern: `TICKER ` at start of filename, handling exchange-suffixed
   tickers like `ZRE.TO`), move file to `Active/{TICKER}/` or `Needs
   Review/` if unmatched. Write this as a standalone, testable function.
4. **API layer**: Endpoints to accept a file upload, list tickers
   (active/inactive/needs review), list files per ticker, move a ticker
   between active/inactive, manually assign a Needs Review file to a
   ticker
5. **Front-end**: Ticker list view (tabs: Active / Inactive / Needs
   Review) → ticker detail view (file list) → move/search actions →
   upload button for direct in-app file intake
6. **Deploy**: Stand up on Render/Railway, connect to a real (or test)
   Dropbox account, verify the full flow live
7. **Handoff pass**: Finalize README, confirm no hardcoded secrets,
   confirm account ownership can transfer cleanly, write a short
   "how this works" doc for the client

## Acceptance criteria for Phase 1 complete
- Client can upload a correctly-named file through the app and see it
  appear under the right ticker immediately
- Misnamed files land in Needs Review, not lost or silently misfiled
- Client can move a ticker between Active/Inactive from the app and see
  the Dropbox folder structure update accordingly
- App is usable from any device via a browser (no local-only dependency)
- A non-technical person (or a future developer) could follow the
  README to redeploy this from scratch under their own accounts
