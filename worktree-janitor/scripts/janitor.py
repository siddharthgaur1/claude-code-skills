"""Audit git worktrees and (opt-in, double-confirmed) prune the safe ones.

Coding agents — Claude Code, Codex, and friends — spin up a `git worktree`
per parallel task so they don't clobber each other's uncommitted work. Over
weeks these accumulate: worktrees for abandoned tasks, worktrees holding
uncommitted work nobody remembers, worktrees whose branch was merged and
deleted upstream while the local worktree lingers. `git worktree list` alone
won't tell you which are safe to remove. This does.

SAFETY BOUNDARIES (non-negotiable — read before touching --prune):
  * The audit is READ-ONLY. Running without --prune never removes anything.
  * Removal requires BOTH --prune AND --yes. No single flag can delete a
    worktree. --prune without --yes prints the plan and refuses.
  * Only worktrees classified `safe to remove` are ever eligible for removal,
    and each is re-checked (git status --porcelain) immediately before removal
    so stale audit state can't cause a surprise deletion.
  * The primary worktree (the one holding the repo's .git) is excluded from
    every destructive path, always, and labelled "primary — never touched".
  * Ambiguity never resolves to safe. Detached HEAD, no discoverable default
    branch, a failed git call — all become `unknown — needs manual review`,
    never `safe to remove`.

Design choices (why it's built this way):
  * We shell out to `git` via subprocess for everything and never reimplement
    git internals — git is the source of truth about git.
  * We parse `git worktree list --porcelain`, not the human-readable form:
    the porcelain format is stable and scriptable.
  * All captured output uses text=True, encoding="utf-8", errors="replace" so
    a non-UTF-8 byte in a commit message or path can't crash the run on a
    Windows cp1252 console (a real failure mode on this dev machine).
  * "Merged" means the worktree's HEAD commit is an ancestor of the repo's
    default branch — i.e. its work already lives there and losing the worktree
    loses nothing. That, plus a clean tree and nothing unpushed, is the only
    path to `safe to remove`.

Usage:
    python -m scripts.janitor --repo /path/to/repo          # audit (read-only)
    python -m scripts.janitor --repo /path/to/repo --json    # machine-readable
    python -m scripts.janitor --repo /path/to/repo --prune --yes  # remove safe
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Classification labels (single source of truth so audit and prune agree).
SAFE = "safe to remove"
DIRTY = "has uncommitted work — do NOT remove"
UNMERGED = "unmerged commits — review before removing"
UNKNOWN = "unknown — needs manual review"


def git(args, cwd=None):
    """Run a git command, returning (returncode, stdout, stderr).

    Never raises on a non-zero exit — callers decide what a failure means.
    Captured text is decoded as UTF-8 with errors="replace" so an odd byte
    in a path or commit message degrades a character instead of crashing.
    """
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def parse_worktrees(repo):
    """Enumerate worktrees via porcelain output.

    Returns a list of dicts: {path, head, branch, detached, bare}. The first
    record git emits is the primary worktree; we mark it so it can be excluded
    from every destructive path.
    """
    rc, out, err = git(["worktree", "list", "--porcelain"], cwd=repo)
    if rc != 0:
        raise RuntimeError(f"`git worktree list` failed: {err or out}")

    records, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                records.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "head": None, "branch": None,
                   "detached": False, "bare": False}
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val  # e.g. refs/heads/feature
        elif key == "detached":
            cur["detached"] = True
        elif key == "bare":
            cur["bare"] = True
    if cur:
        records.append(cur)

    for i, r in enumerate(records):
        r["primary"] = (i == 0)
    return records


def default_branch_ref(repo):
    """Discover the ref to treat as 'main' to test merged-ness against.

    Prefers origin/HEAD (what upstream considers default). Falls back to a
    local main/master. Returns None if nothing discoverable — the caller then
    reports UNKNOWN rather than guessing, because a wrong default would produce
    a wrong (and possibly destructive) "merged" verdict.
    """
    rc, out, _ = git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo)
    if rc == 0 and out:
        return out[len("refs/remotes/"):] if out.startswith("refs/remotes/") else out
    for name in ("main", "master"):
        rc, _, _ = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], cwd=repo)
        if rc == 0:
            return name
    return None


def human_age(unix_ts, now=None):
    if unix_ts is None:
        return "unknown"
    now = now if now is not None else time.time()
    days = int((now - unix_ts) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def audit_worktree(wt, repo, default_ref, now=None):
    """Classify a single non-primary worktree. Pure of side effects.

    Conservative by construction: dirty short-circuits to DIRTY; anything that
    can't be cleanly determined lands in UNKNOWN; SAFE requires clean AND
    merged into the default branch AND nothing unpushed.
    """
    path = wt["path"]
    info = {
        "path": path,
        "branch": wt["branch"][len("refs/heads/"):] if wt["branch"] else None,
        "head": wt["head"],
        "detached": wt["detached"],
        "dirty": None,
        "last_commit_age": None,
        "last_commit_ts": None,
        "upstream": None,
        "ahead": None,
        "behind": None,
        "merged": None,
        "classification": UNKNOWN,
        "reason": "",
    }

    # Uncommitted changes — the hardest gate. Non-empty porcelain == dirty.
    rc, out, err = git(["status", "--porcelain"], cwd=path)
    if rc != 0:
        info["reason"] = f"git status failed: {err or out}"
        return info
    info["dirty"] = bool(out.strip())

    # Last commit age off HEAD.
    rc, out, _ = git(["log", "-1", "--format=%ct", "HEAD"], cwd=path)
    if rc == 0 and out.isdigit():
        info["last_commit_ts"] = int(out)
        info["last_commit_age"] = human_age(int(out), now=now)

    # Upstream + ahead/behind (its own state when absent — not a crash).
    rc, ups, _ = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=path)
    if rc == 0 and ups:
        info["upstream"] = ups
        rc2, counts, _ = git(["rev-list", "--left-right", "--count", f"HEAD...{ups}"], cwd=path)
        if rc2 == 0 and "\t" in counts:
            a, b = counts.split("\t")
            if a.isdigit() and b.isdigit():
                info["ahead"], info["behind"] = int(a), int(b)

    # Merged: is this worktree's HEAD an ancestor of the default branch?
    if default_ref and info["head"] and not wt["detached"]:
        rc, _, _ = git(["merge-base", "--is-ancestor", info["head"], default_ref], cwd=repo)
        # rc 0 -> ancestor (merged); rc 1 -> not; anything else -> unknown.
        if rc == 0:
            info["merged"] = True
        elif rc == 1:
            info["merged"] = False
        # else leave None (UNKNOWN)

    info["classification"], info["reason"] = _classify(info, wt, default_ref)
    return info


def _classify(info, wt, default_ref):
    if info["dirty"]:
        return DIRTY, "working tree has uncommitted changes"
    if wt["detached"]:
        return UNKNOWN, "detached HEAD — no branch to reason about"
    if default_ref is None:
        return UNKNOWN, "no default branch discoverable to test merged-ness"
    if info["merged"] is None:
        return UNKNOWN, "could not determine whether branch is merged"
    if info["merged"] is False:
        return UNMERGED, "branch tip is not in the default branch"
    # merged is True and tree is clean below here.
    if info["upstream"] and info["ahead"]:
        return UNMERGED, f"{info['ahead']} commit(s) not pushed to {info['upstream']}"
    return SAFE, "clean, merged into default branch, nothing unpushed"


def audit(repo, now=None):
    """Audit every worktree in `repo`. Read-only. Returns a result dict."""
    repo = Path(repo).resolve()
    worktrees = parse_worktrees(repo)
    default_ref = default_branch_ref(repo)

    results = []
    for wt in worktrees:
        if wt["primary"]:
            results.append({
                "path": wt["path"],
                "branch": wt["branch"][len("refs/heads/"):] if wt["branch"] else None,
                "primary": True,
                "classification": "primary — never touched",
                "reason": "holds the repo's .git; excluded from all removal",
            })
            continue
        if wt["bare"]:
            results.append({"path": wt["path"], "primary": False, "bare": True,
                            "classification": UNKNOWN, "reason": "bare worktree"})
            continue
        info = audit_worktree(wt, repo, default_ref, now=now)
        info["primary"] = False
        results.append(info)

    return {"repo": str(repo), "default_branch": default_ref, "worktrees": results}


def print_report(result):
    print(f"Worktree audit for {result['repo']}")
    print(f"Default branch: {result['default_branch'] or '(none found)'}")
    print("-" * 72)
    for w in result["worktrees"]:
        tag = "[PRIMARY]" if w.get("primary") else ""
        print(f"{tag} {w['path']}")
        if w.get("branch"):
            print(f"    branch:       {w['branch']}")
        if "dirty" in w and w.get("dirty") is not None:
            print(f"    uncommitted:  {'yes' if w['dirty'] else 'no'}")
        if w.get("last_commit_age"):
            print(f"    last commit:  {w['last_commit_age']}")
        if w.get("upstream"):
            print(f"    upstream:     {w['upstream']} (ahead {w['ahead']}, behind {w['behind']})")
        if w.get("merged") is not None:
            print(f"    merged:       {'yes' if w['merged'] else 'no'}")
        print(f"    -> {w['classification']}")
        if w.get("reason"):
            print(f"       {w['reason']}")
        print()
    safe = [w for w in result["worktrees"] if w["classification"] == SAFE]
    print(f"{len(safe)} worktree(s) classified '{SAFE}'.")
    if safe:
        print("Review, then run with --prune --yes to remove them.")


def prune(repo, assume_yes, now=None):
    """Remove ONLY worktrees classified SAFE, and only with assume_yes=True.

    Re-verifies each candidate's working tree is still clean immediately before
    removal, so a change between audit and prune can't cause a surprise delete.
    The primary worktree is never a candidate (audit never classifies it SAFE).
    """
    result = audit(repo, now=now)
    candidates = [w for w in result["worktrees"]
                  if not w.get("primary") and w["classification"] == SAFE]

    if not candidates:
        print("Nothing classified 'safe to remove'. Nothing to do.")
        return 0

    print("The following worktrees are classified 'safe to remove':")
    for w in candidates:
        print(f"  {w['path']}  (branch {w.get('branch')})")
    print()

    if not assume_yes:
        # --prune alone must never delete. Refuse and explain.
        print("Refusing to remove anything: --prune requires --yes as well.")
        print("Re-run with:  --prune --yes   to actually remove the above.")
        return 1

    removed, skipped = [], []
    for w in candidates:
        path = w["path"]
        # Re-check right before acting — don't trust stale audit state.
        rc, out, _ = git(["status", "--porcelain"], cwd=path)
        if rc != 0 or out.strip():
            print(f"SKIP {path}: state changed since audit (now dirty or unreadable).")
            skipped.append(path)
            continue
        print(f"Removing worktree: {path}")
        rc, out, err = git(["worktree", "remove", path], cwd=repo)
        if rc != 0:
            print(f"  FAILED: {err or out}")
            skipped.append(path)
        else:
            removed.append(path)

    print()
    print(f"Removed {len(removed)}, skipped {len(skipped)}.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Audit git worktrees and optionally prune the safe ones.")
    p.add_argument("--repo", default=".", help="Path to the git repo (default: cwd).")
    p.add_argument("--json", action="store_true", help="Emit the audit as JSON.")
    p.add_argument("--prune", action="store_true",
                   help="Remove worktrees classified 'safe to remove'. Requires --yes.")
    p.add_argument("--yes", action="store_true",
                   help="Confirm removal. Required alongside --prune; no single flag deletes.")
    args = p.parse_args(argv)

    # Labels contain em-dashes; a Windows cp1252 console would otherwise mangle
    # them. Reconfigure to UTF-8 where the runtime supports it (Python 3.7+).
    for stream in (sys.stdout, sys.stderr):
        reconfig = getattr(stream, "reconfigure", None)
        if reconfig:
            try:
                reconfig(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    try:
        if args.prune:
            return prune(args.repo, assume_yes=args.yes)
        result = audit(args.repo)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_report(result)
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
