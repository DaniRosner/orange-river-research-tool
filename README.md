# Research Tool

Web app that organizes research files (saved from ChatGPT/Claude sessions)
into the client's existing Dropbox folder structure, with a browser UI to
browse, search, and manage them.

**Status:** Phase 1, Milestones 1–4 complete (repo scaffold, Dropbox
connection, ticker-sorting logic, API layer). Real frontend wiring — an
actual upload button, move controls, and the "did you mean...?" confirm
dialog — is not yet built. See the `Suggested build order` in
`phase1-project-brief.md` for what's next.

## Project structure

- `backend/` — FastAPI app (Python)
- `frontend/` — React SPA (Vite)

## Setup

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Backend runs at `http://localhost:8000`. Visit `http://localhost:8000/docs`
for the interactive API docs.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`.

## Environment variables

Copy `.env.example` to `.env` at the repo root and fill in the values:

```bash
cp .env.example .env
```

See `.env.example` for what each variable is for. **Never commit `.env`** —
it's already covered by `.gitignore`.

## Credential rotation

The app authenticates to Dropbox using three values in `.env`:
`DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, and `DROPBOX_REFRESH_TOKEN`.

- **App key / secret**: found on the app's page in the
  [Dropbox App Console](https://www.dropbox.com/developers/apps), under the
  app named "Research Tool" → Settings tab. If the secret is
  ever compromised, click "Show" next to App secret, then look for a
  regenerate/reset option in the same panel, and update `.env` (and the
  production environment variables) with the new value.
- **Refresh token**: this is what lets the backend connect to Dropbox
  without a human logging in every time. If it's ever revoked or needs
  replacing, run the one-time setup script:

  ```bash
  cd backend
  source .venv/bin/activate
  python scripts/get_refresh_token.py
  ```

  It prints a Dropbox authorization link — open it, log in as whichever
  Dropbox account should own the connection (the company account), click
  Allow, then paste the resulting code back into the script. It prints a
  new refresh token to paste into `.env` as `DROPBOX_REFRESH_TOKEN`.

**Note on account structure**: this is a Dropbox Business/team account. The
`Active/`, `Inactive/`, etc. folders live in the team space, not the
personal home folder of whichever account connects — the backend code
(`app/services/dropbox_client.py`) automatically detects and uses the team
namespace, so this doesn't require any special handling when rotating
credentials.

## Redeploying

_To be filled in once hosting is set up (Milestone 6): steps to redeploy
the backend and frontend on Render/Railway from a clean checkout._

## Dropbox app ownership handoff

The Dropbox app ("Research Tool") is registered under the
company's Dropbox Business team account, not a personal developer account —
there is no ownership transfer step needed for Dropbox itself.

What *does* depend on an individual today: the app was authorized (i.e. the
current `DROPBOX_REFRESH_TOKEN` was generated) by logging in as
`danirosner58@gmail.com`, a member of that Business team. This is fine
functionally — the app operates on the shared team space regardless of
which team member authorized it — but if that person's access to the team
is ever removed, generate a fresh refresh token authorized by a different
(ideally permanent/role-based) team member using the steps in
"Credential rotation" above.

## Testing against sandbox data vs. real data

`DROPBOX_ACTIVE_PATH`, `DROPBOX_INACTIVE_PATH`, and
`DROPBOX_NEEDS_REVIEW_PATH` in `.env` control where the app looks for its
folders. During development these point at `/Shared/Dev Sandbox/...` — a
folder tree with fake test tickers (`TEST1`, `TEST2`, `TEST3`), separate
from the real client data, so upload/move testing can't accidentally
disturb real ticker folders. **Note:** attempts to create a brand-new
top-level folder (a sibling of `/Shared`) failed with a Dropbox
`no_write_permission` error — this account can only write inside folders
it's already a member of, which is why the sandbox lives at
`/Shared/Dev Sandbox/` rather than as its own top-level folder.

To point the app at the real data, change these three variables to:

```bash
DROPBOX_ACTIVE_PATH=/Shared/Active
DROPBOX_INACTIVE_PATH=/Shared/Inactive
DROPBOX_NEEDS_REVIEW_PATH=/Shared/Needs Review
```

(`/Shared/Needs Review` doesn't exist yet in the real data as of this
writing — it gets created the same way the sandbox one was, or the app
should be extended to create it automatically on first run.)

## How the folder-sorting logic works

Sorting logic lives in `app/services/sorting.py` and is deliberately kept
separate from any Dropbox/API code, so it can be tested with plain
filenames.

- **`parse_ticker(filename)`**: matches the real naming convention,
  `TICKER Description.ext` — an uppercase ticker prefix (letters/digits,
  optionally with an exchange suffix like `.TO`, `.LN`, `.SA`, `.AU`),
  followed by a space and a free-form description. Returns `None` if the
  filename doesn't fit this shape, which is the signal to route the file to
  Needs Review instead of guessing.
- **`find_close_matches(ticker, known_tickers)`**: for a parsed ticker that
  isn't among the tickers that actually exist in Dropbox, finds close
  look-alikes (e.g. a typo'd `ZBP` suggesting the real `ZBQ`) using Python's
  built-in text-similarity matching. Used to power a "did you mean...?"
  confirmation step before a new ticker folder is created, rather than
  silently creating one for every typo.

**Not yet handled, flagged for client input (see open questions in
`phase1-project-brief.md`):** non-ticker folders that exist inside
`Active/` today (`Busted Biotechs`, `Japan`, `SPAC`, etc.), the `Passed On`
folder, and the existing `OEC` duplicate between `Active/` and `Inactive/`.
The sorting logic does not attempt to resolve any of these — it only
governs where *newly uploaded* files go.
