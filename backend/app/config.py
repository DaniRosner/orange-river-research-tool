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

    # Where the app looks for Active/Inactive/Historicals/Needs Review
    # folders. Points at the Dev Sandbox during development; switch to the
    # real /Shared paths (e.g. /Shared/Active) when going live.
    dropbox_active_path: str = "/Shared/Dev Sandbox/Active"
    dropbox_inactive_path: str = "/Shared/Dev Sandbox/Inactive"
    dropbox_historicals_path: str = "/Shared/Dev Sandbox/Historicals"
    dropbox_needs_review_path: str = "/Shared/Dev Sandbox/Needs Review"

    class Config:
        env_file = "../.env"
        extra = "ignore"


settings = Settings()
