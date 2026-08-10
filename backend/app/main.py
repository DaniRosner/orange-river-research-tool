# FastAPI application entry point. Run with:
#   uvicorn app.main:app --reload
# Actual endpoint logic lives in app/routers/ (one file per resource:
# tickers, files) — this file just wires the app together.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import files, tickers

app = FastAPI(title="Research Tool")

# Allows the frontend (a separate origin during local dev, e.g.
# localhost:5173) to call this API from the browser. Origins are
# configured via BACKEND_CORS_ORIGINS in .env, not hardcoded here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickers.router)
app.include_router(files.router)


@app.get("/health")
def health_check():
    """Basic liveness check — used to confirm the server is up and
    responding, not to check Dropbox connectivity or anything deeper."""
    return {"status": "ok"}
