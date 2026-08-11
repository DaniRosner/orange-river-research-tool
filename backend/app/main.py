# FastAPI application entry point. Run with:
#   uvicorn app.main:app --reload
# Actual endpoint logic lives in app/routers/ (one file per resource:
# auth, tickers, files) — this file just wires the app together.

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import auth, files, tickers
from app.services.auth import current_user

app = FastAPI(title="Research Tool")

# Signs the session cookie that /auth/* uses to remember who's signed in
# (see app/services/auth.py). same_site="none" + https_only=True because
# the frontend and backend are different origins (different ports in dev,
# likely different domains in production too) — a fetch() call across
# origins only carries the cookie at all if it's marked SameSite=None, and
# browsers only honor that on a cookie also marked Secure. Chrome/Firefox
# both treat "localhost" as a secure context even over plain HTTP, so this
# still works in local dev without needing a real TLS cert.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key, same_site="none", https_only=True)

# Allows the frontend (a separate origin during local dev, e.g.
# localhost:5173, and likely a separate origin in production too) to call
# this API from the browser, cookies included — allow_credentials=True is
# what lets the session cookie set by /auth/callback actually get sent on
# every later fetch() call. Origins are configured via
# BACKEND_CORS_ORIGINS in .env, not hardcoded here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
# Every ticker/file endpoint requires a signed-in, verified Your Firm
# team member (see current_user() in app/services/auth.py) — added here,
# once, at the router level, rather than repeated on every individual
# endpoint, so a new route added later can't accidentally ship unprotected.
app.include_router(tickers.router, dependencies=[Depends(current_user)])
app.include_router(files.router, dependencies=[Depends(current_user)])


@app.get("/health")
def health_check():
    """Basic liveness check — used to confirm the server is up and
    responding, not to check Dropbox connectivity or anything deeper.
    Deliberately NOT behind auth — a deploy host's health check shouldn't
    need to be signed in."""
    return {"status": "ok"}
