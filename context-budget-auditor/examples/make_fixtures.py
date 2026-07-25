"""Generate the synthetic bloat files for the fixture repo.

Run once to (re)create the deliberately-bloated stand-ins next to the checked-in
normal source. Everything here is SYNTHETIC — a few KB standing in for the
multi-MB reality — so the repo stays tiny while still exercising every branch of
the auditor's pattern matching:

    python examples/make_fixtures.py

Kept small on purpose: the point is to prove the pattern/rollup logic fires, not
to ship real bloat. `src/main.py` (an ordinary small file) is committed by hand
and must stay UN-flagged.
"""

from __future__ import annotations

import json
from pathlib import Path

FIX = Path(__file__).parent / "fixture_repo"


def main() -> None:
    # 1. Lockfile — flagged by NAME, so it need not be huge. Stand-in for the
    #    real multi-MB package-lock.json.
    deps = {
        "_comment": "SYNTHETIC stand-in for a real (multi-MB) package-lock.json.",
        "name": "fixture", "version": "1.0.0", "lockfileVersion": 3,
        "packages": {
            f"node_modules/pkg-{i}": {
                "version": f"1.0.{i}",
                "resolved": f"https://registry.example/pkg-{i}/-/pkg-{i}-1.0.{i}.tgz",
                "integrity": "sha512-" + ("A" * 80),
                "dependencies": {f"pkg-{j}": f"^1.0.{j}" for j in range(i % 5)},
            }
            for i in range(400)
        },
    }
    (FIX / "package-lock.json").write_text(json.dumps(deps, indent=2), encoding="utf-8")

    # 2. Vendored dependency dir — flagged by DIRECTORY NAME and rolled up.
    nm = FIX / "node_modules" / "left-pad"
    nm.mkdir(parents=True, exist_ok=True)
    (nm / "index.js").write_text(
        "// SYNTHETIC vendored dependency stand-in.\n"
        "module.exports = function leftPad(s, n){ return String(s).padStart(n); };\n"
        + "// filler ".ljust(60) * 40,
        encoding="utf-8",
    )
    (nm / "package.json").write_text(
        json.dumps({"name": "left-pad", "version": "1.3.0"}, indent=2), encoding="utf-8")

    # 3. Minified asset — flagged by the .min.js suffix regardless of size.
    (FIX / "assets").mkdir(exist_ok=True)
    (FIX / "assets" / "bundle.min.js").write_text(
        "/* SYNTHETIC minified bundle stand-in */\n"
        + "!function(){var a=1,b=2;" + "c=a+b;" * 300 + "}();",
        encoding="utf-8",
    )

    print(f"Wrote synthetic fixtures under {FIX}")


if __name__ == "__main__":
    main()
