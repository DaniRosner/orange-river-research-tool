# FastAPI application entry point. Run with:
#   uvicorn app.main:app --reload
# Actual endpoint logic lives in app/routers/ (one file per resource:
# auth, tickers, files) — this file just wires the app together.

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import auth, bridge, email_intake, files, tickers
from app.services import bridge_store, notifications
from app.services.auth import current_user

logger = logging.getLogger(__name__)


async def _bridge_notification_loop():
    """Polls for bridge_messages nobody's been emailed about yet and pings
    the user — see app/services/notifications.py. Only started when
    notification settings are actually configured (see lifespan below)."""
    while True:
        await asyncio.sleep(settings.bridge_notify_interval_seconds)
        pending = bridge_store.fetch_unnotified()
        if not pending:
            continue
        if notifications.send_bridge_notification(pending):
            bridge_store.mark_notified([m["id"] for m in pending])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mounting bridge.chatgpt_app/claude_app via app.mount() below does NOT
    # auto-start their MCP session managers' task groups — each sub-app's
    # own lifespan has to be entered manually here, or every real bridge
    # request 500s with "Task group is not initialized." Skipped entirely
    # if a secret isn't configured (e.g. local dev), so bridge routes
    # simply aren't mounted rather than crashing startup.
    async with AsyncExitStack() as stack:
        if settings.bridge_chatgpt_secret:
            await stack.enter_async_context(bridge.chatgpt_app.router.lifespan_context(bridge.chatgpt_app))
        if settings.bridge_claude_secret:
            await stack.enter_async_context(bridge.claude_app.router.lifespan_context(bridge.claude_app))
        if settings.bridge_chatgpt_test_secret:
            await stack.enter_async_context(
                bridge.chatgpt_test_app.router.lifespan_context(bridge.chatgpt_test_app)
            )
        if settings.bridge_claude_test_secret:
            await stack.enter_async_context(bridge.claude_test_app.router.lifespan_context(bridge.claude_test_app))

        notify_task = None
        if notifications.notifications_enabled():
            notify_task = asyncio.create_task(_bridge_notification_loop())

        try:
            yield
        finally:
            if notify_task:
                notify_task.cancel()


app = FastAPI(title=settings.product_name, lifespan=lifespan)

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

# Deliberately NOT behind Depends(current_user) — these are called by
# ChatGPT/Claude's own infra, not a signed-in browser session; the
# URL-embedded secret (see app/config.py) is the auth boundary instead.
if settings.bridge_chatgpt_secret:
    app.mount(f"/bridge/{settings.bridge_chatgpt_secret}", bridge.chatgpt_app)
if settings.bridge_claude_secret:
    app.mount(f"/bridge/{settings.bridge_claude_secret}", bridge.claude_app)
if settings.bridge_chatgpt_test_secret:
    app.mount(f"/bridge/{settings.bridge_chatgpt_test_secret}", bridge.chatgpt_test_app)
if settings.bridge_claude_test_secret:
    app.mount(f"/bridge/{settings.bridge_claude_test_secret}", bridge.claude_test_app)

# Mailgun's inbound-route webhook (see app/routers/email_intake.py) — not
# behind Depends(current_user) (Mailgun's own servers call this, not a
# signed-in browser session) and not a secret-URL path either (unlike the
# bridge routes above, this one verifies each request's Mailgun signature
# inside the handler itself). Skipped entirely if no signing key is
# configured, same "optional integration" pattern as everything else here.
if settings.mailgun_webhook_signing_key:
    app.include_router(email_intake.router, prefix="/email-intake")


@app.get("/health")
def health_check():
    """Basic liveness check — used to confirm the server is up and
    responding, not to check Dropbox connectivity or anything deeper.
    Deliberately NOT behind auth — a deploy host's health check shouldn't
    need to be signed in."""
    return {"status": "ok"}
