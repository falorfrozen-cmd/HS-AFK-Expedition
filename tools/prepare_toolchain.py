"""Place the YYToolkit / Aurie / FunctionWrapper headers under plugin_build/include.

They are upstream AGPL-3.0 headers and are never committed here. This script
copies them from a sibling ForgePact checkout that already fetched them
(ForgePact/tools/fetch_toolchain.py, pinned by SHA-256), or from a directory
named in HS_YYTK_SDK_ROOT (must contain include/YYToolkit and include/Aurie).
Nothing is downloaded by this script.
"""
from __future__ import annotations
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "plugin_build" / "include"
NEEDED = [
    "YYToolkit/YYTK_Shared.hpp",
    "YYToolkit/YYTK_Shared_Base.hpp",
    "YYToolkit/YYTK_Shared_Interface.hpp",
    "YYToolkit/YYTK_Shared_Types.hpp",
    "YYToolkit/YYTK_Shared_Types.cpp",
    "FunctionWrapper/FunctionWrapper.hpp",
    "Aurie/shared.hpp",
]


def candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("HS_YYTK_SDK_ROOT")
    if env:
        out.append(Path(env) / "include")
    out.append(ROOT.parent / "ForgePact" / "plugin_build" / "include")          # hub checkout sibling
    out.append(ROOT.parent.parent / "ForgePact" / "plugin_build" / "include")   # source/ForgePact next to the hub
    return out


def complete(base: Path) -> bool:
    return all((base / n).is_file() for n in NEEDED)


def main() -> int:
    if complete(DEST):
        print(f"toolchain headers already present: {DEST}")
        return 0
    for c in candidates():
        if complete(c):
            for n in NEEDED:
                src = c / n
                dst = DEST / n
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            print(f"copied {len(NEEDED)} header files from {c}")
            return 0
    print("ERROR: no complete YYToolkit header set found. Either set HS_YYTK_SDK_ROOT, or run\n"
          "  py ForgePact/tools/fetch_toolchain.py\n"
          "in a ForgePact checkout next to this repository and re-run.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
