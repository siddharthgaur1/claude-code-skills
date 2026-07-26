"""Run an evaluation set against an agent and produce a results file.

The harness is agent-agnostic. You point it at a Python callable — your
"agent entrypoint" — via `module:function`. The callable receives the eval
input and must return a result dict (see graders.py for the shape):

    def run(input: dict) -> dict:
        ...
        return {"output": ..., "trajectory": [...], "tokens": ...}

Usage:
    python -m scripts.run_evals \
        --eval-set examples/example_eval_set.json \
        --agent examples.echo_agent:run \
        --out results.json \
        --repeats 1

Design choices (matter for trustworthy evals):
- Every case is isolated: an exception in the agent is caught, recorded as
  `error`, and graded as a failure. One broken case never aborts the run.
- Latency is measured by the harness, not self-reported by the agent.
- `--repeats N` runs each case N times so you can see flakiness. The case is
  marked pass only if it passes on every repeat (report shows the rate).
- Nothing is ever invented. If the agent returns no trajectory, trajectory
  assertions simply fail — they are not skipped or assumed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import traceback
from pathlib import Path

from scripts.graders import grade, GradeReport


def load_callable(spec: str):
    """Resolve 'package.module:function' to the callable object."""
    if ":" not in spec:
        raise ValueError(f"agent spec must be 'module:function', got {spec!r}")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def run_one(agent_fn, case: dict) -> dict:
    """Invoke the agent for a single case, timing it and trapping errors."""
    started = time.perf_counter()
    try:
        result = agent_fn(case["input"])
        if not isinstance(result, dict) or "output" not in result:
            raise TypeError("agent must return a dict containing an 'output' key")
        result.setdefault("trajectory", [])
        result["error"] = None
    except Exception:
        result = {"output": None, "trajectory": [], "error": traceback.format_exc(limit=3)}
    # Harness owns the timing so it can't be gamed by the agent.
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def evaluate(eval_set: dict, agent_fn, judge_fn=None, repeats: int = 1) -> dict:
    cases = eval_set["evals"]
    out = {"skill_name": eval_set.get("skill_name"), "repeats": repeats, "cases": []}

    for case in cases:
        cid = str(case["id"])
        per_repeat: list[GradeReport] = []
        last_result = None
        for _ in range(repeats):
            result = run_one(agent_fn, case)
            last_result = result
            report = grade(cid, case["input"].get("prompt", ""), result,
                           case.get("assertions", []), judge_fn=judge_fn)
            per_repeat.append(report)

        passes = sum(1 for r in per_repeat if r.passed)
        out["cases"].append({
            "id": cid,
            "name": case.get("name", cid),
            "input": case["input"],
            "pass_rate": round(passes / repeats, 3),
            "passed": passes == repeats,
            "sample_result": last_result,
            "assertions": [
                {"name": a.name, "kind": a.kind, "passed": a.passed,
                 "detail": a.detail, "score": a.score}
                for a in per_repeat[-1].results
            ],
        })

    total = len(out["cases"])
    passed = sum(1 for c in out["cases"] if c["passed"])
    out["summary"] = {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
    }
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Run an agent evaluation set.")
    p.add_argument("--eval-set", required=True, help="Path to eval set JSON")
    p.add_argument("--agent", required=True, help="Agent entrypoint 'module:function'")
    p.add_argument("--out", default="results.json", help="Where to write results JSON")
    p.add_argument("--repeats", type=int, default=1, help="Runs per case (flakiness check)")
    p.add_argument("--judge", default=None,
                   help="Optional LLM-judge callable 'module:function' for llm_judge assertions")
    args = p.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    agent_fn = load_callable(args.agent)
    judge_fn = load_callable(args.judge) if args.judge else None

    results = evaluate(eval_set, agent_fn, judge_fn=judge_fn, repeats=args.repeats)
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Windows consoles default to a non-UTF-8 codec that can't print the marks below.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")

    s = results["summary"]
    print(f"\n{'PASS' if s['passed'] == s['total'] else 'FAIL'}  "
          f"{s['passed']}/{s['total']} cases  ({s['pass_rate']:.0%})")
    for c in results["cases"]:
        mark = "\u2713" if c["passed"] else "\u2717"
        print(f"  {mark} {c['name']:<28} {c['pass_rate']:.0%}")
        for a in c["assertions"]:
            if not a["passed"]:
                print(f"      \u2717 {a['name']}: {a['detail']}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
