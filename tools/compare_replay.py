r"""Compare item distributions between attribution contexts in a capture session.

    py tools/compare_replay.py                 # latest session
    py tools/compare_replay.py <session.ndjson> [ctxA] [ctxB]

Contexts written by the plugin's CreateItemNew hook: live (real kills while
capture is on), replay (ghost replay of a packet), replaylive (drop routine run
on a live instance). Prints items per drop call and the per-type / per-rarity
shares of each context, plus a chi-square statistic on the type distribution
(no SciPy needed: the critical value table is embedded for df up to 30).
"""
from __future__ import annotations
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Hero_Siege" / "afk"
CHI2_95 = {1: 3.84, 2: 5.99, 3: 7.81, 4: 9.49, 5: 11.07, 6: 12.59, 7: 14.07, 8: 15.51, 9: 16.92, 10: 18.31,
           11: 19.68, 12: 21.03, 13: 22.36, 14: 23.68, 15: 25.00, 16: 26.30, 17: 27.59, 18: 28.87, 19: 30.14,
           20: 31.41, 25: 37.65, 30: 43.77}


def latest_session() -> Path | None:
    d = ROOT / "sessions"
    files = sorted(d.glob("capture_*.ndjson"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    return files[-1] if files else None


def load(path: Path):
    recs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return recs


def chi2(a: collections.Counter, b: collections.Counter):
    keys = sorted(set(a) | set(b), key=str)
    na, nb = sum(a.values()), sum(b.values())
    if na == 0 or nb == 0:
        return None, 0
    stat = 0.0
    df = 0
    for k in keys:
        oa, ob = a.get(k, 0), b.get(k, 0)
        tot = oa + ob
        if tot == 0:
            continue
        ea, eb = tot * na / (na + nb), tot * nb / (na + nb)
        if ea < 1 or eb < 1:
            continue          # too sparse for the approximation; skip the cell
        stat += (oa - ea) ** 2 / ea + (ob - eb) ** 2 / eb
        df += 1
    return stat, max(df - 1, 0)


def main(argv: list[str]) -> int:
    sess = Path(argv[1]) if len(argv) > 1 and argv[1].endswith(".ndjson") else latest_session()
    if not sess:
        print("no session")
        return 1
    ctxA = argv[2] if len(argv) > 2 else "replaylive"
    ctxB = argv[3] if len(argv) > 3 else "replay"
    recs = load(sess)
    items = [r for r in recs if r.get("kind") == "item"]
    print(f"session {sess.name}: {len(items)} items logged")
    by_ctx = collections.defaultdict(list)
    for it in items:
        by_ctx[it.get("ctx")].append(it)
    for ctx, lst in by_ctx.items():
        types = collections.Counter(f"{i.get('type')}:{i.get('tname')}" for i in lst)
        rar = collections.Counter(i.get("rarity") for i in lst)
        print(f"\n[{ctx}] items={len(lst)}")
        print("  by type  :", dict(types.most_common(12)))
        print("  by rarity:", dict(sorted(rar.items(), key=lambda kv: str(kv[0]))))
    A = collections.Counter(f"{i.get('type')}" for i in by_ctx.get(ctxA, []))
    B = collections.Counter(f"{i.get('type')}" for i in by_ctx.get(ctxB, []))
    stat, df = chi2(A, B)
    if stat is None:
        print(f"\nchi-square: one of the contexts ({ctxA}/{ctxB}) has no items")
        return 0
    crit = CHI2_95.get(df) or CHI2_95[min(CHI2_95, key=lambda d: abs(d - df))]
    verdict = "consistent (no significant difference at 95%)" if stat <= crit else "DIFFERENT at 95%"
    print(f"\nchi-square on item type, {ctxA} vs {ctxB}: stat={stat:.2f} df={df} crit95={crit} -> {verdict}")
    print("note: item COUNTS per drop call are compared by the caller (items / calls); this compares composition")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
