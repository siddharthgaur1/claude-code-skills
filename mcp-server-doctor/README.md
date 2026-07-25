# mcp-server-doctor

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skill** that
diagnoses configured MCP (Model Context Protocol) servers *before* a session
needs them — a real JSON-RPC handshake, tool-schema validation, and a plain
`OK`/`FAIL` verdict per server, with the real underlying error instead of a
cryptic mid-task transport failure.

MCP servers fail quietly: the config parses, the session starts, and the
first sign of trouble is a tool call dying twenty minutes in. This runs the
same handshake Claude Code would, up front, and tells you exactly what's
wrong — command not found, handshake timeout, or a malformed tool schema.

**Safety boundary, permanent:** this tool never calls a tool (`tools/call`).
Real MCP tools have real side effects. Diagnosis is limited to what can be
observed without any: can we connect, does `initialize` succeed, does
`tools/list` respond, is every tool's `inputSchema` structurally valid.
Whether a tool's logic is *correct* is out of scope by design.

## What's a skill?

A skill is a folder with a `SKILL.md` that teaches Claude Code a repeatable
workflow. Drop this folder into your Claude Code skills directory, and asking
Claude to "check my MCP config" or "why isn't this tool showing up" will
invoke this workflow. `scripts/doctor.py` is real, runnable code — not
pseudocode.

## Use it standalone (no Claude Code required)

Zero third-party dependencies (Python 3.11+):

```bash
python -m scripts.doctor --config examples/example.mcp.json
```

```
OK    echo-healthy  (stdio, 62ms) [echo-fixture 1.0.0]
      1 tool(s), protocol 2025-06-18
        - echo  [ok]
FAIL  search-broken  (stdio, 78ms) [broken-fixture 0.0.1]
      error: 1 tool(s) with invalid inputSchema - search: inputSchema 'type' is 'string', expected 'object'
        - search: inputSchema 'type' is 'string', expected 'object'

1/2 server(s) healthy.
```

Point `--config` at a real `.mcp.json`. Supports both transports an MCP
config entry can declare — stdio (`command`/`args`) and HTTP/SSE (`url`).
`--json` for machine-readable output, `--server NAME` to check just one,
`--timeout` to adjust the per-request wait. Exit code is `0` only if every
checked server is healthy, so it's CI-gateable.

## Layout

```
mcp-server-doctor/
├── SKILL.md                  # the skill: workflow Claude Code follows
├── scripts/
│   └── doctor.py             # handshake client (stdio + HTTP) + schema checks
└── examples/
    ├── echo_server.py        # correctly-implemented fixture (what OK looks like)
    ├── broken_server.py      # deliberately invalid tool schema (what FAIL looks like)
    └── example.mcp.json      # wires both fixtures together
```

## Design principles

- **Never invoke a tool** — diagnosis stops at the boundary where a real side
  effect could occur.
- **No fabricated verdicts** — `OK` only if every check genuinely passed;
  anything ambiguous is `FAIL` with the real reason.
- **The real error, always** — spawn failures, timeouts, malformed JSON, and
  JSON-RPC errors are never collapsed into a generic message.
- **Stdlib only** — a doctor shouldn't need the patient's own dependencies
  installed to take a pulse.
- **No leaked processes** — every spawned server is terminated on every exit
  path, including timeout.

## License

MIT — see [LICENSE](LICENSE).
