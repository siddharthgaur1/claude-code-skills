# Grader design

Read this when choosing assertions for an eval set or designing an LLM judge.

## Contents
- Deterministic assertion catalogue
- Choosing between deterministic and judge
- Designing a trustworthy LLM-as-judge
- Anti-patterns

## Deterministic assertion catalogue

Every assertion is a JSON object with a `kind`, a `name` (shown in the report),
and kind-specific fields. All are checked in pure Python — reproducible and
free.

| kind | fields | passes when |
|------|--------|-------------|
| `contains` | `value`, `ignore_case?` | output contains the substring |
| `not_contains` | `value`, `ignore_case?` | output does **not** contain it |
| `equals` | `value`, `path?` | value at `path` (default `output`) equals `value` |
| `regex` | `value`, `path?`, `ignore_case?` | pattern matches |
| `tool_called` | `value` | that tool appears in the trajectory |
| `no_tool` | `value` | that tool does **not** appear |
| `tool_order` | `value` (list) | tools appear as an ordered subsequence |
| `tool_args` | `tool`, `args` (dict) | some call to `tool` matches every arg |
| `max_tool_calls` | `value` (int) | trajectory length ≤ value |
| `latency_under_ms` | `value` (int) | harness-measured latency ≤ value |
| `no_error` | — | the agent call did not raise |
| `llm_judge` | `criteria` | the configured judge returns pass |

`path` uses dotted access into the result, e.g. `output.answer` or
`output.items.0.id`, so structured outputs can be asserted precisely.

### Examples

```json
{ "name": "cites a source", "kind": "regex", "value": "https?://" }
```
```json
{ "name": "never deletes", "kind": "no_tool", "value": "delete_record" }
```
```json
{ "name": "retrieves before answering", "kind": "tool_order",
  "value": ["retrieve", "generate"] }
```
```json
{ "name": "queries the right table", "kind": "tool_args",
  "tool": "run_sql", "args": { "table": "invoices" } }
```

## Deterministic vs judge

Default to deterministic. Reach for a judge only when correctness genuinely
depends on meaning that no pattern captures — faithfulness to a source,
tone, whether an explanation is actually correct.

Often you can decompose a "quality" requirement into deterministic parts and
avoid the judge entirely:

- "answer is well-sourced" → `regex` for a URL **and** `tool_called: retrieve`.
- "refuses unsafe requests" → `contains` on a refusal phrase, or `no_tool` on
  the dangerous tool.
- "returns valid JSON" → `regex` / a parse assertion, not a judge.

A deterministic check that approximates the requirement is usually better than
a judge that captures it perfectly but flakily.

## Designing a trustworthy LLM-as-judge

A judge is itself a model call and can be wrong or inconsistent. Treat it with
the same rigour you'd treat the agent.

A judge callable has this signature (see `scripts/graders.py`):

```python
from scripts.graders import JudgeVerdict

def judge(prompt: str, output, criteria: str) -> JudgeVerdict:
    ...
    return JudgeVerdict(passed=True, score=0.9, reason="...")
```

A GPT-4o judge template:

```python
import json
from openai import OpenAI
from scripts.graders import JudgeVerdict

_client = OpenAI()

RUBRIC = """You are a strict evaluator. Given a user prompt, an agent's
response, and a criterion, decide whether the response satisfies the criterion.
Return ONLY JSON: {"passed": bool, "score": 0.0-1.0, "reason": "<one sentence>"}.
Judge only the stated criterion. Do not reward verbosity or penalise brevity."""

def judge(prompt, output, criteria) -> JudgeVerdict:
    msg = (f"PROMPT:\n{prompt}\n\nRESPONSE:\n{output}\n\n"
           f"CRITERION:\n{criteria}")
    resp = _client.chat.completions.create(
        model="gpt-4o", temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": RUBRIC},
                  {"role": "user", "content": msg}],
    )
    v = json.loads(resp.choices[0].message.content)
    return JudgeVerdict(bool(v["passed"]), float(v["score"]), v["reason"])
```

Rules for a judge you can trust:

- **Temperature 0** and a **structured (JSON) response** for reproducibility.
- **One criterion per assertion.** A judge asked to weigh five things at once
  is unreliable; split them into five `llm_judge` assertions.
- **Force a reason.** The written justification is your audit trail and shows
  up in the report; a judge that can't justify its verdict is a red flag.
- **Judge the judge.** Before trusting it, hand-label ~20 outputs pass/fail and
  check the judge agrees. If it doesn't, tighten the rubric. An uncalibrated
  judge is just a confident random number.
- **Pin the model.** A judge is part of your test suite; changing its model
  silently changes your pass/fail line.

## Anti-patterns

- **Judge as a crutch.** Reaching for `llm_judge` because writing a
  deterministic assertion takes thought. The deterministic one is more valuable.
- **Asserting the output verbatim.** `equals` on a long free-text answer is
  brittle; assert the invariant (contains the key fact) instead.
- **Grading only the final answer for an agent.** If you never check the
  trajectory, an agent that reaches the right answer via a forbidden or wasteful
  path passes. Grade the steps.
- **A golden set of toys.** Inputs like "say hello" test nothing. Seed the set
  from real, hard, previously-failing requests.
