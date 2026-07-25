---
name: mcp-server-doctor
description: >-
  Diagnose configured MCP (Model Context Protocol) servers before a session
  starts — real JSON-RPC handshake, tool-schema validation, and a clear
  healthy/broken verdict per server, with the real error surfaced instead of a
  cryptic mid-task transport failure. Use this skill whenever the user wants
  to check, debug, validate, or troubleshoot MCP servers or `.mcp.json` /
  `mcpServers` config — including "why isn't this MCP tool showing up", "is my
  server config right", "test my MCP setup", or "a tool is failing and I don't
  know why" — even if they don't say "MCP" explicitly and just describe a tool
  that silently doesn't work.
---

# MCP server doctor

MCP servers fail quietly. The config parses, Claude Code starts, and twenty
minutes into a task a tool call dies with a transport error that gives no clue
whether the problem is the command path, a crashed process, or a malformed
schema. This skill moves that failure earlier: it performs the real MCP
handshake against every configured server *before* the session needs them,
and reports exactly what broke.

**Safety boundary — read this first.** This tool never calls a tool
(`tools/call`). Real MCP tools have real side effects, and invoking one with
made-up arguments to "test" it is not a safe kind of test. Diagnosis is
limited to what can be observed with zero side effects: can we connect, does
`initialize` complete, does `tools/list` respond, is each tool's
`inputSchema` structurally valid JSON Schema. Whether a tool's logic is
*correct* is out of scope, permanently — that's what actually using it is for.

## When to reach for this

- Before starting a session that depends on MCP servers you haven't used
  recently, or just installed.
- After editing a `.mcp.json` / `mcpServers` config, to confirm the edit
  didn't break anything.
- When a tool silently doesn't appear, or Claude reports a tool error you
  can't otherwise explain.
- As a CI/pre-flight gate for a repo that ships its own `.mcp.json`.

## Workflow

### Step 1 — Point it at a config

```bash
python -m scripts.doctor --config .mcp.json
```

Defaults to `./.mcp.json`. Works with both transports a config entry can
declare:
- **stdio** (`"command"` + `"args"`) — spawns the process and speaks
  line-delimited JSON-RPC over its stdin/stdout.
- **HTTP / streamable HTTP / SSE** (`"url"`) — POSTs JSON-RPC and unwraps
  either a plain JSON or an SSE-framed response.

### Step 2 — Read the verdict

Each server gets one of two outcomes, never a guess in between:

- `OK` — handshake completed, every tool's schema is structurally valid.
- `FAIL` — the real error is printed: command not found, spawn failure,
  handshake timeout, a JSON-RPC error response, or one-or-more tools with a
  concrete schema problem (e.g. `inputSchema 'type' is 'string', expected
  'object'`).

A server that half-answers (say, `tools/list` succeeds but one tool's schema
is broken) is still reported `FAIL` — but its other tools' diagnostics are
retained, not hidden, so you know exactly how much of the server is usable.

### Step 3 — Fix and re-run

- **Command not found** → check the `command` path / that the package is
  installed and on `PATH`.
- **Timeout on `initialize`** → the process started but never answered; check
  it isn't waiting on stdin for something else, or raise `--timeout`.
- **Schema issue** → the server's own tool definitions need fixing; this is
  usually a bug in that MCP server, not your config.

### Step 4 — Wire into CI (optional)

Exit code is `0` only if every checked server is healthy:

```bash
python -m scripts.doctor --config .mcp.json --json > mcp-health.json
```

## Reference files

- `scripts/doctor.py` — the whole tool. Read the module docstring for the
  full design rationale (why stdlib-only, why threads instead of `select`,
  why every failure path preserves the real message).
- `examples/echo_server.py` — a minimal, correctly-implemented fixture server
  (one tool, valid schema) — what "healthy" looks like end to end.
- `examples/broken_server.py` — a fixture server with a deliberately invalid
  tool schema, to exercise the failure-reporting path.
- `examples/example.mcp.json` — wires both fixtures in; run the doctor
  against it to see the tool work without touching a real server.

## Principles (why this is built the way it is)

- **Never invoke a tool.** Diagnosis stops at the boundary where a real side
  effect could occur. This is not a coverage gap to close later — it's the
  point.
- **No fabricated verdicts.** A server is `OK` only if every check actually
  passed. Anything ambiguous, timed out, or partially answered is `FAIL` with
  the real reason attached.
- **The real error, always.** Spawn failures, timeouts, malformed JSON, and
  JSON-RPC errors are never collapsed into a generic "something went wrong."
- **Stdlib only.** A doctor shouldn't need the patient's own dependencies
  installed to take a pulse.
- **No leaked processes.** Every spawned server is terminated in a `finally`,
  on every exit path, including timeout.
