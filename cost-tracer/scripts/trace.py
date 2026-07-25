"""Trace where a Claude Code session spent its token budget.

    python -m scripts.trace --transcript path/to/session.jsonl
    python -m scripts.trace --session-id <uuid> --out report.html --json

Parses a Claude Code JSONL transcript and attributes cost: totals by token
kind, a per-tool breakdown, the largest individual tool results (the bytes
that actually got read back into context), and a turn-by-turn timeline.

Everything runs locally. No transcript content ever leaves the machine and
nothing is uploaded anywhere.

Zero third-party dependencies — Python 3.11+ stdlib only.

Honesty boundary: result "cost" is measured in UTF-8 *bytes of the result
payload*, not tokens — there is no tokenizer here. Bytes are the proxy for
"how much this result taxes the next turn's input". A rough token figure
(bytes // 4) is shown alongside and is clearly labelled an estimate. Lines
with no `usage` block contribute 0 to token totals and are counted and
reported as such — never interpolated or hidden.
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys
from pathlib import Path

# Windows consoles default to cp1252; transcript content is arbitrary Unicode,
# so printing it (text or --json) would raise UnicodeEncodeError. Force UTF-8
# stdout. (File writes already pass encoding="utf-8" explicitly.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

TOKEN_KINDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _result_bytes(content) -> int:
    """UTF-8 byte size of a tool_result payload (str or list of blocks)."""
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    # list of content blocks, or anything else — serialize deterministically
    return len(json.dumps(content, ensure_ascii=False).encode("utf-8"))


def _result_preview(content, limit: int = 240) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(str(b.get("text") or b.get("content") or b.get("type") or ""))
            else:
                parts.append(str(b))
        text = " ".join(parts)
    else:
        text = str(content)
    text = " ".join(text.split())  # collapse whitespace
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_transcript(path: str) -> dict:
    """Walk the JSONL and build a cost-attribution model. Never crashes on a
    single bad or unexpected line."""
    totals = {k: 0 for k in TOKEN_KINDS}
    tool_names: dict[str, str] = {}           # tool_use_id -> tool name
    tool_calls: dict[str, int] = {}           # tool name -> call count
    tool_bytes: dict[str, int] = {}           # tool name -> attributed result bytes
    largest: list[dict] = []                  # individual tool results
    timeline: list[dict] = []                 # per assistant turn with usage
    orphan_result_bytes = 0                   # results whose tool_use we never saw

    lines_total = 0
    bad_json = 0
    assistant_no_usage = 0
    turn = 0
    cumulative = 0

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            lines_total += 1
            try:
                obj = json.loads(line)
            except Exception:
                bad_json += 1
                continue
            if not isinstance(obj, dict):
                continue

            typ = obj.get("type")
            msg = obj.get("message")
            msg = msg if isinstance(msg, dict) else {}
            ts = obj.get("timestamp")

            if typ == "assistant":
                # register tool_use names for later attribution
                content = msg.get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            name = b.get("name") or "<unknown>"
                            tid = b.get("id")
                            if tid:
                                tool_names[tid] = name
                            tool_calls[name] = tool_calls.get(name, 0) + 1

                usage = msg.get("usage")
                if isinstance(usage, dict) and any(k in usage for k in TOKEN_KINDS):
                    turn += 1
                    row = {k: int(usage.get(k, 0) or 0) for k in TOKEN_KINDS}
                    for k in TOKEN_KINDS:
                        totals[k] += row[k]
                    turn_total = sum(row.values())
                    cumulative += turn_total
                    timeline.append({
                        "turn": turn,
                        "timestamp": ts,
                        "turn_tokens": turn_total,
                        "cumulative_tokens": cumulative,
                        **row,
                    })
                else:
                    assistant_no_usage += 1

            elif typ == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            tid = b.get("tool_use_id")
                            name = tool_names.get(tid, "<unmatched>")
                            size = _result_bytes(b.get("content"))
                            tool_bytes[name] = tool_bytes.get(name, 0) + size
                            if name == "<unmatched>":
                                orphan_result_bytes += size
                            largest.append({
                                "tool": name,
                                "bytes": size,
                                "is_error": bool(b.get("is_error")),
                                "preview": _result_preview(b.get("content")),
                                "timestamp": ts,
                            })
            # all other types (mode, attachment, system, summary, ...) ignored

    largest.sort(key=lambda r: r["bytes"], reverse=True)

    per_tool = []
    for name in sorted(set(tool_calls) | set(tool_bytes),
                       key=lambda n: tool_bytes.get(n, 0), reverse=True):
        per_tool.append({
            "tool": name,
            "calls": tool_calls.get(name, 0),
            "result_bytes": tool_bytes.get(name, 0),
            "est_tokens": tool_bytes.get(name, 0) // 4,
        })

    total_result_bytes = sum(tool_bytes.values())
    return {
        "transcript": os.path.abspath(path),
        "totals": totals,
        "grand_total_tokens": sum(totals.values()),
        "per_tool": per_tool,
        "largest_results": largest,
        "timeline": timeline,
        "result_bytes_total": total_result_bytes,
        "result_tokens_estimate": total_result_bytes // 4,
        "stats": {
            "lines_total": lines_total,
            "bad_json_lines": bad_json,
            "assistant_lines_without_usage": assistant_no_usage,
            "tool_calls_total": sum(tool_calls.values()),
            "tool_results_total": len(largest),
            "orphan_result_bytes": orphan_result_bytes,
        },
    }


# ---------------------------------------------------------------------------
# Locate transcript by session id
# ---------------------------------------------------------------------------

def find_by_session_id(session_id: str) -> str:
    home = Path(os.path.expanduser("~"))
    pattern = str(home / ".claude" / "projects" / "*" / f"{session_id}.jsonl")
    matches = glob.glob(pattern)
    if not matches:
        raise SystemExit(f"No transcript found for session id {session_id!r} under {pattern}")
    if len(matches) > 1:
        raise SystemExit(f"Ambiguous session id — {len(matches)} matches:\n" + "\n".join(matches))
    return matches[0]


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def _fmt(n: int) -> str:
    return f"{n:,}"


def render_text(model: dict) -> str:
    t = model["totals"]
    s = model["stats"]
    out = []
    out.append(f"Transcript: {model['transcript']}")
    out.append("")
    out.append("TOKEN TOTALS (from usage blocks — authoritative)")
    out.append(f"  input            {_fmt(t['input_tokens']):>14}")
    out.append(f"  output           {_fmt(t['output_tokens']):>14}")
    out.append(f"  cache creation   {_fmt(t['cache_creation_input_tokens']):>14}")
    out.append(f"  cache read       {_fmt(t['cache_read_input_tokens']):>14}")
    out.append(f"  grand total      {_fmt(model['grand_total_tokens']):>14}")
    out.append("")
    out.append("PER-TOOL (result_bytes = UTF-8 bytes of result payload, a context-cost proxy, NOT tokens)")
    out.append(f"  {'tool':<40}{'calls':>7}{'result_bytes':>15}{'~tokens':>10}")
    for r in model["per_tool"]:
        out.append(f"  {r['tool'][:40]:<40}{r['calls']:>7}{_fmt(r['result_bytes']):>15}{_fmt(r['est_tokens']):>10}")
    out.append(f"  (~tokens = result_bytes // 4, a rough estimate — there is no tokenizer here)")
    out.append("")
    out.append("LARGEST INDIVIDUAL RESULTS")
    for r in model["largest_results"][:10]:
        flag = " [error]" if r["is_error"] else ""
        out.append(f"  {_fmt(r['bytes']):>12} B  {r['tool']}{flag}")
        out.append(f"                {r['preview'][:100]}")
    out.append("")
    out.append("SUMMARY / HONESTY")
    out.append(f"  {_fmt(s['lines_total'])} lines parsed, {s['bad_json_lines']} unparseable (skipped)")
    out.append(f"  {s['assistant_lines_without_usage']} assistant lines had no usage data (contributed 0 to token totals)")
    out.append(f"  {_fmt(s['tool_calls_total'])} tool calls, {_fmt(s['tool_results_total'])} tool results")
    if s["orphan_result_bytes"]:
        out.append(f"  {_fmt(s['orphan_result_bytes'])} bytes of results could not be matched to a tool call (shown as <unmatched>)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTML report — self-contained, no CDN, everything escaped.
# Visual family of agent-eval-harness-skill/scripts/report.py.
# ---------------------------------------------------------------------------

PAGE = """<!doctype html><meta charset="utf-8">
<title>Cost trace report</title>
<style>
 body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;
   margin:40px auto;padding:0 20px;color:#1a1a1a}}
 h1{{font-size:22px;margin-bottom:4px}}
 h2{{font-size:15px;margin:28px 0 10px;color:#333}}
 .head{{padding:16px 20px;border-radius:12px;margin:16px 0 8px;background:#fff4e8}}
 .head b{{font-size:28px}}
 .path{{color:#888;font-size:12px;word-break:break-all;margin-bottom:20px}}
 .kinds{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}}
 .kind{{flex:1 1 140px;border:1px solid #eee;border-radius:10px;padding:10px 14px;background:#fafafa}}
 .kind .n{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
 .kind .l{{color:#888;font-size:12px}}
 table{{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}}
 th{{text-align:left;color:#888;font-weight:600;padding:6px 8px;border-bottom:1px solid #eee}}
 td{{padding:6px 8px;border-top:1px solid #f0f0f0;vertical-align:top;font-variant-numeric:tabular-nums}}
 td.tool{{font-family:ui-monospace,monospace;font-variant-numeric:normal}}
 .bar{{height:8px;border-radius:4px;background:#f0a35e}}
 .case{{border:1px solid #e5e5e5;border-radius:10px;margin:8px 0;overflow:hidden}}
 summary{{padding:10px 14px;cursor:pointer;display:flex;align-items:center;gap:10px}}
 summary::-webkit-details-marker{{display:none}}
 summary .sz{{font-weight:700;font-variant-numeric:tabular-nums}}
 summary .nm{{font-family:ui-monospace,monospace;font-size:13px}}
 summary .rt{{margin-left:auto;color:#666}}
 .err{{color:#d1342f}}
 .body{{padding:8px 14px 14px;border-top:1px solid #f0f0f0}}
 pre.io{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;
   white-space:pre-wrap;word-break:break-word;font-size:12.5px;margin:0}}
 .note{{color:#888;font-size:12px;margin-top:6px}}
 .foot{{margin-top:28px;padding-top:14px;border-top:1px solid #eee;color:#888;font-size:12px}}
</style>
<h1>Session cost trace</h1>
<div class="path">{path}</div>
<div class="head"><b>{grand}</b> total tokens accounted &nbsp;·&nbsp; {calls} tool calls &nbsp;·&nbsp; {results} results</div>

<div class="kinds">
 <div class="kind"><div class="n">{t_input}</div><div class="l">input</div></div>
 <div class="kind"><div class="n">{t_output}</div><div class="l">output</div></div>
 <div class="kind"><div class="n">{t_cc}</div><div class="l">cache creation</div></div>
 <div class="kind"><div class="n">{t_cr}</div><div class="l">cache read</div></div>
</div>

<h2>Where the context cost went — per tool</h2>
<table>
 <tr><th>tool</th><th>calls</th><th>result bytes</th><th>~tokens</th><th></th></tr>
 {tool_rows}
</table>
<div class="note">result bytes = UTF-8 size of each result payload read back into context (a proxy, not token-perfect). ~tokens = bytes ÷ 4, a rough estimate — there is no tokenizer here.</div>

<h2>Largest individual results</h2>
{largest}

<h2>Timeline — cumulative token growth by turn</h2>
{timeline}

<h2>Summary &amp; honesty</h2>
<pre class="io">{summary}</pre>

<div class="foot">Generated by cost-tracer. Runs entirely locally — no transcript content leaves this machine.</div>
"""

TOOL_ROW = ('<tr><td class="tool">{tool}</td><td>{calls}</td><td>{bytes}</td>'
            '<td>{est}</td><td style="width:120px"><div class="bar" style="width:{pct}%"></div></td></tr>')

LARGEST_ROW = """<details class="case">
  <summary><span class="sz {ecls}">{sz} B</span> <span class="nm">{tool}</span><span class="rt">{est} ~tok</span></summary>
  <div class="body"><pre class="io">{preview}</pre></div>
</details>"""


def render_html(model: dict) -> str:
    t = model["totals"]
    s = model["stats"]

    max_bytes = max((r["result_bytes"] for r in model["per_tool"]), default=0) or 1
    tool_rows = "".join(
        TOOL_ROW.format(
            tool=html.escape(r["tool"]),
            calls=_fmt(r["calls"]),
            bytes=_fmt(r["result_bytes"]),
            est=_fmt(r["est_tokens"]),
            pct=round(100 * r["result_bytes"] / max_bytes),
        )
        for r in model["per_tool"]
    ) or '<tr><td colspan="5" class="note">no tool calls in this transcript</td></tr>'

    largest = "".join(
        LARGEST_ROW.format(
            ecls="err" if r["is_error"] else "",
            sz=_fmt(r["bytes"]),
            tool=html.escape(r["tool"]) + (" [error]" if r["is_error"] else ""),
            est=_fmt(r["bytes"] // 4),
            preview=html.escape(r["preview"]),
        )
        for r in model["largest_results"][:15]
    ) or '<div class="note">no tool results in this transcript</div>'

    if model["timeline"]:
        rows = ['<tr><th>turn</th><th>turn tokens</th><th>cumulative</th><th></th></tr>']
        peak = max(r["cumulative_tokens"] for r in model["timeline"]) or 1
        for r in model["timeline"]:
            rows.append(
                f'<tr><td>{r["turn"]}</td><td>{_fmt(r["turn_tokens"])}</td>'
                f'<td>{_fmt(r["cumulative_tokens"])}</td>'
                f'<td style="width:200px"><div class="bar" style="width:{round(100*r["cumulative_tokens"]/peak)}%"></div></td></tr>'
            )
        timeline = "<table>" + "".join(rows) + "</table>"
    else:
        timeline = '<div class="note">no usage-bearing turns in this transcript</div>'

    return PAGE.format(
        path=html.escape(model["transcript"]),
        grand=_fmt(model["grand_total_tokens"]),
        calls=_fmt(s["tool_calls_total"]),
        results=_fmt(s["tool_results_total"]),
        t_input=_fmt(t["input_tokens"]),
        t_output=_fmt(t["output_tokens"]),
        t_cc=_fmt(t["cache_creation_input_tokens"]),
        t_cr=_fmt(t["cache_read_input_tokens"]),
        tool_rows=tool_rows,
        largest=largest,
        timeline=timeline,
        summary=html.escape(render_text(model).split("SUMMARY / HONESTY\n", 1)[-1]),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Trace where a Claude Code session spent tokens.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--transcript", help="Path to a .jsonl transcript file")
    g.add_argument("--session-id", help="Session UUID; located under ~/.claude/projects/*/")
    p.add_argument("--json", action="store_true", help="Emit the full model as JSON to stdout")
    p.add_argument("--out", help="Write a self-contained HTML report to this path")
    args = p.parse_args()

    path = args.transcript or find_by_session_id(args.session_id)
    if not os.path.isfile(path):
        raise SystemExit(f"Not a file: {path}")

    model = parse_transcript(path)

    if args.out:
        Path(args.out).write_text(render_html(model), encoding="utf-8")
        print(f"Wrote HTML report: {os.path.abspath(args.out)}")

    if args.json:
        print(json.dumps(model, indent=2, ensure_ascii=False))
    else:
        print(render_text(model))


if __name__ == "__main__":
    main()
