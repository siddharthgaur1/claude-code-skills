# context-budget-auditor

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skill** that
scans a repository for the paths that would blow a coding agent's context
budget — huge generated files, lockfiles, vendored dependency trees, minified
bundles, binary blobs — ranks them by estimated token cost, and hands back
paste-ready exclude globs for a `.claudeignore`.

A coding agent reads files into a finite context window, and the fastest way to
waste it is to read the wrong file first: one accidental read of a multi-MB
`package-lock.json` and the budget is gone. This skill finds those paths
*before* you start, with one rule throughout: **never fabricate a number.**
Token counts are labelled estimates (`bytes / 4`); files that cannot be
meaningfully estimated — binaries — report their size and say so, rather than
dressing a byte count up as a token count.

## What's a skill?

A skill is a folder with a `SKILL.md` that teaches Claude Code a repeatable
workflow. Drop this folder into your Claude Code skills directory, and asking
Claude "why is my context filling up on this repo?" or "what should I exclude?"
will invoke this workflow. The `scripts/` here are real, runnable code the skill
drives — not pseudocode.

## Use it standalone (no Claude Code required)

The auditor runs on its own with zero third-party dependencies (Python 3.11+):

```bash
python -m scripts.audit --path .                 # ranked table
python -m scripts.audit --path . --suggest-ignore   # .claudeignore globs
python -m scripts.audit --path . --json          # machine-readable
```

Against the bundled fixture repo:

```bash
python -m scripts.audit --path examples/fixture_repo
```

```
Context-budget audit of .../examples/fixture_repo
  token estimate = bytes / 4 (rough heuristic, not a tokenizer; ~ = estimated)
  bloat threshold = 256 KB   |   3 flagged path(s)

  EST.TOKENS     SIZE  CATEGORY        PATH
  -----------------------------------------
     ~34,265  133.9KB  lockfile        package-lock.json
        ~642    2.5KB  dependency dir  node_modules/  (2 files, rolled up)
        ~467    1.8KB  minified asset  assets/bundle.min.js

  Estimated readable-text cost of flagged paths: ~35,374 tokens
  Run with --suggest-ignore for paste-ready exclude globs.
```

The ordinary source file in the fixture (`src/main.py`) is deliberately **not**
flagged — the tool ranks bloat without crying wolf on normal code.

`--suggest-ignore` turns that into an action:

```
# Suggested .claudeignore patterns (deduplicated, sorted)
*.min.js
node_modules/
package-lock.json
```

## Options

| Flag              | Default | Meaning                                                    |
|-------------------|---------|------------------------------------------------------------|
| `--path`          | `.`     | directory to scan (`.git` is always skipped)               |
| `--min-size-kb`   | `256`   | catch-all threshold: any file this big is flagged          |
| `--top`           | `20`    | rows to show in the table                                  |
| `--json`          | off     | emit machine-readable JSON (with the assumptions block)    |
| `--suggest-ignore`| off     | emit deduplicated gitignore-glob exclude patterns          |
| `--detail`        | off     | list every flagged file instead of rolling up dep dirs     |

## How the numbers work (and their limits)

- **Tokens are estimated as `bytes // 4`** — the standard rough approximation
  (~4 bytes per token for English text/code). It is a heuristic, not a
  tokenizer; prose runs leaner, dense code heavier. Every estimate is prefixed
  `~`, and the assumption is printed with every report.
- **Binary files are never token-estimated.** A null-byte sniff of the first
  8 KB detects them; they report size and are flagged "binary — not directly
  readable as context." `estimated_tokens` is `null` in JSON, by design.
- **Dependency directories are rolled up** `du`-style into one line (file count
  + total size) so the report is readable. `--detail` lists everything.

## Layout

```
context-budget-auditor/
├── SKILL.md                    # the skill: workflow Claude Code follows
├── scripts/
│   ├── audit.py                # the scanner: walk, flag, rank, suggest globs
│   └── test_audit.py           # framework-free self-check of the core logic
├── examples/
│   ├── make_fixtures.py        # regenerates the synthetic bloat fixtures
│   └── fixture_repo/           # tiny demo repo (normal file + planted bloat)
│       ├── src/main.py         # ordinary source — must stay UN-flagged
│       ├── package-lock.json   # synthetic lockfile stand-in
│       ├── assets/bundle.min.js
│       └── node_modules/left-pad/…
├── README.md
└── LICENSE
```

Regenerate the fixtures (they are checked in, but reproducible) with
`python examples/make_fixtures.py`, and run the self-check with
`python -m scripts.test_audit`.

## Design principles

- **Never fabricate a number** — token counts are labelled estimates; binaries
  report size and refuse a token count rather than inventing one.
- **Rank, don't itemize** — a dependency tree is one rolled-up line, not 50,000;
  the report is for a human deciding what to exclude.
- **Recognize bloat by shape** — lockfiles, dep dirs, and minified assets are
  flagged by name/extension before reading; a size threshold catches the tail.
- **The exclude list is the deliverable** — the ranking says *what*, the
  `--suggest-ignore` globs are the *action*.
- **Cross-platform and crash-proof** — pure stdlib `pathlib`, relative POSIX
  paths in output, explicit UTF-8 with `errors="replace"` so a Windows cp1252
  default can never abort a scan.

## License

MIT — see [LICENSE](LICENSE).
