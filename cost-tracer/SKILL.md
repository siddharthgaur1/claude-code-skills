---
name: cost-tracer
description: >-
  Attribute where a Claude Code session's token spend actually went — totals
  by token kind, a per-tool breakdown of which tool calls cost the most
  context, the largest individual results read back in, and a timeline of
  cumulative growth — from the session's own local JSONL transcript. Use this
  skill whenever the user wants to understand, audit, explain, or reduce why a
  session got expensive — including "why did this session cost so much",
  "what's eating my context", "which tool call was expensive", or "help me
  understand my token usage" — even if they don't say "tokens" or "cost"
  explicitly and just say a session felt slow or bloated.
---

# Cost tracer

A session "feels expensive" but Claude Code doesn't tell you *why* — which
tool call, which file read, which subagent dispatch actually drove the spend.
This skill parses the session's own transcript (Claude Code already writes
one, as JSONL, under `~/.claude/projects/<project>/<session-id>.jsonl`) and
attributes cost to the things that caused it.

Everything runs locally against a file already on disk. No transcript content
ever leaves the machine, and nothing is uploaded anywhere — this matters,
because a transcript can contain anything that happened in the session.

## When to reach for this

- Right after a session that felt slow, expensive, or context-heavy, to see
  what actually drove it.
- Before repeating a workflow, to find the tool call worth trimming or
  scoping down (a `Bash` command whose output is 10x everything else, a `Read`
  on a file bigger than it needed to be).
- When comparing two approaches to the same task, to see which one was
  cheaper in practice, not just in theory.

## The honesty boundary — read this first

Token *totals* (input/output/cache-read/cache-creation) come straight from
each turn's `usage` block and are authoritative — that's the real number the
API reported. Per-tool and per-result attribution is **not** token-perfect:
there is no tokenizer here, so a tool's cost is measured as the UTF-8 byte
size of its result payload — the thing that actually gets read back into the
next turn's input — with a rough `bytes // 4` estimate shown alongside and
labelled as an estimate everywhere it appears. A transcript line with no
`usage` block contributes `0` to the totals and is counted and reported as
missing — never silently interpolated or hidden.

## Workflow

### Step 1 — Point it at a transcript

```bash
python -m scripts.trace --transcript path/to/session.jsonl
# or, to find it by session id automatically:
python -m scripts.trace --session-id <uuid>
```

### Step 2 — Read the breakdown

- **Token totals** — the authoritative input/output/cache-read/cache-creation
  numbers for the whole session.
- **Per-tool** — which tool name accounts for the most result bytes read back
  into context, ranked, with call counts.
- **Largest individual results** — the specific tool calls whose output was
  biggest, with a preview, so you can see exactly what got read in.
- **Timeline** — cumulative token growth turn by turn, to see whether cost was
  spread evenly or concentrated in one stretch of the session.

### Step 3 — Act on it

The per-tool and largest-results views point at concrete next steps: scope a
`Grep`/`Read` more tightly, avoid re-reading a file that was already read
earlier in the session, or move a large intermediate result out of the main
conversation (e.g. into a fork/subagent, per this monorepo's other skills'
philosophy of keeping raw bytes out of the primary context).

### Step 4 — Share or archive

```bash
python -m scripts.trace --transcript session.jsonl --out report.html
```

Self-contained HTML, no build step, no CDN — open from disk or attach
anywhere. `--json` for machine-readable output if you want to script over it.

## Reference files

- `scripts/trace.py` — the whole tool: parser, text report, and HTML
  renderer. The module docstring states the honesty boundary in full.
- `examples/make_fixture_transcript.py` — generates a small, clearly-synthetic
  transcript matching the real schema, with hand-computable totals documented
  in its own docstring, so the tool has something safe and reproducible to
  run against without touching real session data.

## Principles (why this is built the way it is)

- **Local only.** Reads a file already on disk; nothing is ever transmitted.
- **Token totals are authoritative; attribution is an honest proxy.** The two
  are never conflated — every estimated figure is labelled as an estimate,
  everywhere it's shown.
- **Missing data is reported, not hidden.** A line with no `usage` block
  counts as "no usage data," never as a silent zero baked into an average.
- **Never crash on one bad line.** Real transcripts are messy — unknown
  message types, occasional malformed JSON. One bad line is skipped and
  counted; it never aborts the whole parse.
- **Stdlib only.** No tokenizer dependency, no upload client, no vendor SDK.
