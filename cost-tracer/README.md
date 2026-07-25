# cost-tracer

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skill** that
attributes where a session's token spend actually went, using the session's
own local JSONL transcript — token totals, a per-tool cost breakdown, the
largest individual results read back into context, and a timeline of
cumulative growth.

A session "feels expensive" but Claude Code doesn't say why. This parses the
transcript Claude Code already writes and turns it into a concrete answer:
which tool call, which file read, which result payload actually drove the
spend.

Runs entirely locally, against a file already on disk. No transcript content
ever leaves the machine and nothing is uploaded anywhere.

## What's a skill?

A skill is a folder with a `SKILL.md` that teaches Claude Code a repeatable
workflow. Drop this folder into your Claude Code skills directory, and asking
"why did this session cost so much" or "what's eating my context" will invoke
this workflow. `scripts/trace.py` is real, runnable code — not pseudocode.

## Use it standalone (no Claude Code required)

Zero third-party dependencies (Python 3.11+):

```bash
python -m scripts.trace --transcript path/to/session.jsonl
# or locate by session id automatically:
python -m scripts.trace --session-id <uuid>
```

```
TOKEN TOTALS (from usage blocks — authoritative)
  input                       350
  output                      360
  cache creation              500
  cache read                2,000
  grand total               3,210

PER-TOOL (result_bytes = UTF-8 bytes of result payload, a context-cost proxy, NOT tokens)
  tool                                      calls   result_bytes   ~tokens
  Bash                                          2         12,481     3,120
  Read                                          1             27         6

SUMMARY / HONESTY
  10 lines parsed, 0 unparseable (skipped)
  1 assistant lines had no usage data (contributed 0 to token totals)
  3 tool calls, 3 tool results
```

(That's the output of the shipped fixture — `python -m examples.make_fixture_transcript`
then the command above. Its docstring hand-computes the same totals, so the
tool's correctness is checkable without trusting it blindly.)

`--out report.html` writes a self-contained HTML report (no build step, no
CDN). `--json` for machine-readable output.

## The honesty boundary

Token *totals* come straight from each turn's `usage` block — real numbers
the API reported, not estimated. Per-tool and per-result attribution is
different: there's no tokenizer here, so a tool's cost is measured as the
UTF-8 byte size of its result — the thing that actually gets read back into
the next turn — with a `bytes // 4` estimate shown and labelled as an
estimate everywhere. A transcript line with no `usage` block contributes `0`
and is reported as missing, never silently folded into an average.

## Layout

```
cost-tracer/
├── SKILL.md                          # the skill: workflow Claude Code follows
├── scripts/
│   └── trace.py                      # parser + text report + HTML renderer
└── examples/
    └── make_fixture_transcript.py    # synthetic transcript, hand-computable totals
```

## Design principles

- **Local only** — reads a file already on disk; nothing is ever transmitted.
- **Totals are authoritative; attribution is an honest proxy** — the two are
  never conflated, and every estimated figure is labelled as an estimate.
- **Missing data is reported, not hidden** — a line with no `usage` block
  counts as "no usage data," never a silent zero.
- **Never crash on one bad line** — real transcripts are messy; one bad line
  is skipped and counted, never fatal.
- **Stdlib only** — no tokenizer dependency, no upload client, no vendor SDK.

## License

MIT — see [LICENSE](LICENSE).
