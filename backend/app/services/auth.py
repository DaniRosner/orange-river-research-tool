"""
Per-user Dropbox sign-in — distinct from dropbox_client.py's single
long-lived service-account refresh token, which is still what every
ticker/file operation actually runs as (the team's shared research
folders are the same for everyone signed in, so there's no need to run
each operation "as" the specific logged-in user). This module exists
purely to answer "is a real, verified Your Firm team member currently
signed in" — gating every route in tickers.py/files.py via current_user()
below.

Verification works by checking the signed-in Dropbox account's team ID
against the same team the service account itself belongs to (see
dropbox_client.get_team_id()) — so who's allowed to use this app is
automatically exactly "whoever IT/the user has already given a real Dropbox
seat to," with no separate access list to build or maintain here. Someone
signing in with an unrelated personal Dropbox account is rejected outright.

Deliberately implemented with plain HTTP calls to Dropbox's OAuth
endpoints rather than the SDK's DropboxOAuth2Flow helper — that class
expects to read/write a dict-like session object across two separate
requests (the redirect to Dropbox, then the callback) in a way that's
awkward to line up with FastAPI's request-scoped model. Hand-rolling the
two HTTP calls (authorize URL, then the code-for-token exchange) is
simpler to reason about here.
"""

import secrets

import dropbox
import requests
from fastapi import HTTPException, Request

from app.config import settings
from app.services import dropbox_client

DROPBOX_AUTHORIZE_URL = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"


def new_csrf_state() -> str:
    """A one-time random value stored in the session before redirecting to
    Dropbox, and checked back against the callback's `state` param — this
    is what stops a forged callback request (missing or wrong state) from
    logging someone in as an arbitrary account."""
    return secrets.token_urlsafe(24)


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.dropbox_app_key,
        "redirect_uri": settings.dropbox_redirect_uri,
        "response_type": "code",
        "state": state,
        # "online" — a short-lived access token is all this needs; the app
        # only uses it once, immediately, to read the signer's name/team
        # and then discards it. No refresh token, nothing kept around.
        "token_access_type": "online",
    }
    query = "&".join(f"{key}={requests.utils.quote(str(value))}" for key, value in params.items())
    return f"{DROPBOX_AUTHORIZE_URL}?{query}"


def exchange_code_for_user(code: str) -> dict:
    """
    Exchanges an OAuth authorization code for the signed-in user's own
    short-lived access token, then immediately uses it to read their
    account info (name, email, team) — the access token itself is never
    stored anywhere; only the resulting {name, full_name, email} goes into
    the session.

    Raises HTTPException(403) if the account isn't on Your Firm's own
    Dropbox team — this is the actual access-control check, not just
    "successfully logged into some Dropbox account."
    """
    response = requests.post(
        DROPBOX_TOKEN_URL,
        data={
            "code": code,
            "grant_type": "authorization_code",
            "client_id": settings.dropbox_app_key,
            "client_secret": settings.dropbox_app_secret,
            "redirect_uri": settings.dropbox_redirect_uri,
        },
        timeout=15,
    )
    response.raise_for_status()
    user_access_token = response.json()["access_token"]

    user_client = dropbox.Dropbox(oauth2_access_token=user_access_token)
    account = user_client.users_get_current_account()

    team_id = account.team.id if account.team else None
    if team_id is None or team_id != dropbox_client.get_team_id():
        raise HTTPException(
            status_code=403,
            detail=f"This Dropbox account isn't part of {settings.client_display_name}'s team — access denied.",
        )

    return {
        "name": account.name.given_name,
        "full_name": account.name.display_name,
        "email": account.email,
    }


def current_user(request: Request) -> dict:
    """
    FastAPI dependency: the signed-in user's info from the session, or a
    401 if no one's signed in. Add this as a dependency on every route
    that should require a real, verified team member — which is every
    ticker/file endpoint (see tickers.py/files.py) except the auth routes
    themselves.
    """
    user = request.session.get("user")
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
