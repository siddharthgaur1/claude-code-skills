#!/usr/bin/env python3
"""MCP server doctor — pre-session health check for configured MCP servers.

WHY THIS EXISTS
---------------
A broken MCP server does not announce itself. The config parses fine, Claude
Code starts fine, and then twenty minutes into a task a tool call fails with a
cryptic transport error and the session is wasted. This script moves that
failure to *before* the session: it performs the real Model Context Protocol
JSON-RPC handshake against every configured server and reports, per server,
whether it actually answers.

SAFETY BOUNDARY (deliberate, permanent — not a TODO)
----------------------------------------------------
This tool NEVER invokes a tool (`tools/call`). Real MCP tools have real side
effects — deleting files, sending messages, mutating remote state — and calling
them with synthetic arguments to "test" them is unsafe. Diagnosis is limited to
what can be observed without side effects:

    1. Can we spawn / connect to the server?
    2. Does the `initialize` handshake complete within a timeout?
    3. Does `tools/list` return a well-formed response?
    4. Is each advertised tool's `inputSchema` structurally valid JSON Schema?

Whether a tool actually *works* is out of scope, by design. We report schema
validity, never behavioural correctness.

DESIGN CHOICES
--------------
- Stdlib only (Python 3.11+). The stdio MCP handshake is line-delimited
  JSON-RPC 2.0 over a subprocess's stdin/stdout — `subprocess` + `json` cover
  it. The `mcp` pip package is deliberately not a dependency; a doctor should
  not need the patient's own libraries installed to take a pulse.
- Reads on a background thread + queue rather than `select`, because pipe
  `select` does not work on Windows. This code runs on Windows and POSIX alike.
- All text I/O is `encoding="utf-8", errors="replace"`. A server that emits a
  stray non-UTF-8 byte must not crash the doctor with a decode error.
- Every failure path (spawn failure, timeout, non-JSON output, JSON-RPC error)
  surfaces the *real* underlying message. Nothing is swallowed into a generic
  "failed", and nothing is ever assumed healthy.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"  # verified against modelcontextprotocol.io spec
CLIENT_INFO = {"name": "mcp-server-doctor", "version": "1.0.0"}


# --------------------------------------------------------------------------- #
# Schema validation (structural only — never behavioural)
# --------------------------------------------------------------------------- #
def validate_input_schema(schema: object) -> list[str]:
    """Return a list of structural problems with a tool's ``inputSchema``.

    We check only what JSON Schema basics guarantee about shape, not whether the
    tool behaves correctly. An empty list means "structurally sane".
    """
    issues: list[str] = []
    if not isinstance(schema, dict):
        return [f"inputSchema is not an object (got {type(schema).__name__})"]

    stype = schema.get("type")
    if stype is None:
        issues.append("inputSchema has no 'type'")
    elif stype != "object":
        # MCP tool inputs are argument bags; the spec always shows type:object.
        issues.append(f"inputSchema 'type' is {stype!r}, expected 'object'")

    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        issues.append(f"'properties' is not an object (got {type(props).__name__})")

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list):
            issues.append(f"'required' is not an array (got {type(required).__name__})")
        else:
            known = set(props) if isinstance(props, dict) else set()
            for name in required:
                if not isinstance(name, str):
                    issues.append(f"'required' entry {name!r} is not a string")
                elif isinstance(props, dict) and name not in known:
                    issues.append(f"'required' names {name!r}, absent from 'properties'")
    return issues


def _summarize_tools(tools: object) -> tuple[list[dict], list[str]]:
    """Turn a raw ``tools/list`` result array into per-tool diagnostics."""
    out: list[dict] = []
    problems: list[str] = []
    if not isinstance(tools, list):
        return out, [f"'tools' is not an array (got {type(tools).__name__})"]
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            problems.append(f"tool[{i}] is not an object")
            continue
        name = tool.get("name", f"<unnamed #{i}>")
        if "inputSchema" not in tool:
            issues = ["tool has no 'inputSchema'"]
        else:
            issues = validate_input_schema(tool["inputSchema"])
        out.append({"name": name, "schema_ok": not issues, "schema_issues": issues})
    return out, problems


def _apply_tools(result: dict, raw_tools: object) -> None:
    """Fold a ``tools/list`` array into ``result``, deciding overall health.

    A server is healthy only if the list is well-formed AND every advertised
    tool has a structurally valid schema. Per-tool detail is retained either
    way — we never blank the whole server just because one tool is malformed.
    """
    tools, list_problems = _summarize_tools(raw_tools)
    result["tools"] = tools
    if list_problems:
        result["error"] = "; ".join(list_problems)
        return
    bad = [t for t in tools if not t["schema_ok"]]
    if bad:
        detail = "; ".join(f"{t['name']}: {', '.join(t['schema_issues'])}" for t in bad)
        result["error"] = f"{len(bad)} tool(s) with invalid inputSchema - {detail}"
        return
    result["ok"] = True


# --------------------------------------------------------------------------- #
# stdio transport
# --------------------------------------------------------------------------- #
class MCPProtocolError(Exception):
    """A JSON-RPC error response or a malformed reply from the server."""


class StdioClient:
    """Minimal line-delimited JSON-RPC client over a subprocess's stdio.

    Reads run on a daemon thread feeding a queue so that a per-request timeout
    works identically on Windows (where pipe ``select`` is unavailable) and
    POSIX. The subprocess is always terminated in ``close``.
    """

    def __init__(self, command: str, args: list[str], env: dict | None):
        merged = os.environ.copy()
        if env:
            merged.update({k: str(v) for k, v in env.items()})
        # shell=False; command/args come from the user's own config, not input.
        self.proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
        )
        self._q: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._next_id = 0

    def _pump(self) -> None:
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                self._q.put(line)
        except Exception:
            pass
        finally:
            self._q.put("")  # sentinel: stream closed / process died

    def _send(self, message: dict) -> None:
        if self.proc.stdin is None or self.proc.poll() is not None:
            raise MCPProtocolError("server process is not running (stdin closed)")
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None, timeout: float) -> dict:
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no response to {method!r} within {timeout:.0f}s")
            try:
                line = self._q.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"no response to {method!r} within {timeout:.0f}s")
            if line == "":
                raise MCPProtocolError(
                    f"server closed stdout before answering {method!r}"
                    + self._stderr_tail()
                )
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                raise MCPProtocolError(f"non-JSON line on stdout: {line[:200]!r} ({e})")
            if msg.get("id") != req_id:
                continue  # a notification or an unrelated id; keep reading
            if "error" in msg:
                err = msg["error"]
                raise MCPProtocolError(
                    f"JSON-RPC error {err.get('code')}: {err.get('message')}"
                )
            return msg.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _stderr_tail(self) -> str:
        try:
            data = self.proc.stderr.read() if self.proc.stderr else ""
        except Exception:
            data = ""
        data = (data or "").strip()
        return f" - stderr: {data[-300:]}" if data else ""

    def close(self) -> None:
        """Terminate the child unconditionally. Never leak a process."""
        try:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _initialize(request, notify, timeout: float) -> dict:
    """Run initialize + initialized against a request/notify pair; return info."""
    result = request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        },
        timeout,
    )
    notify("notifications/initialized")
    return result


def check_stdio(name: str, cfg: dict, timeout: float) -> dict:
    result = {"name": name, "transport": "stdio", "ok": False, "error": None,
              "server_info": None, "protocol_version": None, "tools": []}
    command = cfg.get("command")
    if not command:
        result["error"] = "stdio server config has no 'command'"
        return result
    args = cfg.get("args", []) or []
    started = time.monotonic()
    client = None
    try:
        try:
            client = StdioClient(command, args, cfg.get("env"))
        except FileNotFoundError:
            result["error"] = f"command not found: {command!r} (is it installed / on PATH?)"
            return result
        except OSError as e:
            result["error"] = f"failed to spawn {command!r}: {e}"
            return result

        init = _initialize(client.request, client.notify, timeout)
        result["server_info"] = init.get("serverInfo")
        result["protocol_version"] = init.get("protocolVersion")

        listed = client.request("tools/list", {}, timeout)
        _apply_tools(result, listed.get("tools"))
    except (MCPProtocolError, TimeoutError) as e:
        result["error"] = str(e)
    except Exception as e:  # last resort: still surface the real message
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        if client:
            client.close()
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


# --------------------------------------------------------------------------- #
# HTTP / SSE transport (streamable HTTP)
# --------------------------------------------------------------------------- #
class HTTPClient:
    """JSON-RPC over MCP streamable HTTP, using only urllib.

    A single endpoint accepts POSTed JSON-RPC. The response is either a JSON
    object or an SSE stream (``text/event-stream``); we handle both by pulling
    the JSON out of ``data:`` lines when needed. A session id, if the server
    issues one, is echoed back on subsequent requests.
    """

    def __init__(self, url: str, headers: dict | None):
        self.url = url
        self.base_headers = {k: str(v) for k, v in (headers or {}).items()}
        self.session_id: str | None = None
        self._next_id = 0

    def _post(self, payload: dict, timeout: float) -> object:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.base_headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            raw = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
        return _parse_http_body(raw, ctype)

    def request(self, method: str, params: dict | None, timeout: float) -> dict:
        self._next_id += 1
        req_id = self._next_id
        try:
            msg = self._post(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
                timeout,
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise MCPProtocolError(f"HTTP {e.code} {e.reason}: {detail}".strip())
        except urllib.error.URLError as e:
            raise MCPProtocolError(f"cannot reach {self.url}: {e.reason}")
        if not isinstance(msg, dict):
            raise MCPProtocolError(f"unparseable HTTP response to {method!r}")
        if "error" in msg:
            err = msg["error"]
            raise MCPProtocolError(f"JSON-RPC error {err.get('code')}: {err.get('message')}")
        return msg.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, 5)
        except Exception:
            pass  # notifications are fire-and-forget

    def close(self) -> None:
        pass  # nothing persistent to clean up


def _parse_http_body(raw: str, ctype: str) -> object:
    """Extract a JSON-RPC message from a JSON or SSE HTTP body."""
    raw = raw.strip()
    if not raw:
        return None
    if "text/event-stream" in ctype or raw.startswith("event:") or raw.startswith("data:"):
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def check_http(name: str, cfg: dict, timeout: float) -> dict:
    result = {"name": name, "transport": "http", "ok": False, "error": None,
              "server_info": None, "protocol_version": None, "tools": []}
    url = cfg.get("url")
    started = time.monotonic()
    client = HTTPClient(url, cfg.get("headers"))
    try:
        init = _initialize(client.request, client.notify, timeout)
        result["server_info"] = init.get("serverInfo")
        result["protocol_version"] = init.get("protocolVersion")
        listed = client.request("tools/list", {}, timeout)
        _apply_tools(result, listed.get("tools"))
    except (MCPProtocolError, TimeoutError) as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        client.close()
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


# --------------------------------------------------------------------------- #
# Config + orchestration
# --------------------------------------------------------------------------- #
def load_servers(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: no 'mcpServers' object found")
    return servers


def check_server(name: str, cfg: dict, timeout: float) -> dict:
    if not isinstance(cfg, dict):
        return {"name": name, "transport": "?", "ok": False,
                "error": "server entry is not an object", "tools": []}
    transport = cfg.get("type")
    if cfg.get("url") and transport in (None, "http", "sse", "streamable-http"):
        return check_http(name, cfg, timeout)
    if cfg.get("command") or transport == "stdio":
        return check_stdio(name, cfg, timeout)
    return {"name": name, "transport": transport or "?", "ok": False,
            "error": "cannot determine transport (no 'command' and no 'url')", "tools": []}


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def print_human(results: list[dict]) -> None:
    for r in results:
        badge = "OK  " if r["ok"] else "FAIL"
        elapsed = f"{r.get('elapsed_ms', 0)}ms"
        info = r.get("server_info") or {}
        who = f" [{info.get('name')} {info.get('version', '')}]".rstrip() if info else ""
        print(f"{badge}  {r['name']}  ({r['transport']}, {elapsed}){who}")
        if r["ok"]:
            tools = r["tools"]
            print(f"      {len(tools)} tool(s), protocol {r.get('protocol_version')}")
            for t in tools:
                mark = "ok" if t["schema_ok"] else "SCHEMA ISSUE"
                print(f"        - {t['name']}  [{mark}]")
                for issue in t["schema_issues"]:
                    print(f"            ! {issue}")
        else:
            print(f"      error: {r['error']}")
            # a server can hand back tools yet still fail on a schema/list problem
            for t in r.get("tools", []):
                if not t["schema_ok"]:
                    print(f"        - {t['name']}: {'; '.join(t['schema_issues'])}")
    ok = sum(1 for r in results if r["ok"])
    print(f"\n{ok}/{len(results)} server(s) healthy.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose configured MCP servers via the real JSON-RPC "
                    "handshake. Never invokes tools (see safety boundary).")
    parser.add_argument("--config", default=".mcp.json",
                        help="path to MCP config (default: ./.mcp.json)")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="per-request timeout in seconds (default: 10)")
    parser.add_argument("--server", metavar="NAME",
                        help="check only this server")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON instead of text")
    args = parser.parse_args(argv)

    try:
        servers = load_servers(args.config)
    except FileNotFoundError:
        print(f"config not found: {args.config}", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        print(f"cannot read config: {e}", file=sys.stderr)
        return 2

    if args.server:
        if args.server not in servers:
            print(f"no server named {args.server!r} in {args.config}", file=sys.stderr)
            return 2
        servers = {args.server: servers[args.server]}

    if not servers:
        print("no servers configured.", file=sys.stderr)
        return 2

    results = [check_server(name, cfg, args.timeout) for name, cfg in servers.items()]

    if args.json:
        print(json.dumps({"results": results,
                          "healthy": all(r["ok"] for r in results)}, indent=2))
    else:
        print_human(results)

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
