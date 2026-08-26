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

Hosting is [Railway](https://railway.app) (Hobby plan) — two services in one
Railway project, backend and frontend, each with its own Root Directory set
in the Railway dashboard and its own `railway.toml` (`backend/railway.toml`,
`frontend/railway.toml`) controlling the build/start commands.

**One-time setup for a new deploy:**

1. Create a Railway project, then add two services from this repo — one
   with Root Directory `backend`, one with Root Directory `frontend`.
2. On the backend service, set every variable from `.env.example` **except**
   point `DROPBOX_ACTIVE_PATH`, `DROPBOX_INACTIVE_PATH`,
   `DROPBOX_HISTORICALS_PATH`, and `DROPBOX_NEEDS_REVIEW_PATH` at the real
   data, not the Dev Sandbox — see "Testing against sandbox data vs. real
   data" below for the exact values.
3. **Attach a Railway Volume to the backend service**, mounted at `/data`,
   and set `ACTIVITY_DB_PATH=/data/activity.db` in that service's
   variables. Without this, the activity log (`backend/data/activity.db`)
   lives on the container's local disk, which Railway wipes on every
   redeploy — the entire audit trail (who uploaded/moved/deleted what)
   would silently reset each time the backend redeploys. This is separate
   from Dropbox itself, which is the actual file storage and always
   persists regardless.
4. The frontend service doesn't need `VITE_API_BASE_URL` set — it serves
   the app through Caddy (see `frontend/Caddyfile`), which reverse-proxies
   `/api/*` to the backend over Railway's private network
   (`backend.railway.internal`, whatever port the backend actually binds
   to — check its logs if this ever changes). `api.js` defaults to the
   relative `/api`, which is exactly what that proxy expects.
5. Once both services have a public URL, set the backend's
   `DROPBOX_REDIRECT_URI` to **the frontend's own domain**, not the
   backend's — e.g. `https://<frontend-domain>/api/auth/callback` — and
   add that exact URL to the Dropbox App Console's allowed OAuth redirect
   URIs. See "Cross-origin auth and the Firefox sign-in fix" below for why
   this has to be the frontend's domain specifically.

**Ongoing redeploys**: pushing to the connected branch triggers Railway to
rebuild both services automatically; no manual steps beyond that.

## Cross-origin auth and the Firefox sign-in fix

Sign-in used to send the browser directly to the backend's own domain for
both `/auth/login` and `/auth/callback`, with the frontend calling the
backend's public URL directly for every API request afterward. That works
in Chrome/Edge, but Firefox's Total Cookie Protection (on by default) blocks
it: the session cookie gets set on the backend's domain, and a *different*
domain (the frontend) can never read it back on a cross-site fetch, even
with `SameSite=None; Secure` set correctly. The failure mode looks exactly
like "sign-in completes, but you land back signed out" — Safari's ITP
enforces something similar, and Chrome is moving the same direction, so
this wasn't a Firefox-only fix.

The actual fix: eliminate the cross-site relationship entirely. The
frontend now reverse-proxies `/api/*` to the backend (Vite's dev-server
proxy locally — see `vite.config.js` — and Caddy in production — see
`frontend/Caddyfile`), so from the browser's point of view there is only
ever one origin, for both the OAuth callback (where the cookie gets set)
and every subsequent API call (where it gets read back). That's why
`DROPBOX_REDIRECT_URI` points at the *frontend's* domain, not the
backend's — the callback has to land on the same origin the cookie will
later be read from, or nothing here actually changes.

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

`DROPBOX_ACTIVE_PATH`, `DROPBOX_INACTIVE_PATH`, `DROPBOX_HISTORICALS_PATH`,
and `DROPBOX_NEEDS_REVIEW_PATH` in `.env` control where the app looks for
its folders. During development these point at `/Shared/Dev Sandbox/...` —
a folder tree with fake test tickers (`TEST1`, `TEST2`, `TEST3`), separate
from the real client data, so upload/move testing can't accidentally
disturb real ticker folders. **Note:** attempts to create a brand-new
top-level folder (a sibling of `/Shared`) failed with a Dropbox
`no_write_permission` error — this account can only write inside folders
it's already a member of, which is why the sandbox lives at
`/Shared/Dev Sandbox/` rather than as its own top-level folder.

To point the app at the real data, change these four variables to:

```bash
DROPBOX_ACTIVE_PATH=/Shared/Active
DROPBOX_INACTIVE_PATH=/Shared/Inactive
DROPBOX_HISTORICALS_PATH=/Shared/Historicals
DROPBOX_NEEDS_REVIEW_PATH=/Shared/Needs Review
```

All four real folders exist as of 2026-08-11 (`/Shared/Needs Review` was
created that day specifically for this — it didn't exist before).

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

## Research bridge (ChatGPT ↔ Claude)

Phase 2 addition: lets the user hand a research report back and forth between
ChatGPT and Claude mid-conversation ("send this to Claude", "check what
Claude said", "save the final version"), instead of a fully-automated
pipeline. Lives in `backend/app/routers/bridge.py` and
`backend/app/services/bridge_store.py`.

**How it works:** two separate MCP servers, one per AI, each mounted at
its own secret URL (`/bridge/<BRIDGE_CHATGPT_SECRET>/mcp` and
`/bridge/<BRIDGE_CLAUDE_SECRET>/mcp` — see `.env.example`). The server
infers which AI is calling purely from which secret path was hit, so tool
calls never need an explicit "from/to" argument. Each AI gets three tools:
`send_report` (always to the other AI), `get_pending` (check what the
other AI sent), and `save_final` (write an approved version into that
ticker's real Dropbox folder, reusing the same `dropbox_client`/
`ticker_registry`/`activity_log` code the file-upload flow already uses).

Both `send_report` and `save_final` take an `encoding` argument — `"text"`
(default) for a plain markdown report, or `"base64"` for a real binary
file (e.g. an actual `.xlsx` financial model with working formulas, which
both ChatGPT and Claude can genuinely generate in their own apps) plus a
`filename` naming it. So a draft model can be handed to the other AI for
review via `send_report` before anything's finalized, not just saved
directly — `save_final` verified byte-for-byte round-trip for the binary
path; `get_pending` surfaces each message's `encoding`/`filename` so the
receiving AI knows whether `content` is text to read or a file's raw
bytes.

**`save_final`'s text path renders a real PDF, server-side** —
`app/services/pdf_render.py` (markdown2 + xhtml2pdf, both pure Python, no
native system dependencies) converts the markdown to a formatted PDF
before it's written to Dropbox, and the filename's extension is forced to
`.pdf` regardless of what was passed in. This is deterministic backend
code, not the AI generating anything, so it carries none of the
reliability issues base64 content does. the user gets a real, readable PDF
memo, not raw markdown text. Verified: headings, bold/italic, bullet and
numbered lists, tables, and blockquotes all render correctly; round-tripped
a real save through the Dev Sandbox and confirmed the downloaded PDF's
extracted text matches. `send_report` (AI-to-AI review, not what the user
sees) is untouched — still plain markdown, no rendering.

`send_report` de-dupes: an identical (sender/recipient/ticker/content/
encoding) call within 2 minutes returns the existing pending message's id
instead of creating a duplicate — real-world trigger was ChatGPT's client
timing out on a slow permission-confirmation click and silently retrying
the tool call, which without this created several identical pending
messages for the other AI to see.

Both tools' MCP `instructions` explicitly tell the model never to call
`send_report`/`save_final` in the same turn it drafts/revises something —
show it and wait for the user to explicitly say to send/save it first. This
is a prompt-level guardrail, not code-enforced (the server has no way to
verify "real" approval happened), so it's a strong nudge, not a guarantee.

`save_final` can also create a genuinely new ticker (not just save into an
existing one) — pass `new_ticker_status` ("active"/"inactive"/
"historicals") to say which bucket it belongs in; omitting it for a new
ticker returns an error telling the AI to ask the user first rather than
guessing, and a likely typo of an existing ticker always refuses outright
(same "confirm, don't guess" principle `ticker_registry.resolve_ticker`
already enforces elsewhere in the app).

Messages live in a `bridge_messages` table in the
same SQLite DB the activity log uses — same Railway Volume, no new infra.

**Large files:** a real file's base64 form can run tens of thousands of
characters, and an AI's own tools that read a file back into its context
truncate past roughly that point (confirmed on a real ~34KB PDF, ~45K
base64 chars) — silently corrupting anything sent through
`send_report`/`save_final` in one call. A chunked-upload mechanism was
built and tested to work around this, but live testing showed a deeper
problem chunking doesn't fix: passing a chunk's content as a tool
argument means the model has to *generate* that base64 text itself
(tool arguments are model output, not a silent code-to-code handoff),
which is slow and not fully reliable for long random-looking strings —
so it was removed rather than kept as a half-working feature. Current
guidance (in the MCP `instructions`): `encoding='base64'` is fine for
files confidently small (roughly under 20K base64 characters); for
anything larger, tell the user to download the file directly from the AI's
own chat interface and use the tool's existing Upload button instead —
that path never requires the AI to touch the file's bytes as generated
text at all.

**Setup for the user:** generate both secrets
(`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`), set
`BRIDGE_CHATGPT_SECRET`/`BRIDGE_CLAUDE_SECRET` on the backend service, then
add a custom connector in his ChatGPT (Settings → Plugins → Developer
mode) and Claude accounts, each pointed at the matching
`https://<backend-domain>/bridge/<secret>/mcp` URL. No paid plan upgrade
required on either side — confirmed via a full validation pass that custom
MCP connectors, including write-capable ones, work fine even on ChatGPT
Free.

**Optional email notifications:** set `BRIDGE_NOTIFY_EMAIL` (the user's
address) plus a Gmail app password
(`BRIDGE_NOTIFY_SMTP_USER`/`BRIDGE_NOTIFY_SMTP_APP_PASSWORD`) to have the
backend email him a one-line heads-up whenever a new message shows up, so
he doesn't have to remember to check. This is a nudge, not real
automation — neither ChatGPT nor Claude's consumer apps expose any way for
a third party to inject a message into a live chat session, so the user will
always need to send at least a short message himself to actually get a
response; the connector's own instructions also tell the model to check
`get_pending` proactively whenever a ticker comes up in conversation, so
mentioning the ticker by name is usually enough.

**Gotchas that cost real debugging time, designed around from the
start — worth knowing if this ever needs touching again:**
- The `mcp` Python SDK serves its endpoint at `/mcp` by default, not `/`.
- Mounting an MCP sub-app inside FastAPI via `app.mount()` does **not**
  auto-start its session manager's lifespan — has to be entered manually
  from the parent app's own `lifespan=` (see `app/main.py`), or every real
  request 500s with "Task group is not initialized."
- The SDK's built-in DNS-rebinding protection rejects any Host header
  other than `127.0.0.1`/`localhost` by default — must pass
  `TransportSecuritySettings(enable_dns_rebinding_protection=False)` into
  `streamable_http_app()` for a server sitting behind a real domain.
- ChatGPT's custom-connector UI has no plain bearer-token field (only
  OAuth/No Auth/Mixed) — a header-based secret is invisible from
  ChatGPT's side, which is why the secret lives in the URL path instead.
