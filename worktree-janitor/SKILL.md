---
name: worktree-janitor
description: >-
  Audit and safely clean up stale git worktrees — the ones coding agents
  (Claude Code, Codex, and friends) leave behind after parallel tasks. Produces
  a ranked, read-only report per worktree: branch, uncommitted-changes status,
  last-commit age, merged-into-default status, and ahead/behind counts, with a
  conservative recommendation (safe to remove / has uncommitted work / unmerged
  / unknown). An opt-in, double-confirmed --prune removes only worktrees already
  proven safe. Use this whenever the user wants to clean up, prune, garbage-
  collect, or make sense of piled-up git worktrees, asks "which worktrees can I
  delete", "why do I have so many worktrees", or "is it safe to remove this
  worktree" — even if they don't use the word "worktree".
---

# Worktree janitor

Coding agents isolate parallel work in `git worktree`s — one per feature or
task, so uncommitted changes never collide. That's good hygiene while the work
is live and a mess afterward: worktrees for abandoned tasks, worktrees holding
uncommitted work nobody remembers, worktrees whose branch was merged and
deleted upstream while the local checkout lingers. `git worktree list` shows
you the pile but not which pieces are safe to remove.

This skill answers exactly that, and it answers it **conservatively**.

## Safety boundaries (non-negotiable)

These are the rules the tool is built around. State them to the user before
running anything destructive:

- **The audit is read-only.** Running without `--prune` never removes anything.
- **Removal needs two flags.** `--prune` *and* `--yes`. No single flag can
  delete a worktree; `--prune` alone prints the plan and refuses.
- **Only proven-safe worktrees are eligible.** Removal touches only worktrees
  already classified `safe to remove`, and each is re-checked (`git status
  --porcelain`) immediately before removal — stale audit state can't cause a
  surprise deletion.
- **The primary worktree is never touched.** The worktree holding the repo's
  `.git` is excluded from every destructive path and labelled `primary — never
  touched`.
- **Ambiguity never resolves to safe.** Detached HEAD, no discoverable default
  branch, a failed git call — all become `unknown — needs manual review`. The
  tool never guesses a worktree is safe.

## When to reach for this

The user has accumulated git worktrees and wants to understand or clean them
up. Signals: "clean up my worktrees", "which of these can I delete", "why do I
have twelve worktrees", "prune merged worktrees", "is this worktree safe to
remove". The word "worktree" need not appear — "the agent left a bunch of
checkouts around" counts too.

## The classifications

Every non-primary worktree lands in exactly one bucket:

| Classification | Meaning | Removable? |
|----------------|---------|------------|
| `safe to remove` | Clean tree, merged into the default branch, nothing unpushed | yes (via `--prune --yes`) |
| `has uncommitted work — do NOT remove` | `git status --porcelain` is non-empty | never |
| `unmerged commits — review before removing` | Branch tip isn't in the default branch, or has commits not pushed to its upstream | not automatically |
| `unknown — needs manual review` | Detached HEAD, no default branch found, or a git command was ambiguous/failed | not automatically |

`safe to remove` is the *only* bucket `--prune` ever acts on.

## Workflow

### Step 1 — Audit (always first, always read-only)

```bash
python -m scripts.janitor --repo /path/to/repo
```

Enumerates worktrees via `git worktree list --porcelain` and prints, per
worktree: branch, uncommitted status, last-commit age, upstream + ahead/behind,
merged status, and the classification with a one-line reason. The primary
worktree is shown first and labelled `primary — never touched`.

Add `--json` for machine-readable output (same data, scriptable).

### Step 2 — Review with the user

Walk the report together. The interesting rows are `safe to remove` (candidates
for pruning) and `unknown — needs manual review` (the tool refused to judge —
usually a detached HEAD or a repo with no clear default branch; a human should
look). `has uncommitted work` and `unmerged` rows are telling you there's work
that would be lost — do not push to remove them.

### Step 3 — Prune (opt-in, double-confirmed)

Only after the user has seen the report and confirmed:

```bash
python -m scripts.janitor --repo /path/to/repo --prune --yes
```

This lists exactly which worktrees it will remove (only the `safe to remove`
ones), re-checks each is still clean, then runs `git worktree remove` on them.
`--prune` without `--yes` prints the same plan and refuses — by design, so a
single flag can never delete anything.

## Try it end-to-end

`examples/make_fixture_repo.py` builds a throwaway repo (under a temp dir it
controls) with four worktrees — one merged, one dirty, one unmerged, one
detached — so you can watch every classification and a real prune without
risking a real repo:

```bash
python -m examples.make_fixture_repo          # prints the fixture repo path
python -m scripts.janitor --repo <that path>  # see all four classifications
python -m scripts.janitor --repo <that path> --prune --yes  # removes only the merged one
```

## Principles (why it's built this way)

- **Git is the source of truth about git.** Everything shells out to `git` via
  subprocess; the tool reimplements no git internals. It parses the stable
  `--porcelain` format, never the human-readable one.
- **Conservative by construction.** The default is to do nothing. Destruction
  is opt-in, double-confirmed, re-verified, and limited to a single explicitly-
  proven bucket.
- **Ambiguity is a first-class outcome.** No upstream, detached HEAD, no default
  branch — each is its own reported state, never silently coerced to "safe".
  A wrong "safe" verdict costs someone their work; an honest "unknown" costs a
  minute of review.
- **Merged means the work already lives elsewhere.** `safe to remove` requires
  the worktree's HEAD to be an ancestor of the default branch — its commits are
  already there, so removing the worktree loses nothing.
- **Windows-correct.** All captured subprocess output is decoded UTF-8 with
  `errors="replace"`, and stdout is reconfigured to UTF-8, so an odd byte in a
  path or commit message never crashes the run on a cp1252 console.

## Layout

```
worktree-janitor/
├── SKILL.md                     # this file: the workflow Claude Code follows
├── README.md                    # standalone usage + design principles
├── scripts/
│   └── janitor.py               # audit + double-confirmed prune (stdlib only)
├── examples/
│   └── make_fixture_repo.py     # builds a temp repo with 4 varied worktrees
└── LICENSE
```
