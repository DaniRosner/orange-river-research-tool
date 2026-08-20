# Central place for all app configuration. pydantic-settings reads each
# field below from an environment variable of the same name (case-
# insensitive), e.g. `dropbox_app_key` <- DROPBOX_APP_KEY. Real values live
# in the repo-root .env file (never committed — see .env.example for the
# full list with explanations). Import `settings` (the instance at the
# bottom of this file) anywhere config is needed; don't read os.environ
# directly elsewhere in the app.

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App configuration, loaded from environment variables / .env file."""

    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    dropbox_refresh_token: str = ""
    backend_cors_origins: str = "http://localhost:5173"

    # Per-user Dropbox sign-in (see app/services/auth.py) — separate from
    # dropbox_refresh_token above, which is the one fixed service account
    # every ticker/file operation still runs as regardless of who's signed
    # in. dropbox_redirect_uri must also be registered in the Dropbox App
    # Console's OAuth redirect URI list, or Dropbox rejects the sign-in
    # before it ever reaches this app.
    dropbox_redirect_uri: str = "http://localhost:8000/auth/callback"
    frontend_url: str = "http://localhost:5173"
    session_secret_key: str = ""

    # Where the app looks for Active/Inactive/Historicals/Needs Review
    # folders. Points at the Dev Sandbox during development; switch to the
    # real /Shared paths (e.g. /Shared/Active) when going live.
    dropbox_active_path: str = "/Shared/Dev Sandbox/Active"
    dropbox_inactive_path: str = "/Shared/Dev Sandbox/Inactive"
    dropbox_historicals_path: str = "/Shared/Dev Sandbox/Historicals"
    dropbox_needs_review_path: str = "/Shared/Dev Sandbox/Needs Review"

    # Where the activity-log SQLite file lives (see
    # app/services/activity_log.py). Defaults to a path inside the repo for
    # local dev; in production this must point at a Railway Volume's mount
    # path instead, or the audit trail is wiped on every redeploy — see
    # README.md "Deploying" for the volume setup steps.
    activity_db_path: str = "data/activity.db"

    # Used to look up each ticker's company logo (see
    # app/services/logos.py) — free-tier Logo.dev publishable key. Coverage
    # is very good but not universal (small-cap/unlisted names, or a
    # foreign exchange suffix Logo.dev doesn't itself recognize); a ticker
    # with no match just keeps the plain folder icon.
    logo_dev_api_key: str = ""

    # ChatGPT/Claude "bridge" connector secrets (see app/routers/bridge.py).
    # Each AI's custom connector is registered pointing at its own
    # /bridge/<secret>/mcp URL — the server infers which AI is calling
    # purely from which secret path was hit, since ChatGPT's custom-
    # connector UI has no plain bearer-token option (Claude's does, but a
    # URL-embedded secret works identically for both, so that's the one
    # mechanism used for both). Generate each with
    # `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` —
    # long, random, never committed, never logged. Leaving either blank
    # simply skips mounting that AI's bridge route.
    bridge_chatgpt_secret: str = ""
    bridge_claude_secret: str = ""

    # A second, fully isolated identity pair used only for continued
    # dev/testing (see app/routers/bridge.py) — same tools, own send/
    # receive queue, own Dropbox destination (dropbox_bridge_test_path
    # below), so testing never mixes with or lands in the user's real bridge
    # data. Leaving either blank simply skips mounting that test route.
    bridge_chatgpt_test_secret: str = ""
    bridge_claude_test_secret: str = ""

    # Where anything saved via the "_test" bridge identities lands —
    # always this one folder regardless of active/inactive/historicals,
    # since it's just for verifying rendering/behavior, not real ticker
    # organization.
    dropbox_bridge_test_path: str = "/Shared/Bridge Test"

    # Email ping to the user whenever a new bridge message shows up, so he
    # doesn't have to remember to go check (see app/services/notifications.py
    # and the poll loop in app/main.py). Sent via plain SMTP with a Gmail
    # app password — not a real subscription-based inbox, just enough to
    # send one-line notifications. Leaving bridge_notify_email blank
    # disables the whole notification loop.
    bridge_notify_email: str = ""
    bridge_notify_smtp_user: str = ""
    bridge_notify_smtp_app_password: str = ""
    bridge_notify_interval_seconds: int = 300

    class Config:
        env_file = "../.env"
        extra = "ignore"


settings = Settings()
