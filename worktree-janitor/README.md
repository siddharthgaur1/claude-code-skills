# worktree-janitor

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skill** that
audits and safely cleans up stale `git worktree`s — the ones coding agents
(Claude Code, Codex, and friends) leave behind after parallel tasks.

Agents isolate parallel work in one worktree per task so uncommitted changes
never collide. Weeks later you have a pile: abandoned tasks, worktrees holding
forgotten uncommitted work, worktrees whose branch was merged and deleted
upstream while the local checkout lingers. `git worktree list` shows the pile
but not which pieces are safe to remove. This tells you — conservatively — and
will remove only the ones it can prove are safe, and only when you ask twice.

## Safety boundaries (non-negotiable)

- **The audit is read-only.** Without `--prune`, nothing is ever removed.
- **Removal needs two flags:** `--prune` *and* `--yes`. No single flag deletes.
  `--prune` alone prints the plan and refuses.
- **Only proven-safe worktrees are eligible** — those classified `safe to
  remove` — and each is re-checked (`git status --porcelain`) immediately
  before removal, so a change between audit and prune can't cause a surprise
  delete.
- **The primary worktree** (the one holding the repo's `.git`) is excluded from
  every destructive path, always, and labelled `primary — never touched`.
- **Ambiguity never resolves to safe.** Detached HEAD, no discoverable default
  branch, or a failed git call all become `unknown — needs manual review`.

## What's a skill?

A skill is a folder with a `SKILL.md` that teaches Claude Code a repeatable
workflow. Drop this folder into your Claude Code skills directory and asking
Claude to "clean up my worktrees" invokes this workflow. The `scripts/` here
are real, runnable code the skill drives — not pseudocode.

## Use it standalone (no Claude Code required)

Zero third-party dependencies — Python 3.11+ stdlib only, plus `git` on PATH.

```bash
# Audit (read-only): rank every worktree with a recommendation.
python -m scripts.janitor --repo /path/to/repo

# Same data, machine-readable.
python -m scripts.janitor --repo /path/to/repo --json

# Remove only worktrees classified 'safe to remove' (needs BOTH flags).
python -m scripts.janitor --repo /path/to/repo --prune --yes
```

Example audit output:

```
Worktree audit for /path/to/repo
Default branch: main
------------------------------------------------------------------------
[PRIMARY] /path/to/repo
    branch:       main
    -> primary — never touched

 /path/to/worktrees/feat-merged
    branch:       feat-merged
    uncommitted:  no
    last commit:  3 days ago
    merged:       yes
    -> safe to remove
       clean, merged into default branch, nothing unpushed

 /path/to/worktrees/feat-dirty
    branch:       feat-dirty
    uncommitted:  yes
    -> has uncommitted work — do NOT remove
```

## What each classification means

| Classification | Criteria | Removable |
|----------------|----------|-----------|
| `safe to remove` | Clean tree AND merged into the default branch AND nothing unpushed | yes, via `--prune --yes` |
| `has uncommitted work — do NOT remove` | `git status --porcelain` non-empty | never |
| `unmerged commits — review before removing` | Branch tip not in default branch, or commits unpushed to upstream | not automatically |
| `unknown — needs manual review` | Detached HEAD, no default branch found, or an ambiguous/failed git check | not automatically |

## Try it on a throwaway repo

`examples/make_fixture_repo.py` builds a temp repo (under a directory it
controls — nothing outside `tempfile`) with four worktrees exercising every
classification:

```bash
python -m examples.make_fixture_repo          # prints the fixture repo path
python -m scripts.janitor --repo <that path>          # audit
python -m scripts.janitor --repo <that path> --prune --yes  # removes only feat-merged
```

## Layout

```
worktree-janitor/
├── SKILL.md                     # the skill: workflow Claude Code follows
├── README.md                    # this file
├── scripts/
│   └── janitor.py               # audit + double-confirmed prune (stdlib only)
├── examples/
│   └── make_fixture_repo.py     # builds a temp repo with 4 varied worktrees
└── LICENSE
```

## How it decides

- Enumerates worktrees with `git worktree list --porcelain` (stable, scriptable
  — never the human-readable form).
- Uncommitted: `git -C <wt> status --porcelain` (non-empty ⇒ dirty ⇒ never safe).
- Last-commit age: `git -C <wt> log -1 --format=%ct` on HEAD.
- Upstream + ahead/behind: `rev-parse @{u}` then `rev-list --left-right
  --count` (no upstream is its own reported state, not an error).
- Merged: the default branch is found via `symbolic-ref
  refs/remotes/origin/HEAD` (falling back to a local `main`/`master`), then
  `git merge-base --is-ancestor <HEAD> <default>` tests whether the worktree's
  work already lives there.

## Design principles

- **Git is the source of truth about git** — everything shells out to `git`;
  no git internals are reimplemented.
- **Conservative by construction** — the default is to do nothing; destruction
  is opt-in, double-confirmed, re-verified, and limited to a single proven
  bucket.
- **Ambiguity is a first-class outcome** — unknowns are reported as unknown,
  never coerced to safe. A wrong "safe" costs someone their work.
- **Windows-correct** — captured subprocess output is decoded UTF-8 with
  `errors="replace"` and stdout is reconfigured to UTF-8, so a stray byte on a
  cp1252 console can't crash the run.

## License

MIT — see [LICENSE](LICENSE).
