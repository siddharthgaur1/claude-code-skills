"""Create a throwaway git repo with worktrees in varied states, for testing.

Builds everything under a fresh temp directory it controls (never a path
outside tempfile's control) and prints that path so you can point janitor.py
at it:

    python -m examples.make_fixture_repo
    python -m scripts.janitor --repo <printed main path>

The fixture deliberately exercises every classification janitor can produce:

  * feat-merged   — clean, branch merged into main   -> safe to remove
  * feat-dirty    — clean-branch + uncommitted change -> has uncommitted work
  * feat-unmerged — clean, has a commit not on main   -> unmerged
  * detached      — detached HEAD                      -> unknown (ambiguous)

Nothing here talks to a network or a remote; it's fully local and
reproducible. Delete the printed directory when you're done.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _commit(repo, name, content):
    (Path(repo) / name).write_text(content, encoding="utf-8")
    git(["add", name], cwd=repo)
    git(["commit", "-m", f"add {name}"], cwd=repo)


def build(base=None):
    base = Path(base) if base else Path(tempfile.mkdtemp(prefix="wtj-fixture-"))
    main = base / "repo"
    main.mkdir(parents=True, exist_ok=True)

    git(["init", "-b", "main"], cwd=main)
    git(["config", "user.email", "fixture@example.com"], cwd=main)
    git(["config", "user.name", "Fixture"], cwd=main)
    _commit(main, "README.md", "fixture repo\n")

    wt_dir = base / "worktrees"
    wt_dir.mkdir(exist_ok=True)

    # 1) merged branch -> SAFE. Make the commit on a branch, merge to main.
    git(["branch", "feat-merged"], cwd=main)
    git(["worktree", "add", str(wt_dir / "feat-merged"), "feat-merged"], cwd=main)
    _commit(wt_dir / "feat-merged", "merged.txt", "done work\n")
    git(["merge", "--no-ff", "feat-merged", "-m", "merge feat-merged"], cwd=main)

    # 2) branch with an uncommitted change -> DIRTY.
    git(["worktree", "add", "-b", "feat-dirty", str(wt_dir / "feat-dirty"), "main"], cwd=main)
    _commit(wt_dir / "feat-dirty", "wip.txt", "committed part\n")
    (wt_dir / "feat-dirty" / "wip.txt").write_text("uncommitted edit\n", encoding="utf-8")

    # 3) branch with a commit not on main -> UNMERGED.
    git(["worktree", "add", "-b", "feat-unmerged", str(wt_dir / "feat-unmerged"), "main"], cwd=main)
    _commit(wt_dir / "feat-unmerged", "feature.txt", "unmerged feature\n")

    # 4) detached HEAD -> UNKNOWN (ambiguous).
    rc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(main),
                        capture_output=True, text=True, encoding="utf-8").stdout.strip()
    git(["worktree", "add", "--detach", str(wt_dir / "detached"), rc], cwd=main)

    return main


def main():
    main_path = build()
    print(main_path)


if __name__ == "__main__":
    main()
