"""Grading engine for agent evaluation.

Two families of graders:

1. Deterministic assertions — checked in pure Python against the agent's
   result. These are cheap, reproducible, and should be your first choice.
   An assertion never guesses: it either verifiably passes or it fails.

2. LLM-as-judge — for open-ended outputs where no deterministic check
   captures quality (tone, faithfulness, helpfulness). This is pluggable:
   you pass a `judge_fn`. If an eval declares an `llm_judge` assertion but
   no judge is configured, the run raises instead of inventing a score.
   No fabricated results, ever.

A "result" is the dict your agent returns for one eval case:

    {
        "output": "final answer string (or any JSON-serialisable value)",
        "trajectory": [                     # optional, for agentic evals
            {"tool": "search_web", "args": {"q": "..."}},
            {"tool": "read_page",  "args": {"url": "..."}},
        ],
        "latency_ms": 1234,                 # optional
        "tokens": 5678,                     # optional
        "error": null,                      # set by the runner on failure
    }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# A judge takes (prompt, output, criteria) and returns (passed, score, reason).
JudgeFn = Callable[[str, Any, str], "JudgeVerdict"]


@dataclass
class JudgeVerdict:
    passed: bool
    score: float  # 0.0–1.0
    reason: str


@dataclass
class AssertionResult:
    name: str
    kind: str
    passed: bool
    detail: str
    score: Optional[float] = None  # populated for llm_judge assertions


@dataclass
class GradeReport:
    eval_id: str
    results: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results) and bool(self.results)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)


def _get_path(obj: Any, path: str) -> Any:
    """Dotted-path lookup into nested dict/list, e.g. 'meta.items.0.id'."""
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _tools(result: dict) -> list[str]:
    return [step.get("tool") for step in result.get("trajectory", []) or []]


# --- individual assertion checkers -------------------------------------------
# Each returns (passed: bool, detail: str). Deterministic and side-effect free.

def _check_contains(result, spec):
    text = str(result.get("output", ""))
    needle = spec["value"]
    ci = spec.get("ignore_case", False)
    hay = text.lower() if ci else text
    ned = needle.lower() if ci else needle
    ok = ned in hay
    return ok, f"{'found' if ok else 'missing'} substring {needle!r}"


def _check_not_contains(result, spec):
    ok, detail = _check_contains(result, spec)
    return (not ok), detail.replace("found", "unexpectedly found").replace("missing", "correctly absent")


def _check_equals(result, spec):
    actual = _get_path(result, spec.get("path", "output"))
    ok = actual == spec["value"]
    return ok, f"expected {spec['value']!r}, got {actual!r}"


def _check_regex(result, spec):
    text = str(_get_path(result, spec.get("path", "output")))
    flags = re.IGNORECASE if spec.get("ignore_case") else 0
    ok = re.search(spec["value"], text, flags) is not None
    return ok, f"pattern {spec['value']!r} {'matched' if ok else 'did not match'}"


def _check_tool_called(result, spec):
    ok = spec["value"] in _tools(result)
    return ok, f"tool {spec['value']!r} {'was' if ok else 'was NOT'} called; trajectory={_tools(result)}"


def _check_no_tool(result, spec):
    called = _tools(result)
    ok = spec["value"] not in called
    return ok, f"tool {spec['value']!r} {'correctly avoided' if ok else 'was called (should not be)'}"


def _check_tool_order(result, spec):
    """spec['value'] is an ordered subsequence of tools that must appear in order."""
    want = list(spec["value"])
    got = _tools(result)
    it = iter(got)
    ok = all(tool in it for tool in want)
    return ok, f"expected ordered subsequence {want}; got {got}"


def _check_tool_args(result, spec):
    """Assert a specific tool was called with args matching a subset spec."""
    tool = spec["tool"]
    want = spec["args"]
    for step in result.get("trajectory", []) or []:
        if step.get("tool") == tool:
            args = step.get("args", {}) or {}
            if all(args.get(k) == v for k, v in want.items()):
                return True, f"{tool} called with matching args {want}"
    return False, f"no call to {tool} matched args {want}"


def _check_max_tool_calls(result, spec):
    n = len(_tools(result))
    ok = n <= spec["value"]
    return ok, f"{n} tool calls (limit {spec['value']})"


def _check_latency(result, spec):
    lat = result.get("latency_ms")
    if lat is None:
        return False, "no latency_ms recorded"
    ok = lat <= spec["value"]
    return ok, f"latency {lat}ms (limit {spec['value']}ms)"


def _check_no_error(result, spec):
    err = result.get("error")
    return (err is None), ("clean run" if err is None else f"agent errored: {err}")


DETERMINISTIC = {
    "contains": _check_contains,
    "not_contains": _check_not_contains,
    "equals": _check_equals,
    "regex": _check_regex,
    "tool_called": _check_tool_called,
    "no_tool": _check_no_tool,
    "tool_order": _check_tool_order,
    "tool_args": _check_tool_args,
    "max_tool_calls": _check_max_tool_calls,
    "latency_under_ms": _check_latency,
    "no_error": _check_no_error,
}


def grade(
    eval_id: str,
    prompt: str,
    result: dict,
    assertions: list[dict],
    judge_fn: Optional[JudgeFn] = None,
) -> GradeReport:
    """Run every assertion for one eval case and collect a GradeReport."""
    report = GradeReport(eval_id=eval_id)

    for a in assertions:
        kind = a["kind"]
        name = a.get("name", kind)

        if kind == "llm_judge":
            if judge_fn is None:
                # Do not fabricate a score. Fail loudly so the gap is visible.
                raise RuntimeError(
                    f"eval {eval_id!r} declares an 'llm_judge' assertion but no "
                    f"judge_fn was provided. Configure a judge or remove the assertion."
                )
            verdict = judge_fn(prompt, result.get("output"), a["criteria"])
            report.results.append(
                AssertionResult(name, kind, verdict.passed, verdict.reason, verdict.score)
            )
            continue

        checker = DETERMINISTIC.get(kind)
        if checker is None:
            raise ValueError(f"unknown assertion kind {kind!r} in eval {eval_id!r}")
        passed, detail = checker(result, a)
        report.results.append(AssertionResult(name, kind, passed, detail))

    return report
