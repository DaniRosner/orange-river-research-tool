"""
Local MCP server — runs on the user's own machine (not deployed to Railway),
registered as a local (stdio) MCP connector in both ChatGPT Desktop and
Claude Desktop. Gives either AI one tool, upload_file, that takes a LOCAL
FILE PATH and a ticker name — never the file's actual content — and does
a real multipart upload to the Your Firm backend.

Why this exists: the AI-facing bridge (see backend/app/routers/bridge.py)
requires a file's bytes to be base64-encoded and passed as a literal tool
argument, which for real files (financial models, full memos) is slow and
occasionally unreliable — the model has to *generate* that base64 text
itself, and long random-looking strings aren't something models retype
perfectly. Both ChatGPT Desktop and Claude Desktop can now be granted
direct local-folder access and can write real files to disk correctly
(confirmed via live testing, 2026-08-17). This script closes the loop:
once a file is already sitting on disk, upload_file's only job is to pass
a short path string to this tool, which reads the real bytes off disk
itself and uploads them as a normal HTTP multipart request — no
base64/JSON step anywhere, so none of the reliability problems apply.

Setup (see README.md in this folder for the full walkthrough):
    Environment variables required:
      BRIDGE_BASE_URL  - e.g. https://research-tool-backend.up.railway.app
      BRIDGE_SECRET     - the SAME per-identity secret already configured
                          as BRIDGE_CHATGPT_SECRET or BRIDGE_CLAUDE_SECRET
                          on the backend (use the matching one for
                          whichever app this instance is registered in —
                          register this script TWICE, once per app, each
                          with its own secret).

Run directly for a local smoke test: BRIDGE_BASE_URL=... BRIDGE_SECRET=...
python research-tool_upload_mcp.py
"""

import os
import sys
from pathlib import Path

import requests
from mcp.server.mcpserver import MCPServer

BASE_URL = os.environ.get("BRIDGE_BASE_URL", "").rstrip("/")
SECRET = os.environ.get("BRIDGE_SECRET", "")

if not BASE_URL or not SECRET:
    print(
        "research-tool_upload_mcp: BRIDGE_BASE_URL and BRIDGE_SECRET must both be set "
        "as environment variables in this server's MCP config entry.",
        file=sys.stderr,
    )
    sys.exit(1)

server = MCPServer(
    name="research-tool-local-upload",
    instructions=(
        "One tool, upload_file, for saving a REAL file (financial model, polished "
        "PDF memo — anything you already wrote to a local shared folder) into the user's "
        "Dropbox. Pass the file's LOCAL PATH, not its content — this tool reads the "
        "bytes off disk itself. Only call this after the user has explicitly approved "
        "this exact file as final; writing a file to the shared folder is not by "
        "itself approval to save it. Same ticker rules as everywhere else in this "
        "system: an unrecognized ticker needs new_ticker_status "
        "('active'/'inactive'/'historicals') confirmed with the user first, and a likely "
        "typo of an existing ticker is always refused rather than guessed at."
    ),
)


@server.tool()
def upload_file(
    local_path: str,
    ticker: str,
    new_ticker_status: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Upload a real file already sitting on this computer into the
    correct ticker's Dropbox folder. `local_path` is the file's full path
    on disk (e.g. one you just wrote to the shared folder) — this tool
    reads its bytes directly, you never need to include the file's
    content in this call. `ticker` is matched the same way save_final
    matches it (typo-guard refuses a near-miss of an existing ticker
    rather than guessing; a genuinely new ticker needs new_ticker_status
    set to 'active', 'inactive', or 'historicals', confirmed with the user
    first). `overwrite` replaces an existing file of the same name if
    True (default False)."""
    path = Path(local_path).expanduser()
    if not path.is_file():
        return {"status": "error", "message": f"no file found at '{local_path}'."}

    with open(path, "rb") as handle:
        response = requests.post(
            f"{BASE_URL}/bridge/{SECRET}/upload-file",
            files={"file": (path.name, handle)},
            data={
                "ticker": ticker,
                "new_ticker_status": new_ticker_status or "",
                "overwrite": str(overwrite),
            },
            timeout=120,
        )

    if response.status_code != 200:
        return {
            "status": "error",
            "message": f"upload failed ({response.status_code}): {response.text[:500]}",
        }
    return response.json()


if __name__ == "__main__":
    server.run()
