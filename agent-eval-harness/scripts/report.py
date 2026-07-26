"""Render results.json into a single self-contained HTML report.

    python -m scripts.report --results results.json --out report.html

The report is deliberately dependency-free (no build step, no CDN) so it can
be opened straight from disk or committed as a CI artifact.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROW = """
<details class="case {cls}">
  <summary><span class="mark">{mark}</span> {name}
    <span class="rate">{rate:.0%}</span></summary>
  <div class="body">
    <pre class="io"><b>input</b>  {inp}
<b>output</b> {out}</pre>
    <table>{rows}</table>
  </div>
</details>"""

ASSERT_ROW = ('<tr class="{cls}"><td>{mark}</td><td>{name}</td>'
              '<td class="kind">{kind}</td><td>{detail}{score}</td></tr>')

PAGE = """<!doctype html><meta charset="utf-8">
<title>Agent eval report</title>
<style>
 body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;
   margin:40px auto;padding:0 20px;color:#1a1a1a}}
 h1{{font-size:22px;margin-bottom:4px}}
 .head{{padding:16px 20px;border-radius:12px;margin:16px 0 28px;
   background:{banner}}}
 .head b{{font-size:28px}}
 .case{{border:1px solid #e5e5e5;border-radius:10px;margin:8px 0;overflow:hidden}}
 summary{{padding:12px 16px;cursor:pointer;display:flex;align-items:center;gap:10px}}
 summary::-webkit-details-marker{{display:none}}
 .rate{{margin-left:auto;color:#666;font-variant-numeric:tabular-nums}}
 .mark{{font-weight:700}}
 .pass .mark{{color:#159a4f}} .fail .mark{{color:#d1342f}}
 .body{{padding:4px 16px 16px;border-top:1px solid #f0f0f0}}
 pre.io{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;
   white-space:pre-wrap;word-break:break-word;font-size:12.5px}}
 table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}}
 td{{padding:6px 8px;border-top:1px solid #f0f0f0;vertical-align:top}}
 tr.fail td{{background:#fff6f6}} .kind{{color:#888;font-family:ui-monospace,monospace}}
</style>
<h1>Agent evaluation report</h1>
<div class="head" >
  <b>{passed}/{total}</b> cases passed &nbsp;·&nbsp; {rate:.0%} pass rate
  &nbsp;·&nbsp; {repeats}× per case
</div>
{cases}
"""


def render(results: dict) -> str:
    s = results["summary"]
    banner = "#e8f7ee" if s["passed"] == s["total"] else "#fdeeee"
    cases_html = []
    for c in results["cases"]:
        rows = []
        for a in c["assertions"]:
            passed = a["passed"]
            score = f' <i>(score {a["score"]:.2f})</i>' if a.get("score") is not None else ""
            rows.append(ASSERT_ROW.format(
                cls="pass" if passed else "fail",
                mark="\u2713" if passed else "\u2717",
                name=html.escape(a["name"]),
                kind=html.escape(a["kind"]),
                detail=html.escape(str(a["detail"])),
                score=score,
            ))
        res = c.get("sample_result") or {}
        cases_html.append(ROW.format(
            cls="pass" if c["passed"] else "fail",
            mark="\u2713" if c["passed"] else "\u2717",
            name=html.escape(c["name"]),
            rate=c["pass_rate"],
            inp=html.escape(json.dumps(c["input"], ensure_ascii=False))[:600],
            out=html.escape(str(res.get("output")))[:800],
            rows="".join(rows),
        ))
    return PAGE.format(
        passed=s["passed"], total=s["total"], rate=s["pass_rate"],
        repeats=results.get("repeats", 1), banner=banner,
        cases="".join(cases_html),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--results", default="results.json")
    p.add_argument("--out", default="report.html")
    args = p.parse_args()
    results = json.loads(Path(args.results).read_text())
    Path(args.out).write_text(render(results), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
