---
name: context-budget-auditor
description: >-
  Scan a repository for the files and directories that would blow a coding
  agent's context budget if read naively — huge generated files, lockfiles,
  vendored dependency trees, minified bundles, binary blobs, deeply nested
  node_modules — and rank them by estimated token cost with paste-ready
  exclude globs. Use this skill whenever the user is about to start work on an
  unfamiliar or large repo, or asks things like "why is my context filling
  up", "what should I exclude", "this repo is huge", "scope this search",
  "what's eating my context window", "which files are too big to read", or
  "set up a .claudeignore" — even if they never say the words "token" or
  "context window".
---

# Context budget auditor

A coding agent reads files into a finite context window. The fastest way to
waste it is to read the wrong file first — a 4 MB `package-lock.json`, a
vendored `node_modules` tree, a minified bundle, a checked-in SQLite database.
This skill scans a repo *before* you dive in, ranks the context-expensive paths
by what they would cost, and hands back exclude globs so you never read them by
accident.

The guiding principle throughout: **never fabricate a number.** Token counts
are honest estimates, labelled as estimates. Files we cannot meaningfully
estimate (binaries) report their size and say so, rather than pretending a byte
count is a token count.

## When to reach for this

Run it at the **start of a session on an unfamiliar or large repo**, before you
grep broadly or start reading files to build a mental model. Signals from the
user: "why is context filling up", "what should I exclude", "this repo is
huge", "scope this search down", "which files are too big", "set up a
.claudeignore", "what's safe to ignore". The words "token" or "context window"
need not appear — the shape of the request is *"help me not drown in this
repo."*

It is also worth a pass whenever a naive `grep`/glob is about to run against a
tree that might contain vendored code — the `--suggest-ignore` output doubles
as a set of `!pattern` scopes.

## The mental model

Three facts drive every decision the auditor makes:

1. **Bytes are a proxy for tokens.** ~4 bytes of English text or code is
   roughly one token. It is a heuristic, not a tokenizer — good enough to rank
   "read this last" vs "never read this", not good enough to bill against.
2. **Some files aren't text at all.** A `.png` or `.sqlite` has a size but no
   sensible token count; feeding it to the model yields garbage, not context.
   Those are flagged, never estimated.
3. **Bloat clusters in known shapes.** Lockfiles, dependency dirs, minified
   assets, and media are recognizable by name/extension before you read a byte.
   Anything else large enough is caught by a size threshold.

## Workflow

### Step 1 — Scan the repo

From the repo root:

```bash
python -m scripts.audit --path .
```

You get a ranked table: estimated tokens, size, category, and path, heaviest
first. Dependency directories are rolled up into a single `du`-style line
(file count + total size) so the report stays readable instead of listing
50,000 files. Pass `--detail` if you genuinely want every flagged file.

Tune the catch-all with `--min-size-kb` (default 256): the threshold above
which *any* file is flagged regardless of category. Lower it on a repo of many
medium files; raise it if the report is noisy.

### Step 2 — Read the report

- **Top rows are your risk.** A single multi-hundred-KB lockfile at the top is
  the classic context sink — one accidental read and it is gone.
- **`~` means estimated.** Every token number is `bytes / 4`. Treat it as an
  order-of-magnitude signal, not an exact cost.
- **`binary` in the token column** means "size known, tokens unknowable — do
  not read this as text." That is a refusal to guess, not a missing value.
- **Rolled-up dep dirs** show a total and a file count. The token figure is a
  rough upper bound (it assumes the whole tree is readable text, which it is
  not) — its job is to say "this subtree is enormous," nothing finer.

### Step 3 — Act on `--suggest-ignore`

```bash
python -m scripts.audit --path . --suggest-ignore
```

This emits deduplicated, sorted gitignore-glob patterns — `node_modules/`,
`package-lock.json`, `*.min.js`, `*.png`, and so on. Two ways to use them:

- **Persist them:** paste into a `.claudeignore` (or `.gitignore`-style file
  your tooling honours) so these paths stay out of context for the whole
  session.
- **Scope one search:** pass them as negative globs (`--glob '!node_modules/'`
  style) to a single grep/glob invocation.

Patterns that can be generalized are (lockfile *names*, extension globs,
directory names); a one-off large file with no safe generalization is listed as
its exact path, so you never over-exclude by accident.

### Step 4 — Machine consumption (optional)

```bash
python -m scripts.audit --path . --json
```

Emits the same findings plus the stated assumptions block and the suggested
ignore list, for scripting or feeding to another tool. `estimated_tokens` is
`null` for binary files by design — a consumer must handle that rather than
receive a fabricated number.

## Interpreting the numbers honestly

The `--json` output carries an `assumptions` block spelling out
`bytes // 4` and its limits. Repeat that to the user if they treat the token
figure as exact: it is a ranking heuristic. The one number you can trust
literally is `size_bytes`; everything token-shaped is derived from it.

## Principles (why this is built the way it is)

- **Never fabricate a number.** Token counts are labelled estimates; binaries
  report size and refuse a token count rather than inventing one.
- **Rank, don't itemize.** A dependency tree is one rolled-up line, not 50,000.
  The report is for a human deciding what to exclude, not an inventory.
- **Recognize bloat by shape.** Lockfiles, dep dirs, and minified assets are
  known patterns — flag them by name before reading, then catch the long tail
  with a size threshold.
- **The exclude list is the deliverable.** The ranking tells you *what*; the
  `--suggest-ignore` globs are the *action*. Everything feeds that output.
- **Cross-platform and crash-proof.** Pure stdlib `pathlib`; relative POSIX
  paths in output; explicit UTF-8 with `errors="replace"` so a Windows cp1252
  default can never abort a scan.
