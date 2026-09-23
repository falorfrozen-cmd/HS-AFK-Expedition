r"""Summarise a capture session: kills per monster, packets, argument kinds, exp.

    py tools/inspect_capture.py            # latest session under %LOCALAPPDATA%\Hero_Siege\afk
    py tools/inspect_capture.py <session.ndjson>
"""
from __future__ import annotations
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Hero_Siege" / "afk"


def latest_session() -> Path | None:
    d = ROOT / "sessions"
    if not d.is_dir():
        return None
    files = sorted(d.glob("capture_*.ndjson"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main(argv: list[str]) -> int:
    sess = Path(argv[1]) if len(argv) > 1 else latest_session()
    if not sess or not sess.is_file():
        print("no session file")
        return 1
    kills = []
    for line in sess.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "kill":
            kills.append(rec)
    print(f"session: {sess.name}  kills: {len(kills)}")
    by_monster = collections.Counter((k.get("monster_key") or "?", k.get("rank")) for k in kills)
    for (m, r), n in by_monster.most_common():
        print(f"  {m:32s} rank {r}  x{n}")
    rooms = collections.Counter(k.get("room") for k in kills)
    print("rooms:", dict(rooms))
    exps = [k.get("exp") for k in kills if isinstance(k.get("exp"), (int, float))]
    if exps:
        print(f"exp resolved on {len(exps)}/{len(kills)} kills, min {min(exps):.0f} max {max(exps):.0f}")
    hashes = collections.Counter(k.get("packet") for k in kills)
    print(f"distinct packets referenced: {len(hashes)}")
    pdir = ROOT / "packets"
    for h, n in hashes.most_common(8):
        p = pdir / f"{h}.json"
        if not p.is_file():
            print(f"  {h[:12]}  x{n}  (file missing)")
            continue
        pk = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        args = pk.get("args", [])
        kinds = []
        for a in args:
            if isinstance(a, dict) and "_kind" in a:
                kinds.append("<" + a["_kind"] + ">")
            elif isinstance(a, list):
                kinds.append(f"array[{len(a)}]")
            else:
                kinds.append(type(a).__name__)
        snap = pk.get("self_snapshot", {})
        print(f"  {h[:12]}  x{n}  {pk.get('monster_key')} r{pk.get('rank')} obj={pk.get('self_object')} "
              f"argc={pk.get('argc')} args={kinds} vars={len(snap)} exp={pk.get('exp_reward')} size={p.stat().st_size}")
        interesting = {k: snap[k] for k in ("dList", "affixList", "enemyRarity", "nameKey", "level", "isBoss") if k in snap}
        print(f"      {interesting}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
