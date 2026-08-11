# Sign-in via a real per-user Dropbox login — see app/services/auth.py for
# the full reasoning (why this exists, how team verification works). The
# actual ticker/file endpoints don't live here; this is purely "who's
# allowed in at all."

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.services import auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login(request: Request):
    """Redirects the browser to Dropbox's own sign-in/consent screen. The
    frontend never talks to this endpoint via fetch — it's a real
    top-level navigation (e.g. `window.location.href = ...`), since the
    whole point is handing off to Dropbox's own page."""
    state = auth.new_csrf_state()
    request.session["oauth_state"] = state
    return RedirectResponse(auth.build_authorize_url(state))


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    """
    Where Dropbox redirects back to after the user approves (or cancels)
    sign-in. Verifies `state` matches what /login stored before ever
    trusting `code`, then hands off to auth.exchange_code_for_user() for
    the actual token exchange + team-membership check. On success, stores
    the resulting user info in the session and sends the browser on to the
    real frontend app (a different origin from this backend — see
    settings.frontend_url).
    """
    if error:
        raise HTTPException(status_code=400, detail=f"Dropbox sign-in was cancelled or failed: {error}")

    expected_state = request.session.pop("oauth_state", None)
    if not state or state != expected_state:
        raise HTTPException(status_code=400, detail="Sign-in request expired or was tampered with — please try again")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    user = auth.exchange_code_for_user(code)
    request.session["user"] = user
    return RedirectResponse(settings.frontend_url)


@router.get("/me")
def me(user: dict = Depends(auth.current_user)):
    """Current signed-in user's info, or 401 if no one's signed in — the
    frontend calls this once on load to decide whether to show the app or
    the sign-in screen."""
    return user


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}
