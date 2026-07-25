#!/usr/bin/env python3
"""A minimal, correct stdio MCP server — the healthy fixture for the doctor.

Stdlib only. Speaks line-delimited JSON-RPC 2.0 on stdin/stdout and implements
exactly the three things the doctor exercises: ``initialize``,
``notifications/initialized``, and ``tools/list``. It advertises one trivial
tool (``echo``) with a valid ``inputSchema``.

It intentionally does NOT implement ``tools/call`` in any meaningful way — the
doctor never calls tools, so a health check must pass without it.
"""
import json
import sys

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "echo",
        "title": "Echo",
        "description": "Return the text you send it.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "text to echo"}},
            "required": ["text"],
        },
    }
]


def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main():
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
                "serverInfo": {"name": "echo-fixture", "version": "1.0.0"},
            })
        elif method == "tools/list":
            reply(msg["id"], {"tools": TOOLS})
        elif method == "notifications/initialized":
            pass  # notification: no response
        elif "id" in msg:
            reply(msg["id"], {})  # benign default for anything else with an id


if __name__ == "__main__":
    main()
