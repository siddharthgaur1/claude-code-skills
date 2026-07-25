#!/usr/bin/env python3
"""A deliberately broken stdio MCP server — the failure fixture for the doctor.

It completes ``initialize`` normally (so the handshake itself isn't the fault),
then answers ``tools/list`` with a tool whose ``inputSchema`` is invalid: it
declares ``required: ["query"]`` but exposes no matching property, and uses a
wrong top-level ``type``. This exercises the doctor's per-tool schema validation
and its honest error reporting, not just the happy path.

Flip ``CRASH_ON_START`` to True to instead exercise the spawn/handshake-timeout
failure path (the process exits before answering initialize).
"""
import json
import sys

CRASH_ON_START = False
PROTOCOL_VERSION = "2025-06-18"

BAD_TOOLS = [
    {
        "name": "search",
        "description": "Broken schema on purpose.",
        "inputSchema": {
            "type": "string",              # wrong: MCP tool inputs are objects
            "required": ["query"],         # names a property that doesn't exist
        },
    }
]


def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main():
    if CRASH_ON_START:
        sys.stderr.write("boom: server crashed before initialize\n")
        sys.exit(1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            reply(msg["id"], {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "broken-fixture", "version": "0.0.1"},
            })
        elif method == "tools/list":
            reply(msg["id"], {"tools": BAD_TOOLS})
        elif method == "notifications/initialized":
            pass
        elif "id" in msg:
            reply(msg["id"], {})


if __name__ == "__main__":
    main()
