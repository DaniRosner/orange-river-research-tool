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

    class Config:
        env_file = "../.env"
        extra = "ignore"


settings = Settings()
