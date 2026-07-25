"""Smallest self-check that fails if the core logic breaks. No framework.

    python -m scripts.test_audit

Covers the load-bearing behaviours: bloat is flagged and ranked, ordinary
source is left alone, binary files report size but NOT a token count, and
ignore-glob suggestion dedupes correctly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.audit import audit, suggest_ignore


def _build(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "package-lock.json").write_text("{}\n" + "x" * 6000, encoding="utf-8")
    (root / "app.min.js").write_text("var a=1;" * 100, encoding="utf-8")
    nm = root / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports=1;" * 50, encoding="utf-8")
    # A real binary: PNG magic + a null byte, over the 1 KB threshold used below.
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00" + b"\x00" * 2000)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build(root)
        rep = audit(root, min_size_kb=1, detail=False)
        paths = [f.path for f in rep.findings]

        # 3+4: ordinary source is never flagged.
        assert not any("main.py" in p for p in paths), paths

        # 2: known bloat is flagged.
        assert any(f.category == "lockfile" for f in rep.findings)
        assert any(f.category == "dependency dir" for f in rep.findings)
        assert any(f.category == "minified asset" for f in rep.findings)

        # Binary: flagged, size reported, tokens refused (None).
        png = next(f for f in rep.findings if f.path == "logo.png")
        assert png.tokens is None, "binary must not get a token estimate"
        assert png.size > 2000

        # Ranking: lockfile (largest text) outranks the min.js asset.
        lock_i = paths.index("package-lock.json")
        js_i = paths.index("app.min.js")
        assert lock_i < js_i, paths

        # Ignore globs: deduplicated + generalized.
        pats = suggest_ignore(rep)
        assert "node_modules/" in pats
        assert "package-lock.json" in pats
        assert "*.min.js" in pats
        assert "*.png" in pats
        assert len(pats) == len(set(pats)), "patterns must be deduplicated"

    print("ok - all self-checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
