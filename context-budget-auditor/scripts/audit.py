"""Scan a repository for paths that would blow a coding agent's context budget.

    python -m scripts.audit --path .            # ranked table to stdout
    python -m scripts.audit --path . --json     # machine-readable
    python -m scripts.audit --path . --suggest-ignore   # .claudeignore globs

Why this exists
---------------
A coding agent reads files into a finite context window. Read the wrong file —
a 4 MB lockfile, a vendored dependency tree, a minified bundle — and you burn
the budget on bytes that carry almost no signal. This tool finds those paths
*before* you start a session, ranks them by how much context they would cost,
and hands back exclude globs you can paste into a `.claudeignore`.

Honesty about the numbers (read this)
-------------------------------------
Token counts here are ESTIMATES, not truth. We use `bytes // 4`, the standard
rough approximation for English text and code (~4 bytes per token). It is a
heuristic, not a tokenizer: prose runs leaner, dense code and non-Latin scripts
run heavier. Every number is prefixed `~` and the assumption is printed with
the report. For binary files we refuse to guess a token count at all — we
report the byte size and flag the file as "binary, not directly readable as
context", because feeding it to a text model produces garbage, not tokens you
can reason about.

Design choices
--------------
- Flagged dependency directories (node_modules, .venv, target, ...) are rolled
  up into ONE line each — file count + total size, the way `du -sh` reports —
  instead of listing 50,000 files nobody will read. `--detail` disables the
  rollup and lists every flagged file.
- Everything opens with `encoding="utf-8", errors="replace"`; nothing here
  actually decodes file *text* (we sniff bytes), but the rule stands so a
  Windows cp1252 default can never crash the scan.
- Pure `pathlib` / `os.walk`; no POSIX assumptions; Python 3.11+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# ~4 bytes per token is the common rough rule for English text/code. Stated
# loudly because it is a heuristic, not a tokenizer.
BYTES_PER_TOKEN = 4

# Directories that are almost never worth reading into context. Matched by
# exact directory NAME at any depth, then rolled up and pruned from the walk.
DEP_DIRS = {
    "node_modules", "vendor", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", "target", ".tox", ".mypy_cache",
    ".pytest_cache", ".gradle", "bower_components",
}

# Lockfiles: huge, machine-generated, and you basically never need to read one.
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
    "poetry.lock", "Gemfile.lock", "composer.lock", "Pipfile.lock",
}

# Binary / media / archive extensions: report size, never estimate tokens.
BINARY_EXTS = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".tif",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # audio / video
    ".mp4", ".mov", ".avi", ".mp3", ".wav", ".webm", ".mkv", ".flac",
    # archives / compiled / docs
    ".zip", ".gz", ".tar", ".7z", ".rar", ".bz2", ".xz",
    ".pdf", ".so", ".dll", ".dylib", ".o", ".a", ".class", ".pyc", ".wasm",
}

# On-disk data stores: opaque, large, not meant to be read as text.
DATA_EXTS = {".sqlite", ".sqlite3", ".db", ".parquet", ".pkl", ".npy", ".npz"}

NULL_SNIFF_BYTES = 8192


@dataclass
class Finding:
    """One context-expensive path. `tokens` is None for binary (unestimable)."""
    path: str            # relative, POSIX-style, for stable cross-OS output
    kind: str            # "file" or "dir"
    category: str        # why it was flagged
    size: int            # bytes
    tokens: int | None   # estimated tokens, or None if binary/unestimable
    files: int = 1       # >1 only for rolled-up directories
    note: str = ""

    @property
    def sort_key(self) -> int:
        # Rank by estimated token cost; binary files fall back to size/4 purely
        # for ordering (never shown as a token count).
        return self.tokens if self.tokens is not None else self.size // BYTES_PER_TOKEN


@dataclass
class Report:
    root: str
    min_size_kb: int
    findings: list[Finding] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)  # paths we couldn't stat


def _is_binary(p: Path) -> bool:
    """Null-byte sniff on the first few KB. Cheap, standard, good enough."""
    try:
        with p.open("rb") as fh:
            return b"\x00" in fh.read(NULL_SNIFF_BYTES)
    except OSError:
        return False


def _classify(p: Path, size: int, min_size: int) -> tuple[str, bool] | None:
    """Return (category, is_binary) if the file is bloat, else None.

    `is_binary` decides whether we estimate tokens or refuse to.
    """
    name = p.name
    suffix = p.suffix.lower()
    lname = name.lower()

    if name in LOCKFILES:
        return ("lockfile", False)
    if lname.endswith(".min.js") or lname.endswith(".min.css"):
        return ("minified asset", False)
    if suffix in DATA_EXTS:
        return ("data store", True)
    if suffix in BINARY_EXTS:
        return ("binary/media", True)
    if suffix == ".csv" and size >= min_size:
        return ("large data file", False)
    if size >= min_size:
        # Catch-all: anything over threshold, category depends on a byte sniff.
        return ("binary blob" if _is_binary(p) else "large file", _is_binary(p))
    return None


def _dir_totals(root: Path) -> tuple[int, int]:
    """(file_count, total_bytes) for an entire subtree. Skips unstattable files."""
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            try:
                total += (Path(dirpath) / fn).stat().st_size
                files += 1
            except OSError:
                pass
    return files, total


def _rel(p: Path, root: Path) -> str:
    """Relative POSIX path so output is identical on Windows and *nix."""
    return str(PurePosixPath(p.relative_to(root).as_posix()))


def audit(root: Path, min_size_kb: int, detail: bool) -> Report:
    """Walk `root`, flag context-expensive paths, and return a Report."""
    root = root.resolve()
    min_size = min_size_kb * 1024
    rep = Report(root=str(root), min_size_kb=min_size_kb)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        here = Path(dirpath)

        # Always skip .git; roll up + prune dependency dirs (unless --detail).
        pruned = []
        keep = []
        for d in dirnames:
            if d == ".git":
                continue
            if d in DEP_DIRS and not detail:
                pruned.append(d)
            else:
                keep.append(d)
        dirnames[:] = keep

        for d in pruned:
            sub = here / d
            count, total = _dir_totals(sub)
            rep.findings.append(Finding(
                path=_rel(sub, root) + "/",
                kind="dir",
                category="dependency dir",
                size=total,
                tokens=total // BYTES_PER_TOKEN,
                files=count,
                note="rolled up (du-style); token estimate is a rough upper bound",
            ))

        for fn in filenames:
            fp = here / fn
            try:
                size = fp.stat().st_size
            except OSError:
                rep.unreadable.append(_rel(fp, root))
                continue
            hit = _classify(fp, size, min_size)
            if hit is None:
                continue
            category, is_binary = hit
            rep.findings.append(Finding(
                path=_rel(fp, root),
                kind="file",
                category=category,
                size=size,
                tokens=None if is_binary else size // BYTES_PER_TOKEN,
                note="binary — not directly readable as context" if is_binary else "",
            ))

    rep.findings.sort(key=lambda f: f.sort_key, reverse=True)
    return rep


# --- ignore-pattern suggestion -------------------------------------------------

def suggest_ignore(rep: Report) -> list[str]:
    """Derive deduplicated, sorted gitignore-glob patterns from the findings."""
    pats: set[str] = set()
    for f in rep.findings:
        if f.category == "dependency dir":
            pats.add(f.path.rstrip("/") + "/")          # node_modules/
        elif f.category == "lockfile":
            pats.add(PurePosixPath(f.path).name)         # package-lock.json
        elif f.category == "minified asset":
            ext = ".min.js" if f.path.lower().endswith(".min.js") else ".min.css"
            pats.add("*" + ext)                          # *.min.js
        elif f.category in ("binary/media", "data store"):
            suf = PurePosixPath(f.path).suffix.lower()
            pats.add("*" + suf if suf else f.path)       # *.png
        elif f.category == "large data file":
            pats.add("*.csv")
        else:
            # large/binary blob with no safe generalization: ignore the exact path.
            pats.add(f.path)
    return sorted(pats)


# --- rendering -----------------------------------------------------------------

def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _tok(t: int | None) -> str:
    return "binary" if t is None else f"~{t:,}"


def render_table(rep: Report, top: int) -> str:
    shown = rep.findings[:top]
    total_tokens = sum(f.tokens for f in rep.findings if f.tokens is not None)
    n_binary = sum(1 for f in rep.findings if f.tokens is None)

    lines = []
    lines.append(f"Context-budget audit of {rep.root}")
    lines.append(f"  token estimate = bytes / {BYTES_PER_TOKEN} (rough heuristic, not a tokenizer; ~ = estimated)")
    lines.append(f"  bloat threshold = {rep.min_size_kb} KB   |   {len(rep.findings)} flagged path(s)")
    lines.append("")

    if not shown:
        lines.append("  No context-expensive paths found. Repo is lean.")
        return "\n".join(lines)

    w_tok = max(len("EST.TOKENS"), max(len(_tok(f.tokens)) for f in shown))
    w_sz = max(len("SIZE"), max(len(_human(f.size)) for f in shown))
    w_cat = max(len("CATEGORY"), max(len(f.category) for f in shown))
    header = f"  {'EST.TOKENS':>{w_tok}}  {'SIZE':>{w_sz}}  {'CATEGORY':<{w_cat}}  PATH"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for f in shown:
        p = f.path
        if f.kind == "dir":
            p = f"{p}  ({f.files} files, rolled up)"
        lines.append(f"  {_tok(f.tokens):>{w_tok}}  {_human(f.size):>{w_sz}}  {f.category:<{w_cat}}  {p}")

    if len(rep.findings) > top:
        lines.append(f"  ... and {len(rep.findings) - top} more (raise --top to see them)")
    lines.append("")
    lines.append(f"  Estimated readable-text cost of flagged paths: ~{total_tokens:,} tokens"
                 + (f"  (+{n_binary} binary path(s), unestimable)" if n_binary else ""))
    if rep.unreadable:
        lines.append(f"  {len(rep.unreadable)} path(s) could not be stat'd and were skipped (not estimated).")
    lines.append("  Run with --suggest-ignore for paste-ready exclude globs.")
    return "\n".join(lines)


def to_json(rep: Report, top: int) -> str:
    payload = {
        "root": rep.root,
        "assumptions": {
            "token_estimate": f"bytes // {BYTES_PER_TOKEN}",
            "note": "Rough heuristic (~4 bytes/token), not a real tokenizer. "
                    "Binary files report size only; tokens is null.",
        },
        "min_size_kb": rep.min_size_kb,
        "total_flagged": len(rep.findings),
        "estimated_total_tokens": sum(f.tokens for f in rep.findings if f.tokens is not None),
        "unreadable_paths": rep.unreadable,
        "findings": [
            {
                "path": f.path,
                "kind": f.kind,
                "category": f.category,
                "size_bytes": f.size,
                "estimated_tokens": f.tokens,
                "file_count": f.files,
                "note": f.note,
            }
            for f in rep.findings[:top]
        ],
        "suggested_ignore": suggest_ignore(rep),
    }
    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="audit",
        description="Rank the paths that would blow a coding agent's context budget.",
    )
    ap.add_argument("--path", default=".", help="directory to scan (default: cwd)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--suggest-ignore", action="store_true",
                    help="emit deduplicated .claudeignore-style exclude globs")
    ap.add_argument("--top", type=int, default=20, help="rows to show (default: 20)")
    ap.add_argument("--min-size-kb", type=int, default=256,
                    help="catch-all bloat threshold in KB (default: 256)")
    ap.add_argument("--detail", action="store_true",
                    help="list every flagged file instead of rolling up dep dirs")
    args = ap.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        ap.error(f"path does not exist: {root}")
    if not root.is_dir():
        ap.error(f"path is not a directory: {root}")

    rep = audit(root, args.min_size_kb, args.detail)

    if args.suggest_ignore:
        pats = suggest_ignore(rep)
        if args.json:
            print(json.dumps({"suggested_ignore": pats}, indent=2))
        else:
            print("# Suggested .claudeignore patterns (deduplicated, sorted)")
            print("# Paste into .claudeignore, or pass as --glob '!<pattern>' to your tooling.")
            print("\n".join(pats) if pats else "# (nothing to exclude — repo is lean)")
        return 0

    print(to_json(rep, args.top) if args.json else render_table(rep, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
