# Local upload MCP server

`research-tool_upload_mcp.py` runs on the user's own computer — not deployed
to Railway — and gives ChatGPT Desktop and Claude Desktop one extra tool,
`upload_file`, for saving a real file (financial model, PDF memo) that's
already sitting on disk into the correct ticker's Dropbox folder. It
exists because the cloud-hosted bridge's `save_final` requires the AI to
retype a file's content as base64 text, which is unreliable for real
binary files — this tool instead takes just a local file path and reads
the bytes itself, sidestepping that problem entirely. See the module
docstring in `research-tool_upload_mcp.py` for the full rationale.

**Prerequisite:** both ChatGPT Desktop and Claude Desktop need local
folder access already set up and pointed at the *same* shared folder —
that's what lets an AI write a real file to disk in the first place. This
tool picks up from there.

## Setup

1. Make sure Python 3.11+ and `pip` are available on the user's machine.
2. Install the two dependencies this script needs:
   ```bash
   pip install mcp requests
   ```
3. Note the folder this file lives in — you'll need its full path for the
   config below (e.g. `/Users/the user/research-tool-local-tools`).
4. You'll register this **same script twice** — once in ChatGPT Desktop's
   local MCP config, once in Claude Desktop's — each with its own
   `BRIDGE_SECRET` (the identity is implied entirely by which secret is
   used, exactly like the cloud bridge's two separate connector URLs).

### Claude Desktop

Add an entry to `claude_desktop_config.json` (Settings → Developer →
Edit Config, or the file directly):

```json
{
  "mcpServers": {
    "research-tool-upload": {
      "command": "python3",
      "args": ["/full/path/to/research-tool_upload_mcp.py"],
      "env": {
        "BRIDGE_BASE_URL": "https://<your-backend-domain>",
        "BRIDGE_SECRET": "<the same value as BRIDGE_CLAUDE_SECRET on Railway>"
      }
    }
  }
}
```

### ChatGPT Desktop

Add the equivalent entry to ChatGPT Desktop's local MCP server config
(same JSON shape, shared with Codex CLI's config — see ChatGPT Desktop's
settings for the exact file location, which varies by version), using
`BRIDGE_CHATGPT_SECRET`'s value instead:

```json
{
  "mcpServers": {
    "research-tool-upload": {
      "command": "python3",
      "args": ["/full/path/to/research-tool_upload_mcp.py"],
      "env": {
        "BRIDGE_BASE_URL": "https://<your-backend-domain>",
        "BRIDGE_SECRET": "<the same value as BRIDGE_CHATGPT_SECRET on Railway>"
      }
    }
  }
}
```

## Using it

Once registered, ask the AI to (1) write the finished file to the shared
folder — already confirmed working — then (2) call `upload_file` with
that file's local path and the ticker. It never needs to see or retype
the file's content; only a short path string and a ticker name.

## Smoke-testing it yourself

```bash
BRIDGE_BASE_URL=https://<your-backend-domain> BRIDGE_SECRET=<a real secret> \
  python3 research-tool_upload_mcp.py
```

This starts the server on stdio and waits — it's meant to be launched by
Claude/ChatGPT Desktop, not run standalone for real use, but this
confirms it starts without errors (missing env vars exit immediately with
a clear message).
