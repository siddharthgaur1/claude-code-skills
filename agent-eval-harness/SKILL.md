---
name: agent-eval-harness
description: >-
  Scaffold and run evaluation suites for LLM agents — trajectory / tool-call
  correctness, final-output assertions, LLM-as-judge grading, regression
  testing against a golden set, and an HTML report. Use this skill whenever
  the user wants to test, evaluate, benchmark, or measure the reliability of an
  agent, chatbot, RAG pipeline, or any LLM-powered workflow — including when
  they say things like "how do I know my agent works", "add tests for this
  agent", "catch regressions", "grade these outputs", "is my agent flaky", or
  "set up evals" — even if they don't use the word "eval".
---

# Agent evaluation harness

Building an agent is easy; knowing whether it *works* is the hard part. This
skill sets up a small, dependency-light harness that turns "it seemed fine when
I tried it" into a repeatable, quantitative signal you can run on every change.

The guiding principle throughout: **never fabricate a result.** A check either
verifiably passes or it fails. When quality can only be judged subjectively, use
an explicit LLM-as-judge — never a silent guess.

## When to reach for this

Use it when the user wants to test, benchmark, or harden any LLM-powered thing:
an agent, a tool-calling loop, a RAG pipeline, a chatbot. Signals include
"catch regressions", "is it reliable", "add tests for my agent", "grade these
answers", "is it flaky", "set up evals". The word "eval" need not appear.

## The mental model

An evaluation is three pieces:

1. **A dataset** — a set of inputs plus what should be true of the output. This
   is your *golden set*. Growing it is the single highest-leverage eval activity.
2. **A runner** — invokes the agent on each input and records what happened:
   the final output *and* the trajectory (which tools it called, in what order,
   with what arguments).
3. **Graders** — assertions checked against each recorded result.

The harness in `scripts/` implements 2 and 3. Your job with the user is mostly
1: capturing what "correct" means for their agent as concrete assertions.

## Workflow

### Step 1 — Define the agent contract

The harness is agent-agnostic. It calls a Python entrypoint, `module:function`,
that takes the eval input and returns a result dict:

```python
def run(input: dict) -> dict:
    return {
        "output": "...",                       # required: the final answer
        "trajectory": [                        # optional: for agentic evals
            {"tool": "search_web", "args": {"q": "..."}},
        ],
        "tokens": 512,                         # optional: cost signal
    }
```

If the user's agent doesn't return this shape, write a thin adapter that calls
their agent and maps its output into this dict. For LangGraph, run the graph
and read the tool calls off the message history / checkpointer state. Do **not**
have the adapter invent a trajectory it can't observe — an unobservable
trajectory is an empty list, and trajectory assertions should then fail
honestly. See `references/trajectory-eval.md` for how to extract trajectories
from common frameworks.

`examples/echo_agent.py` is a working reference entrypoint.

### Step 2 — Write the eval set

Create a JSON file matching `assets/eval_set.schema.json`. Each case has an
`id`, a human-readable `name`, an `input`, and a list of `assertions`.

Start the golden set from real failures and real requirements, not toy inputs.
Ask the user: what has this agent gotten wrong before? What must it never do
(e.g. call a destructive tool, leak a secret, hallucinate a citation)? Each
answer becomes a case. `examples/example_eval_set.json` is a complete example.

Aim for coverage across three axes:
- **Happy path** — typical requests that must succeed.
- **Trajectory** — the agent takes the right *steps*, not just lands the right
  answer (searched before answering; did *not* call the delete tool; stayed
  under a tool-call budget).
- **Guardrails / regressions** — one case per past bug, so it can never
  silently return.

### Step 3 — Choose assertions

Prefer deterministic assertions; they are free, reproducible, and unambiguous.
Reach for `llm_judge` only when no deterministic check captures what matters.
See `references/grader-design.md` for the full catalogue and guidance. Quick map:

| Want to check that…                    | Assertion kind        |
|----------------------------------------|-----------------------|
| answer mentions / omits something      | `contains`, `not_contains` |
| answer exactly equals a value          | `equals` (+ `path`)   |
| answer matches a pattern               | `regex`               |
| a tool was / wasn't used               | `tool_called`, `no_tool` |
| tools ran in a required order          | `tool_order`          |
| a tool got specific arguments          | `tool_args`           |
| the agent didn't over-call tools       | `max_tool_calls`      |
| it responded fast enough               | `latency_under_ms`    |
| it didn't crash                        | `no_error`            |
| quality is subjective (tone, faithfulness) | `llm_judge`       |

### Step 4 — Run

```bash
python -m scripts.run_evals \
  --eval-set path/to/eval_set.json \
  --agent your_module:run \
  --out results.json \
  --repeats 3        # run each case 3× to expose flakiness
```

`--repeats` matters for agents: a case that passes 2 of 3 times is flaky, and
flakiness is a bug. The harness measures latency itself and traps any exception
as a failed case, so one broken agent call never aborts the run.

For `llm_judge` assertions, pass a judge callable with
`--judge your_module:judge`. Without one, judge assertions raise rather than
pass silently. `references/grader-design.md` shows a GPT-4o judge template.

### Step 5 — Read the report and iterate

```bash
python -m scripts.report --results results.json --out report.html
```

This writes a self-contained HTML report (no build step, openable from disk or
committed as a CI artifact). Walk the failures with the user. Each failure is
either a real agent bug (fix the agent) or a bad expectation (fix the
assertion). Re-run until green, then **grow the golden set** — the eval is only
as good as its coverage.

### Step 6 — Wire into CI (optional)

`run_evals` exits 0 regardless so you can inspect output; for CI, gate on the
`summary.pass_rate` in `results.json`, e.g.:

```bash
python -m scripts.run_evals --eval-set evals.json --agent app:run --out r.json
python -c "import json,sys; s=json.load(open('r.json'))['summary']; \
  sys.exit(0 if s['pass_rate']==1.0 else 1)"
```

## Reference files

- `references/grader-design.md` — every assertion kind with examples, plus how
  to design a trustworthy LLM-as-judge (rubric, calibration, judge-the-judge).
- `references/trajectory-eval.md` — extracting trajectories from LangGraph,
  OpenAI function-calling, and other frameworks; what makes a good trajectory
  assertion.
- `assets/eval_set.schema.json` — the eval-set JSON schema.

## Principles (why this is built the way it is)

- **Deterministic first.** Assertions you can check in code are cheaper and more
  trustworthy than a model's opinion. Use the judge only where you must.
- **No fabricated results.** Missing data fails honestly; it is never assumed.
  A judge-less `llm_judge` assertion raises rather than inventing a pass.
- **Trajectory ≠ output.** For agents, *how* it got the answer matters as much
  as the answer. Grade the steps.
- **Regressions are cases.** Every bug found becomes a permanent eval case.
- **The golden set is the product.** Runner and graders are fixed; the leverage
  is in the breadth and realism of the dataset.
