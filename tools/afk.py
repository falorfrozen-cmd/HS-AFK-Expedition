#!/usr/bin/env python3
"""HS AFK Expedition command line: profiles, plans, the offline clock, running
an expedition through the in-game plugin and handing the spool to the Item
Editor's Infinite Vault. Standard library only.

    afk.py profile build [--session PATH|latest] [--room ROOM] [--active]
    afk.py profile list
    afk.py plan --hours H [--zone ROOM[=WEIGHT] ...] [--id ID] [--per-frame N]
    afk.py preview PLAN
    afk.py start PLAN            # arm the offline clock (then quit the game)
    afk.py claim [--dry-run]     # after time passed: scale, replay, ingest
    afk.py run PLAN [--no-ingest]
    afk.py status
    afk.py ingest SPOOL [--keep-filtered]
    afk.py capture on|off|stats | capture auto on|off   # passive calibration while you play
    afk.py verify [--runs 3]     # live sample vs same-size replays, statistics only

Data lives in %LOCALAPPDATA%\\Hero_Siege\\afk (packets, sessions, spool,
profiles, plans, state.json). The plugin's IPC is <game bin>\\afk_ipc; the game
folder is taken from --game-bin, the HS_GAME_BIN environment variable or the
value remembered in afk\\config.json.
"""
from __future__ import annotations

import argparse
import reward_modifiers
import hashlib
import json
import math
import os
import sys
import time
import calibration
import recovery
from functools import wraps
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Hero_Siege" / "afk"
PACKETS, SESSIONS, SPOOL = DATA / "packets", DATA / "sessions", DATA / "spool"
PROFILES, PLANS = DATA / "profiles", DATA / "plans"
STATE, CONFIG = DATA / "state.json", DATA / "config.json"
MAX_HOURS = 8.0
ACTIVE_WINDOW_S = 30
MIN_COVERAGE = 0.95


def character_key(stamp) -> tuple:
    """Version 1 stamps used a name search and may identify the wrong save."""
    if (not isinstance(stamp, dict) or stamp.get('identity_version') != 2
            or not isinstance(stamp.get('name'), str) or not stamp['name']
            or type(stamp.get('class')) is not int or stamp['class'] < 0
            or type(stamp.get('slot')) is not int or not 0 <= stamp['slot'] < 2**31):
        sys.exit('character identity is missing or legacy; load the character with the updated plugin and capture a new calibration')
    return stamp['slot'], stamp['name'], stamp['class']


def profile_key(profile) -> str:
    context = [profile['room'], character_key(profile.get('character')),
               profile.get('game_build'), profile.get('forgepact'), profile.get('rate_basis'), (profile.get('farm_context') or {}).get('hash')]
    digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:16]
    return profile['room'] + '--' + digest


# ------------------------------------------------------------------ helpers
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def require_reward_plan(plan):
    """A combat workbench result is not an authorized native-replay plan."""
    if isinstance(plan, dict) and (str(plan.get('document_type', '')).startswith('afk.combat')
                                  or plan.get('reward_eligible') is False):
        sys.exit('combat model research output is not a reward plan; native character/activity adapters and live validation are still required')


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(obj, stream, indent=1, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def serialized_rewards(function):
    @wraps(function)
    def call(*args, **kwargs):
        with recovery.data_lock(DATA):
            return function(*args, **kwargs)
    return call


def read_ndjson(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def largest_remainder(total: int, weights: list[float]) -> list[int]:
    """Integer counts summing to `total`, proportional to `weights`."""
    s = sum(weights)
    if total <= 0 or s <= 0:
        return [0] * len(weights)
    raw = [total * w / s for w in weights]
    counts = [int(math.floor(r)) for r in raw]
    short = total - sum(counts)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[:short]:
        counts[i] += 1
    return counts


def game_bin(args) -> Path:
    cfg = read_json(CONFIG, {}) or {}
    cand = getattr(args, "game_bin", None) or os.environ.get("HS_GAME_BIN") or cfg.get("game_bin")
    if not cand:
        sys.exit("game folder unknown: pass --game-bin <...\\HeroSiege\\bin> once (it is remembered)")
    p = Path(cand)
    if not (p / "Hero_Siege.exe").exists():
        sys.exit(f"not the game's bin folder (no Hero_Siege.exe): {p}")
    if cfg.get("game_bin") != str(p):
        cfg["game_bin"] = str(p)
        write_json(CONFIG, cfg)
    return p


def packet_index(hashes=None) -> dict[str, dict]:
    """hash -> {monster_key, self_object, rank, room, exp, deep}"""
    idx = {}
    files = PACKETS.glob('*.json') if hashes is None else (PACKETS/(h+'.json') for h in hashes if isinstance(h,str) and len(h)==64 and all(c in '0123456789abcdef' for c in h))
    for f in files:
        d = read_json(f)
        if not isinstance(d, dict):
            continue
        h = d.get("packet_hash") or f.stem
        prot = d.get("protected") if isinstance(d.get("protected"), dict) else {}
        # The death event hands the monster's protected `killExperience` to the
        # experience routine (STATIC + MEASURED 2026-09-17); the packet's
        # exp_reward is the base `experience` and is up to 3x smaller.
        exp = prot.get("killExperience")
        if not isinstance(exp, (int, float)):
            exp = (d.get("exp_reward") or {}).get("resolved")
        # A monster packet must carry the protected values the drop routine
        # reads (MEASURED 2026-09-18: 20 of 317 packets lacked them because the
        # capture's handle window was too narrow; replaying such a packet hands
        # the ghost raw handle ids, which read as some other value or throw).
        # Breakables too: a chest's snapshot carried `dSlots` as a raw handle
        # id and one replay of it produced 4, then 0, then 6 650 items as the
        # id drifted onto other counters (MEASURED 2026-09-18). Any drop
        # variable that looks like a handle in the snapshot must be resolved.
        needed = ("dSlots", "dCommonChance", "dCommonDropMult", "dSatanicDropMult", "killExperience", "extraMagicFind", "lootAmount")
        snap = d.get("self_snapshot") if isinstance(d.get("self_snapshot"), dict) else {}
        def looks_like_handle(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1000 and float(v).is_integer()
        complete = all((k in prot) or not looks_like_handle(snap.get(k)) for k in needed)
        if d.get("monster_key"):
            complete = complete and all(k in prot for k in needed if k != "lootAmount")
        idx[h] = {"monster_key": d.get("monster_key") or "", "self_object": d.get("self_object") or "",
                  "rank": d.get("rank"), "room": d.get("room"), "exp": exp if isinstance(exp, (int, float)) else None,
                  # breakables (chests, barrels) legitimately have no protected values: {} is still the deep format
                  "deep": isinstance(d.get("protected"), dict), "complete": complete, "build": d.get("game_build_id")}
    return idx


def latest_session_with_kills() -> Path | None:
    """Newest capture file that holds kill/break lines (a replay run also opens
    a session file, with item lines only)."""
    for f in sorted(SESSIONS.glob("capture_*.ndjson"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    if '"kind": "kill"' in line or '"kind": "break"' in line or '"kind":"kill"' in line or '"kind":"break"' in line:
                        return f
        except OSError:
            continue
    return None


# ------------------------------------------------------------------ ForgePact
FORGEPACT_FILE = DATA.parent / "forgepact.json"
# settings that change how FAST you kill (baked into a profile's rate) versus
# settings the game applies live while replaying (drop chances, magic find);
# experience multipliers are baked into each packet's kill experience at spawn
RATE_KEYS = ("density", "damage", "attackspeed", "castrate", "movespeed", "rarity", "spawners", "enemy_speed")


def forgepact_summary(cfg):
    """Normalised view of the panel's settings: only what is switched on."""
    if not isinstance(cfg, dict):
        return {}
    out = {}
    d = float(cfg.get("density", 1) or 1) if cfg.get("density_on") else 1.0
    if d > 1.0:
        out["density"] = d
    stats = cfg.get("stats") or {}
    for k in ("exp", "magicfind", "movespeed"):
        v = float(stats.get(k, 1) or 1)
        if v > 1.0:
            out[k] = v
    for k, v in (cfg.get("percent_stats") or {}).items():
        if float(v or 0) > 0:
            out[k] = float(v)
    rare, ancient = float(cfg.get("rarity_rare", 0) or 0), float(cfg.get("rarity_ancient", 0) or 0)
    if rare > 0 or ancient > 0:
        out["rarity"] = [rare, ancient]
    if float(cfg.get("angelic_items", 1) or 1) > 1:
        out["angelic_items"] = float(cfg["angelic_items"])
    if float(cfg.get("enemy_speed", 0) or 0) > 0:
        out["enemy_speed"] = float(cfg["enemy_speed"])
    for group in ("drops", "keys", "spawners"):
        on = {k: float(v) for k, v in (cfg.get(group) or {}).items() if float(v or 1) > 1}
        if on:
            out[group] = on
    for flag in ("headhunter", "tyrant", "beacon", "mod_filter_max_relics"):
        if cfg.get(flag):
            out[flag] = True
    return out


def forgepact_current() -> dict:
    return forgepact_summary(read_json(FORGEPACT_FILE, {}))


def forgepact_text(summary: dict) -> str:
    if not summary:
        return "vanilla (nothing switched on)"
    parts = []
    for k, v in summary.items():
        if isinstance(v, dict):
            parts.append(k + "{" + ", ".join(f"{a} x{b:g}" for a, b in v.items()) + "}")
        elif isinstance(v, list):
            parts.append(k + " " + "/".join(f"{x:g}" for x in v))
        elif v is True:
            parts.append(k)
        elif k in ("density", "exp", "magicfind", "movespeed", "angelic_items"):
            parts.append(f"{k} x{v:g}")
        else:
            parts.append(f"{k} +{v:g}%")
    return ", ".join(parts)


def forgepact_compare(calibrated: dict, current: dict):
    """(rate-affecting differences, reward-affecting differences)"""
    rate, reward = [], []
    for k in sorted(set(calibrated) | set(current)):
        a, b = calibrated.get(k), current.get(k)
        if a == b:
            continue
        was = forgepact_text({k: a}) if a is not None else "off"
        now = forgepact_text({k: b}) if b is not None else "off"
        (rate if k in RATE_KEYS else reward).append(f"{k}: calibrated {was} -> now {now}")
    return rate, reward


# ------------------------------------------------------------------ profiles
def build_profile(session: Path, room_filter: str | None, active: bool) -> list[dict]:
    recs = read_ndjson(session)
    if any(r.get('kind') == 'context_invalid' for r in recs):
        sys.exit('character/settings changed during this capture; use a new stable calibration session')
    start = next((r for r in recs if r.get("kind") == "session_start"), {})
    starts = [r for r in recs if r.get('kind') == 'session_start']
    contexts = {json.dumps([r.get('character'), r.get('build'), r.get('forgepact')], sort_keys=True) for r in starts}
    if len(contexts) > 1:
        sys.exit('capture contains multiple character/build/settings contexts; capture a new session')
    events = [r for r in recs if r.get("kind") in ("kill", "break") and r.get("packet")]
    pidx = packet_index({r['packet'] for r in events})
    by_room: dict[str, list[dict]] = defaultdict(list)
    # Sum separate visits; time spent killing in B cannot slow A's rate.
    visits = defaultdict(list)
    previous_room = None
    for r in events:
        room = r.get('room') or '?'
        by_room[room].append(r)
        if room != previous_room:
            visits[room].append([])
        visits[room][-1].append(parse_iso(r['t']))
        previous_room = room
    profiles = []
    for room, evs in by_room.items():
        if room_filter and room != room_filter:
            continue
        times = sorted(parse_iso(r["t"]) for r in evs)
        wall_s = sum(max(1.0, (max(v) - min(v)).total_seconds() + 1.0) for v in visits[room])
        buckets = {int((t - times[0]).total_seconds() // ACTIVE_WINDOW_S) for t in times}
        active_s = float(len(buckets) * ACTIVE_WINDOW_S)
        clock = [r for r in recs if r.get('kind') == 'farm_clock' and r.get('room') == room]
        clock_s = sum(float(r.get('seconds', 0)) for r in clock)
        if start.get('farm_context') and not active:
            if not clock_s > 0:
                sys.exit('capture has no valid farm clock; capture a new session')
            basis_s = clock_s
        else:
            basis_s = active_s if active else wall_s
        kills = [r for r in evs if r["kind"] == "kill"]
        breaks = [r for r in evs if r["kind"] == "break"]
        usable = [r for r in evs if pidx.get(r["packet"], {}).get("deep")]
        dropped = len(evs) - len(usable)
        # Never turn a missing snapshot into a different monster or chest.
        complete = [r for r in usable if pidx[r['packet']].get('complete')
                    and pidx[r['packet']].get('build') == start.get('build')
                    and pidx[r['packet']].get('room') == room]
        reassigned, orphaned = 0, len(usable) - len(complete)
        usable = complete
        counts = Counter(r["packet"] for r in usable)
        packets = []
        exp_total = 0.0
        for h, c in counts.most_common():
            meta = pidx.get(h, {})
            is_kill = any(r["kind"] == "kill" for r in usable if r["packet"] == h)
            # experience varies from kill to kill even for the same drop
            # situation (affix count, level): use the mean of the kills that
            # mapped to this packet (kill lines carry kill_exp), the packet's
            # own value only as a fallback
            kexps = [float(r["kill_exp"]) for r in usable if r["packet"] == h and isinstance(r.get("kill_exp"), (int, float)) and r["kill_exp"] > 0]
            exp = (sum(kexps) / len(kexps)) if (is_kill and kexps) else (float(meta.get("exp") or 0) if is_kill else 0.0)
            exp_total += c * exp
            native = [r.get('native_kill_exp') for r in usable if r['packet'] == h and r['kind'] == 'kill']
            valid_native = bool(native) and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in native)
            native_exp = sum(native) / len(native) if valid_native else (0.0 if not is_kill else None)
            packets.append({"hash": h, "monster_key": meta.get("monster_key", ""), "object": meta.get("self_object", ""),
                            "rank": meta.get("rank"), "kind": "kill" if is_kill else "break",
                            "count": c, "weight": c / len(usable), "exp": exp, "native_exp": native_exp})
        profiles.append({
            "room": room, "built_at": iso(now_utc()), "session": str(session), "game_build": start.get("build"),
            "plugin": start.get("plugin"), "character": start.get("character"), "rate_basis": "farm-clock" if start.get("farm_context") and not active else ("active" if active else "wall"),
            "farm_context": start.get("farm_context"),
            "reward_baseline": dict(start.get('reward_baseline') or {}, complete=bool((start.get('reward_baseline') or {}).get('available') and all(p['native_exp'] is not None for p in packets))),
            "forgepact": forgepact_summary(start.get("forgepact")) if start.get("forgepact") is not None else None,
            "wall_seconds": wall_s, "active_seconds": active_s, "basis_seconds": basis_s,
            "kills": len(kills), "breaks": len(breaks), "events_usable": len(usable), "events_dropped_old_format": dropped,
            "coverage": len(usable) / len(evs), "profile_version": 3 if start.get("farm_context") else 2,
            "events_reassigned_incomplete": reassigned, "events_orphaned_incomplete": orphaned,
            "kills_per_min": 60.0 * sum(r['kind'] == 'kill' for r in usable) / basis_s,
            "breaks_per_min": 60.0 * sum(r['kind'] == 'break' for r in usable) / basis_s,
            "exp_per_min": 60.0 * exp_total / basis_s, "exp_per_kill": (exp_total / len(kills)) if kills else 0.0,
            "packets": packets,
            "quality": calibration.quality(recs, room, set(counts)),
            "session_sha256": hashlib.sha256(session.read_bytes()).hexdigest() if session.is_file() else None,
        })
    return profiles


def cmd_profile(args) -> None:
    PROFILES.mkdir(parents=True, exist_ok=True)
    if args.action == "list":
        rows = sorted(PROFILES.glob("*.json"))
        if not rows:
            print("no profiles yet: play a calibration run with `afk capture on`, then `afk.py profile build`")
        for f in rows:
            p = read_json(f) or {}
            print(f"{p.get('room','?'):14s} kills/min {p.get('kills_per_min',0):6.2f}  exp/min {p.get('exp_per_min',0):10.0f}  "
                  f"packets {len(p.get('packets',[])):3d}  basis {p.get('rate_basis')} {p.get('basis_seconds',0):.0f}s  ({f.name})")
        return
    if args.session in (None, "latest"):
        session = latest_session_with_kills()
        if session is None:
            sys.exit("no capture session with kills found (play with capture on first)")
    else:
        session = Path(args.session)
    profiles = build_profile(session, args.room, args.active)
    if not profiles:
        sys.exit("no kills/breaks in that session (is capture on while you play?)")
    for p in profiles:
        if p["kills"] + p["breaks"] < args.min_events or p["basis_seconds"] < args.min_seconds:
            print(f"skip {p['room']}: {p['kills'] + p['breaks']} events over {p['basis_seconds']:.0f}s "
                  f"(need {args.min_events} events and {args.min_seconds}s; a burst is not a rate)")
            continue
        p['profile_id'] = profile_key(p)
        out = PROFILES / f"{p['profile_id']}.json"
        write_json(out, p)
        # Backward-compatible latest selection; context-specific copies remain available.
        write_json(PROFILES / f"{p['room']}.json", p)
        print(f"coverage {p['room']}: {p['events_usable']}/{p['kills'] + p['breaks']} ({p['coverage']:.1%})")
        if p["basis_seconds"] < 900:
            print(f"note {p['room']}: only {p['basis_seconds'] / 60:.1f} min of play; monster/event sources (such as treasure goblins, "
                  f"bosses) may be absent. Native item drops are rolled afresh; unseen item rarity is not excluded. A longer representative sample is recommended.")
        print(f"{p['room']}: {p['kills']} kills, {p['breaks']} breaks over {p['basis_seconds']:.0f}s ({p['rate_basis']}) -> "
              f"{p['kills_per_min']:.2f} kills/min, {p['exp_per_min']:.0f} exp/min, {len(p['packets'])} packets"
              + (f", {p['events_dropped_old_format']} old-format events ignored" if p['events_dropped_old_format'] else "")
              + (f", {p['events_reassigned_incomplete']} kills with incomplete packets counted under siblings" if p.get('events_reassigned_incomplete') else "")
              + (f", {p['events_orphaned_incomplete']} kills with incomplete packets left out" if p.get('events_orphaned_incomplete') else "")
              + f" -> {out}")


# ------------------------------------------------------------------ rates (items / gold per call, from earlier spools)
def learned_rates(context: dict | None = None) -> dict:
    items_by_packet: Counter = Counter()
    calls_by_packet: Counter = Counter()
    gold, calls = 0.0, 0
    for f in SPOOL.glob("*.ndjson"):
        recs = read_ndjson(f)
        plan = read_json(PLANS / f"{f.stem}.json") or read_json(PLANS / f"{f.stem}.claim.json")
        if not plan:
            continue        # research spools (no plan) carry no usable rates
        if context and any(plan.get(k) != context.get(k) for k in ('character', 'game_build', 'forgepact')):
            continue
        # the plugin appends to an existing spool: keep only the last complete
        # run (records after the previous "summary" line; a "partial" line is a
        # resumed run and belongs to the same expedition)
        cut = max([i for i, r in enumerate(recs[:-1]) if r.get("kind") == "summary"], default=-1)
        seg = recs[cut + 1:]
        summ = [r for r in seg if r.get("kind") == "summary"]
        if not summ:
            continue
        if context and forgepact_summary(summ[-1].get('forgepact')) != context.get('forgepact'):
            continue
        for r in seg:
            if r.get("kind") == "item" and r.get("packet"):
                items_by_packet[r["packet"]] += 1
        for p in plan.get("packets", []):
            calls_by_packet[p["hash"]] += int(p.get("count", 0))
        gold += float(summ[-1].get("gold", 0))
        calls += int(summ[-1].get("calls", 0))
    return {"items_per_call": {h: items_by_packet[h] / calls_by_packet[h] for h in calls_by_packet if calls_by_packet[h] > 0},
            "gold_per_call": (gold / calls) if calls else None}


# ------------------------------------------------------------------ plans
def rebuild_preview(plan: dict) -> None:
    """One source of truth: the integer calls that will actually be replayed."""
    pk = plan['packets']
    calls = sum(p['count'] for p in pk)
    rates = plan.get('estimate_rates', {})
    items = rates.get('items_per_call', {})
    gold = rates.get('gold_per_call')
    for z in plan.get('zones', []):
        for kind in ('kill', 'break'):
            z[kind + 's'] = sum(p['count'] for p in pk if p.get('room') == z['room']
                               and p.get('kind') == kind and not p.get('extra'))
    for extra in plan.get('extras', []):
        extra['count'] = sum(p['count'] for p in pk if p.get('extra') and p['hash'] == extra['packet'])
    plan['preview'] = dict(
        calls=calls, kills=sum(p['count'] for p in pk if p.get('kind') == 'kill'),
        breaks=sum(p['count'] for p in pk if p.get('kind') == 'break'),
        exp=int(sum(p['count'] * float(p.get('exp') or 0) for p in pk)) if plan.get('exp', True) else 0,
        items_estimate=round(sum(p['count'] * items[p['hash']] for p in pk)) if pk and all(p['hash'] in items for p in pk) else None,
        gold_estimate=round(calls * gold) if gold is not None else None,
        seconds_to_replay=round(calls / max(1, plan['per_frame']) / 60.0, 1))


def make_plan(hours: float, zones: list[tuple[str, float]], exp_id: str, per_frame: int, gold: str,
              extras: list[tuple[str, float]] | None = None, *, profile_overrides: dict | None = None) -> dict:
    """extras: (packet hash prefix, kills per hour) added on top of the zone
    mix, e.g. a boss or treasure-goblin packet you want at a chosen rate
    rather than at the rate the calibration happened to show."""
    if not math.isfinite(hours) or hours <= 0:
        sys.exit("hours must be > 0")
    hours = min(float(hours), MAX_HOURS)
    if not zones or any(not math.isfinite(w) or w <= 0 for _, w in zones):
        sys.exit('zone weights must be positive finite numbers')
    if len({room for room, _ in zones}) != len(zones):
        sys.exit('duplicate zone/profile selection')
    profs = {}
    for room, _ in zones:
        p = (profile_overrides or {}).get(room) or read_json(PROFILES / f"{room}.json")
        if not p:
            sys.exit(f"no profile for {room} (afk.py profile list)")
        character_key(p.get('character'))
        if not p.get('game_build') or not isinstance(p.get('forgepact'), dict):
            sys.exit(f'{room}: missing build/settings context; recalibrate')
        coverage = p.get('coverage', p.get('events_usable', 0) / max(1, p.get('kills', 0) + p.get('breaks', 0)))
        if coverage < MIN_COVERAGE:
            sys.exit(f'{room}: only {coverage:.1%} packet coverage (need {MIN_COVERAGE:.0%}); capture a new calibration')
        profs[room] = p
    first = next(iter(profs.values()))
    for p in profs.values():
        if (character_key(p['character']) != character_key(first['character'])
                or any(p.get(k) != first.get(k) for k in ('game_build', 'forgepact', 'rate_basis'))):
            sys.exit('profiles have incompatible character/build/settings/rate contexts; recalibrate under one context')
    if len(profs) != 1:
        sys.exit('multi-zone replay has no verified room context; create one expedition per zone')
    wsum = sum(w for _, w in zones)
    rates = learned_rates(first)
    plan_zones, packets, preview_exp, preview_items = [], [], 0.0, 0.0
    total_kills = total_breaks = 0
    for room, w in zones:
        p = profs[room]
        minutes = hours * 60.0 * w / wsum
        kills = int(round(sum(q['count'] for q in p['packets'] if q['kind'] == 'kill') * minutes * 60 / p['basis_seconds']))
        breaks = int(round(sum(q['count'] for q in p['packets'] if q['kind'] == 'break') * minutes * 60 / p['basis_seconds']))
        room = p['room']
        kill_pk = [q for q in p["packets"] if q["kind"] == "kill"]
        break_pk = [q for q in p["packets"] if q["kind"] == "break"]
        for group, n in ((kill_pk, kills), (break_pk, breaks)):
            counts = largest_remainder(n, [q["weight"] for q in group]) if group else []
            for q, c in zip(group, counts):
                if c <= 0:
                    continue
                packets.append({"hash": q["hash"], "count": c, "monster_key": q["monster_key"], "room": room,
                                "kind": q["kind"], "exp": q["exp"]})
                preview_exp += c * float(q["exp"] or 0)
                ipc = rates["items_per_call"].get(q["hash"])
                if ipc is not None:
                    preview_items += c * ipc
        plan_zones.append({"room": room, "weight": w, "minutes": minutes, "kills": kills, "breaks": breaks})
        total_kills += kills
        total_breaks += breaks
    pidx = packet_index() if extras else {}
    extra_rows = []
    for prefix, per_hour in extras or []:
        matches = [h for h in pidx if h.startswith(prefix)]
        if len(matches) != 1:
            sys.exit(f"--extra {prefix}: {'no' if not matches else 'several'} packet(s) match that prefix")
        h = matches[0]; meta = pidx[h]
        if not meta.get("deep") or not meta.get("complete"):
            sys.exit(f"--extra {prefix}: that packet is incomplete or old; capture it again")
        if meta.get('build') != first['game_build'] or meta.get('room') != first['room']:
            sys.exit(f'--extra {prefix}: packet build/room differs from the calibration')
        if not math.isfinite(per_hour) or per_hour < 0:
            sys.exit('--extra rate must be a nonnegative finite number')
        c = int(round(per_hour * hours))
        if c <= 0:
            continue
        packets.append({"hash": h, "count": c, "monster_key": meta.get("monster_key", ""), "room": meta.get("room"),
                        "kind": "kill" if meta.get("monster_key") else "break", "exp": float(meta.get("exp") or 0), "extra": True})
        preview_exp += c * float(meta.get("exp") or 0)
        total_kills += c
        extra_rows.append({"packet": h, "monster_key": meta.get("monster_key", ""), "per_hour": per_hour, "count": c})
    calls = sum(p["count"] for p in packets)
    plan = {
        "expedition_id": exp_id, "created": iso(now_utc()), "hours": hours, "zones": plan_zones, "extras": extra_rows,
        "rate_source": "measured-profile",
        "character": first['character'], "forgepact": first['forgepact'], "game_build": first['game_build'],
        "estimate_rates": rates, "coverage": first.get('coverage'), "farm_context": first.get("farm_context"),
        "packets": packets, "exp": True, "gold": gold, "per_frame": per_frame, "frame_budget_ms": 10,
        "preview": {"kills": total_kills, "breaks": total_breaks, "calls": calls, "exp": int(preview_exp),
                    "items_estimate": (int(round(preview_items)) if preview_items else None),
                    "gold_estimate": (int(round(calls * rates["gold_per_call"])) if rates["gold_per_call"] else None),
                    "seconds_to_replay": round(calls / max(1, per_frame) / 60.0, 1)},
    }
    rebuild_preview(plan)
    return plan


def scale_plan(plan: dict, factor: float, new_id: str) -> dict:
    factor = max(0.0, min(1.0, factor))
    out = json.loads(json.dumps(plan))
    out["expedition_id"] = new_id
    out["scaled_from"] = plan["expedition_id"]
    out["scale"] = factor
    counts = [p['count'] for p in out['packets']]
    allocated = largest_remainder(round(sum(counts) * factor), counts)
    for p, count in zip(out['packets'], allocated):
        p['count'] = count
    out["packets"] = [p for p in out["packets"] if p["count"] > 0]
    for z in out["zones"]:
        z["minutes"] *= factor
    rebuild_preview(out)
    return out


def print_preview(plan: dict) -> None:
    pv = plan["preview"]
    print(f"expedition {plan['expedition_id']}: {plan['hours']:.2f} h" + (f" (scaled x{plan['scale']:.3f})" if "scale" in plan else ""))
    if plan.get("forgepact") is not None:
        print(f"  ForgePact at calibration: {forgepact_text(plan['forgepact'])}")
    for z in plan["zones"]:
        prof = read_json(PROFILES / f"{z['room']}.json") or {}
        if prof and prof.get("basis_seconds", 0) < 900:
            print(f"  note: {z['room']} has a {prof['basis_seconds'] / 60:.1f} min rate sample; combat-rate stability has not been verified")
    for z in plan["zones"]:
        print(f"  {z['room']:14s} {z['minutes']:6.1f} min  kills {z['kills']:6d}  breaks {z['breaks']:5d}")
    for e in plan.get("extras", []):
        print(f"  extra {e['monster_key'] or e['packet'][:12]:22s} {e['per_hour']:.1f}/h -> {e['count']} kills")
    print(f"  total: {pv['kills']} kills, {pv['breaks']} breaks, {pv['calls']} drop calls, exp {pv['exp']:,}"
          + (f", ~{pv['items_estimate']} items" if pv.get("items_estimate") is not None else ", items: no rate learned yet")
          + (f", ~{pv['gold_estimate']} gold" if pv.get("gold_estimate") is not None else ", gold: no rate learned yet")
          + "; delivery time depends on game performance and reward-call cost")


def cmd_plan(args) -> None:
    zones = []
    for z in args.zone or []:
        room, _, w = z.partition("=")
        zones.append((room, float(w) if w else 1.0))
    if not zones:
        sys.exit("pick at least one --zone ROOM[=WEIGHT] (afk.py profile list)")
    exp_id = args.id or "exp_" + now_utc().strftime("%Y%m%d_%H%M%S")
    extras = []
    for e in args.extra or []:
        prefix, _, rate = e.partition("=")
        if not rate:
            sys.exit("--extra needs PACKET=PER_HOUR, e.g. --extra 3071a8f7=4")
        extras.append((prefix, float(rate)))
    plan = make_plan(args.hours, zones, exp_id, args.per_frame, "none" if args.no_gold else "pickup", extras)
    if extras:
        sys.exit('Independent rewards require calibrated packets; --extra has no native XP baseline')
    try:
        profile=read_json(PROFILES / f"{zones[0][0]}.json")
        reward_modifiers.apply_to_plan(plan,profile,reward_modifiers.load(DATA/'reward-modifiers.json'))
        rebuild_preview(plan)
    except (ValueError, KeyError) as error:
        sys.exit(str(error))
    out = PLANS / f"{exp_id}.json"
    write_json(out, plan)
    print_preview(plan)
    print(f"-> {out}")


def cmd_preview(args) -> None:
    plan = read_json(Path(args.plan))
    if not plan:
        sys.exit("cannot read plan")
    require_reward_plan(plan)
    print_preview(plan)


# ------------------------------------------------------------------ IPC with the plugin
class Ipc:
    def __init__(self, bin_dir: Path):
        self.dir = bin_dir / "afk_ipc"
        self.dir.mkdir(exist_ok=True)
        self.cmd, self.out = self.dir / "cmd.txt", self.dir / "out.txt"

    def alive(self) -> bool:
        return self.send("afk status", timeout=6.0) is not None

    DONE_MARK = "---- done ----"

    def send(self, line: str, timeout: float = 30.0) -> list[str] | None:
        # A single command channel is shared by the panel, CLI and test helper.
        # The OS releases this byte lock after a crash; never overwrite a pending file.
        import msvcrt
        deadline=time.monotonic()+timeout
        lock_path=self.dir/'writer.lock'
        with lock_path.open('a+b') as guard:
            if guard.tell()==0: guard.write(b'0');guard.flush()
            while True:
                try:
                    guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>=deadline:raise RuntimeError('another AFK command owns the IPC channel')
                    time.sleep(.05)
            try:
                while self.cmd.exists():
                    if time.monotonic()>=deadline:raise RuntimeError('an IPC command is still pending; it was not overwritten')
                    time.sleep(.05)
                return self._send_locked(line,timeout)
            finally:
                guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_UNLCK,1)

    def _send_locked(self, line: str, timeout: float = 30.0) -> list[str] | None:
        """One command; the reply is everything the plugin wrote before its
        end-of-batch marker. A quiet file is not enough: loading a plan's
        packets takes longer than one poll interval (MEASURED 2026-09-18, the
        claim was reported as "not started" while it was already running)."""
        # The completion marker can become readable just before the game closes
        # its output handle. Retry only sharing violations before publishing a
        # command; never resend a command whose execution may already have begun.
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.out.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (32, 33):
                    raise
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.05)
        self.cmd.write_text(line + "\n", encoding="utf-8")
        last = ""
        while time.monotonic() < deadline:
            time.sleep(0.25)
            try:
                last = self.out.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                continue
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (32, 33):
                    raise
                continue
            if self.DONE_MARK in last:
                break
        lines = [l for l in last.splitlines() if l.strip() and not l.startswith("----")]
        return lines if self.DONE_MARK in last else None


def run_plan(plan_path: Path, plan: dict, bin_dir: Path, ingest: bool, anywhere: bool = False, forgepact_ignore: bool = False) -> dict | None:
    require_reward_plan(plan)
    ipc = Ipc(bin_dir)
    if plan.get('farm_context'):
        import uuid
        token=uuid.uuid4().hex
        response=DATA/'models'/f'session-state-{token}.json'
        if ipc.send('afk session state '+token+' isolated',timeout=15) is None:
            sys.exit('fresh character context unavailable')
        try:
            state=read_json(response if response.exists() else DATA/'models/session-state.json',{}) or {}
        finally:
            response.unlink(missing_ok=True)
        if state.get('request_id')!=token or state.get('online') is not False:
            sys.exit('stale or online character context; claim refused')
        if not reward_modifiers.context_matches(plan['farm_context'], state.get('farm_context'), bool(plan.get('reward_modifiers'))):
            sys.exit('equipment/talents/level/difficulty/settings changed; recalibrate before claim')

    if not ipc.alive():
        sys.exit("the game (with the AFK plugin) is not running, or it is not answering on afk_ipc")
    # Names are not unique. Unknown/legacy identities must never enable replay.
    who = ipc.send("afk who") or []
    stamp = None
    for l in who:
        if l.startswith("who: "):
            try:
                stamp = json.loads(l[5:])
            except ValueError:
                stamp = None
    want = plan.get("character")
    if character_key(stamp) != character_key(want):
        sys.exit(f"the loaded character ({stamp.get('name')!r}, class {stamp.get('class')}) is not the one calibrated "
                 f"({want.get('name')!r}, class {want.get('class')}, slot {want.get('slot')}). Load that character or recalibrate.")
    status = ipc.send("afk status") or []
    build_line = next((l for l in status if "build id" in l), "")
    if not plan.get('game_build') or build_line.split(':', 1)[-1].strip() != plan['game_build']:
        sys.exit(f"the game build changed since the calibration ({plan['game_build']}); capture a new calibration run")
    # the drop routine reads the current room: replaying in town gave 8 % fewer
    # items than in the zone itself (MEASURED 2026-09-18), so stand in a
    # calibrated zone unless --anywhere says otherwise
    # ForgePact: its drop / magic-find hooks act on the replay like on a real
    # kill (MEASURED 2026-09-18), so whatever is on now applies; what it did to
    # the kill RATE is baked into the profile, so those must still match
    calibrated = plan.get("forgepact")
    current = forgepact_current()
    if calibrated is not None and not plan.get('reward_modifiers'):
        rate_diff, reward_diff = forgepact_compare(calibrated, current)
        if reward_diff:
            print("ForgePact reward settings changed since the calibration (the current ones apply to this replay):")
            for l in reward_diff:
                print("   " + l)
        if rate_diff:
            print("ForgePact settings that change your kill rate differ from the calibration:")
            for l in rate_diff:
                print("   " + l)
            if not forgepact_ignore:
                sys.exit("the profile's kills per minute were measured under other settings; set the panel back, "
                         "recalibrate, or pass --forgepact-ignore to replay anyway")
    print(f"ForgePact now: {forgepact_text(current)}")
    room_line = next((l for l in status if l.strip().startswith("room")), "")
    room = room_line.split(":", 1)[1].strip() if ":" in room_line else ""
    zones = [z["room"] for z in plan.get("zones", [])]
    if len(set(zones)) != 1 or not room:
        sys.exit('one calibrated zone and a readable current room are required')
    if calibrated is None:
        sys.exit('plan has no calibration settings; recalibrate')
    if zones and room and room not in zones and not anywhere:
        sys.exit(f"you are in {room}; stand in one of the plan's zones ({', '.join(zones)}) so the drops see the right map, "
                 f"or pass --anywhere to replay here anyway")
    if zones and room and room not in zones and anywhere:
        print(f"warning: replaying in {room}, not in {', '.join(zones)}; drops that depend on the map may differ")
    # Pass the explicit diagnostic override without changing the saved plan.
    reply = ipc.send(f"afk expedition start {plan_path}" + (' --anywhere' if anywhere else '')) or []
    print("\n".join(reply))
    if any('already running' in l for l in reply) and not any(f"already running ({plan['expedition_id']})" in l for l in reply):
        sys.exit('another expedition is running; wait for it or abort it first')
    already_done = any('already done' in l for l in reply)
    if not any("started" in l or "resumed" in l or "already running" in l for l in reply):
        if not already_done:
            sys.exit("the plugin did not start the expedition")
    progress = SESSIONS / f"{plan['expedition_id']}.progress.json"
    failure = SESSIONS / f"{plan['expedition_id']}.failure.json"
    t0 = time.time()
    last_print = 0.0
    while not already_done:
        time.sleep(0.5)
        pr = read_json(failure) or read_json(progress) or {}
        state = pr.get("state")
        if time.time() - last_print > 5:
            print(f"  {state}: {pr.get('calls_done', 0)}/{pr.get('calls_total', 0)} calls, items {pr.get('items', 0)}, "
                  f"gold {pr.get('gold', 0)}, exp {pr.get('exp', 0):,}" + (f" [{pr.get('pause')}]" if pr.get("pause") else ""))
            last_print = time.time()
        if state in ("done", "error", "aborted"):
            break
        if time.time() - t0 > 3 * 3600:
            print("giving up waiting after 3 hours")
            break
    pr = read_json(failure) or read_json(progress) or {}
    # the plugin performs the game's own save when the expedition stops; ask
    # once more here so a reward is never left only in memory
    saved = ipc.send("afk save") or []
    print("  " + " ".join(l for l in saved if l.startswith("save:")))
    print(f"expedition {plan['expedition_id']}: {pr.get('state')} - {pr.get('calls_done', 0)}/{pr.get('calls_total', 0)} calls, "
          f"{pr.get('items', 0)} items, {pr.get('gold', 0)} gold, {pr.get('exp', 0):,} exp"
          + (f", failed {pr.get('failed')}" if pr.get("failed") else "") + (f" - {pr.get('error')}" if pr.get("error") else ""))
    expected_calls = sum(q['count'] for q in plan.get('packets', []))
    replay_ok = (pr.get('state') == 'done' and expected_calls > 0
                 and pr.get('calls_done') == pr.get('calls_total') == expected_calls
                 and pr.get('failed') == 0 and pr.get('skipped') == 0)
    save_ok = 'save: saved (character and account save performed)' in saved
    pr['stages'] = dict(replay='done' if replay_ok else 'error', save='done' if save_ok else 'error',
                        ingest='not_requested' if not ingest else 'pending')
    pr['rewards_saved'] = replay_ok and save_ok
    pr['success'] = replay_ok and save_ok and not ingest
    # Vault/network interruption must not lose the already completed save receipt.
    write_json(SESSIONS / f"{plan['expedition_id']}.result.json", pr)
    if ingest and replay_ok and save_ok:
        rc = ingest_spool(SPOOL / f"{plan['expedition_id']}.ndjson", plan.get("label"))
        pr['stages']['ingest'] = 'done' if rc == 0 else 'error'
        pr['ingest_exit_code'] = rc
    pr['rewards_saved'] = replay_ok and save_ok
    pr['success'] = replay_ok and save_ok and pr['stages']['ingest'] in ('done', 'not_requested')
    write_json(SESSIONS / f"{plan['expedition_id']}.result.json", pr)
    return pr


def ingest_spool(spool: Path, label: str | None, keep_filtered: bool = False) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import ingest_spool as mod  # tools/ingest_spool.py
    except ImportError:
        print("ingest_spool.py missing next to afk.py; run it by hand later")
        return 1
    if not spool.exists():
        print(f"no spool at {spool}")
        return 1
    argv = ["ingest_spool.py", str(spool)] + (["--label", label] if label else []) + (["--keep-filtered"] if keep_filtered else [])
    try:
        rc = mod.main(argv)
    except SystemExit as e:
        rc = e.code
    except (OSError, ValueError, RuntimeError) as e:
        print(f'vault ingest failed: {e}')
        rc = 1
    if rc not in (0, None):
        print(f"vault ingest did not complete (exit {rc}); items stay in {spool}. Start the Item Editor and run:\n"
              f"  py tools\\ingest_spool.py \"{spool}\"")
    return 0 if rc in (0, None) else (rc if isinstance(rc, int) else 1)


def run_succeeded(result) -> bool:
    return bool(result and result.get('state') == 'done' and result.get('rewards_saved', result.get('success', False))
                and not result.get('failed') and not result.get('skipped'))


# ------------------------------------------------------------------ clock
@serialized_rewards
def cmd_start(args) -> None:
    plan_path = Path(args.plan)
    plan = read_json(plan_path)
    if not plan:
        sys.exit("cannot read plan")
    require_reward_plan(plan)
    character_key(plan.get('character'))
    if len(plan.get('zones', [])) != 1:
        sys.exit('create a new single-zone plan before starting the clock')
    st = read_json(STATE, {}) or {}
    if st.get("armed"):
        sys.exit(f"an expedition is already armed ({st['armed']['expedition_id']} since {st['armed']['started_at']}); claim or cancel it first")
    st["armed"] = {"expedition_id": plan["expedition_id"], "plan": str(plan_path), "started_at": iso(now_utc()), "hours": plan["hours"]}
    write_json(STATE, st)
    print_preview(plan)
    print(f"armed at {st['armed']['started_at']} for up to {plan['hours']:.2f} h. Quit the game if you like; come back and run `afk.py claim`.")


@serialized_rewards
def cmd_cancel(args) -> None:
    st = read_json(STATE, {}) or {}
    if not st.get("armed"):
        print("nothing armed")
        return
    ident=st['armed']['expedition_id']+'_claim'
    if any((folder/f'{ident}{suffix}').exists() for folder,suffix in ((SESSIONS,'.progress.json'),(SESSIONS,'.failure.json'),(SPOOL,'.ndjson'))):
        sys.exit('delivery has started; inspect recovery instead of cancelling the clock')
    st["cancelled"] = st.pop("armed")
    write_json(STATE, st)
    print("cancelled")


@serialized_rewards
def cmd_claim(args) -> None:
    st = read_json(STATE, {}) or {}
    armed = st.get("armed")
    if not armed:
        sys.exit("nothing armed: afk.py start <plan> first")
    plan = read_json(Path(armed["plan"]))
    if not plan:
        sys.exit("the armed plan file is gone")
    started = parse_iso(armed["started_at"])
    elapsed_h = (now_utc() - started).total_seconds() / 3600.0
    credited_h = max(0.0, min(elapsed_h, float(armed["hours"]), MAX_HOURS))
    factor = credited_h / float(plan["hours"]) if plan["hours"] else 0.0
    claim_id = f"{plan['expedition_id']}_claim"
    claim_path = PLANS / f"{claim_id}.json"
    if not args.dry_run and plan.get('panel_version'):
        review=recovery.inspect(DATA,claim_id)
        if review['recoverable']:
            recovery.settle(DATA,claim_id)
            print('Saved claim recovered without replay. Transfer queued items to the Vault separately.')
            return
        if review['status']!='not_started':
            sys.exit('claim records require review; rewards were not repeated: '+' '.join(review['reasons']))
    pr_old = read_json(SESSIONS / f"{claim_id}.progress.json") or {}
    reuse = pr_old.get("state") in ("running", "paused", "aborted", "error", "done") and claim_path.exists()
    if reuse:
        # the plugin already holds (or finished) this claim's counts: follow it, never rescale
        scaled = read_json(claim_path)
        print(f"a claim for this expedition is already {pr_old['state']} "
              f"({pr_old.get('calls_done', 0)}/{pr_old.get('calls_total', 0)} calls); following it")
    else:
        scaled = scale_plan(plan, factor, claim_id)
        print(f"elapsed {elapsed_h:.2f} h, credited {credited_h:.2f} h of {armed['hours']:.2f} h")
    print_preview(scaled)
    if args.dry_run:
        print("(dry run: nothing replayed, the clock stays armed)")
        return
    if scaled["preview"]["calls"] <= 0:
        print("nothing to credit yet")
        return
    if not reuse:
        write_json(claim_path, scaled)
    pr = run_plan(claim_path, scaled, game_bin(args), ingest=not args.no_ingest, anywhere=args.anywhere, forgepact_ignore=args.forgepact_ignore)
    if run_succeeded(pr):
        credited_h = float(scaled.get("hours", 0)) * float(scaled.get("scale", 1.0)) if reuse else credited_h
        st["last_claim"] = {"expedition_id": claim_id, "credited_hours": credited_h, "at": iso(now_utc()), "result": pr}
        st.pop("armed", None)
        write_json(STATE, st)
        print("claimed. The clock is free again.")
    else:
        print("the run did not finish; the clock stays armed so you can claim again (it resumes where it stopped)")
        sys.exit(1)


@serialized_rewards
def cmd_run(args) -> None:
    plan_path = Path(args.plan)
    plan = read_json(plan_path)
    if not plan:
        sys.exit("cannot read plan")
    print_preview(plan)
    pr = run_plan(plan_path, plan, game_bin(args), ingest=not args.no_ingest, anywhere=args.anywhere, forgepact_ignore=args.forgepact_ignore)
    if not run_succeeded(pr):
        sys.exit(1)


def cmd_status(args) -> None:
    st = read_json(STATE, {}) or {}
    print(f"ForgePact now: {forgepact_text(forgepact_current())}")
    armed = st.get("armed")
    if armed:
        started = parse_iso(armed["started_at"])
        elapsed_h = (now_utc() - started).total_seconds() / 3600.0
        credited = min(elapsed_h, float(armed["hours"]), MAX_HOURS)
        print(f"armed: {armed['expedition_id']} since {armed['started_at']} - elapsed {elapsed_h:.2f} h, credited so far {credited:.2f} h "
              f"(cap {min(float(armed['hours']), MAX_HOURS):.2f} h)")
    else:
        print("no expedition armed")
    lc = st.get("last_claim")
    if lc:
        r = lc.get("result", {})
        print(f"last claim: {lc['expedition_id']} at {lc['at']} - {lc['credited_hours']:.2f} h, {r.get('items', 0)} items, {r.get('gold', 0)} gold, {r.get('exp', 0):,} exp")
    for f in sorted(SESSIONS.glob("*.progress.json")):
        pr = read_json(f) or {}
        if pr.get("state") in ("running", "paused", "error", "aborted"):
            print(f"unfinished: {pr.get('expedition_id')} {pr.get('state')} {pr.get('calls_done')}/{pr.get('calls_total')} (afk.py run {PLANS / (pr.get('expedition_id','') + '.json')})")


def cmd_ingest(args) -> None:
    rc = ingest_spool(Path(args.spool), args.label, keep_filtered=args.keep_filtered)
    if rc:
        sys.exit(rc)


def cmd_packets(args) -> None:
    """List captured packets (for --extra): hash prefix, monster, rank, room, exp, kills seen."""
    pidx = packet_index()
    seen = Counter()
    for f in SESSIONS.glob("capture_*.ndjson"):
        for r in read_ndjson(f):
            if r.get("kind") in ("kill", "break") and r.get("packet"):
                seen[r["packet"]] += 1
    rows = []
    for h, m in pidx.items():
        if args.room and m.get("room") != args.room:
            continue
        key = (m.get("monster_key") or m.get("self_object") or "")
        if args.find and args.find.lower() not in key.lower():
            continue
        rows.append((h[:12], key, m.get("rank"), m.get("room"), m.get("exp"), seen[h], "" if m.get("complete") else "INCOMPLETE"))
    for r in sorted(rows, key=lambda r: (-r[5], r[1])):
        print(f"  {r[0]}  {r[1]:28s} rank {str(r[2]):4s} {str(r[3]):14s} exp {str(r[4]):8s} seen {r[5]:5d} {r[6]}")
    print(f"{len(rows)} packets")


def cmd_events(args) -> None:
    """Rewards that are not kills, as observed in capture sessions: which
    routine ran, from which object, with what arguments, and what items it
    built (rift and battlefield completions, chaos tower rewards, loot
    explosions). Feeds the design of event replay."""
    files = [Path(args.session)] if args.session not in (None, "latest", "all") else         sorted(SESSIONS.glob("capture_*.ndjson"), key=lambda f: f.stat().st_mtime)
    if args.session in (None, "latest"):
        files = files[-1:]
    total = 0
    for f in files:
        recs = read_ndjson(f)
        events = [r for r in recs if r.get("kind") == "event"]
        if not events:
            continue
        items = [r for r in recs if r.get("kind") == "item" and r.get("ctx") == "live_event"]
        print(f"{f.name}: {len(events)} event(s), {len(items)} item(s) built inside them")
        by_script = Counter(e.get("script") for e in events)
        for sc, n in by_script.most_common():
            its = [r for r in items if r.get("packet") == sc]
            print(f"  {sc:24s} x{n:<4d} items {len(its):4d}   " + ", ".join(f"{r.get('name')}" for r in its[:6]))
        for e in events[:args.show]:
            argtxt = "; ".join(f"{a.get('kind')}={a.get('value')[:80]}" for a in e.get("args", []))
            print(f"    {e.get('t')} {e.get('script')} self={e.get('self')} room={e.get('room')} argc={e.get('argc')} :: {argtxt}")
        total += len(events)
    if not total:
        print("no reward events recorded yet: complete a rift, battlefield or chaos tower with capture on")


def cmd_migrate_build(args) -> None:
    """Re-label packets, profiles and plans with the current game build id.
    Needed once after the plugin switched from a file-time identity to the
    PE-header identity (2026-09-18), or after a verified re-copy of the exe."""
    to = args.to or (read_json(DATA / "build.json") or {}).get("game_build")
    if not to:
        sys.exit("current build id unknown: start the game once (the plugin writes afk\build.json) or pass --to")
    n_packets = n_profiles = n_plans = 0
    for f in PACKETS.glob("*.json"):
        d = read_json(f)
        if isinstance(d, dict) and d.get("game_build_id") != to and (args.from_id is None or d.get("game_build_id") == args.from_id):
            d["game_build_id_previous"] = d.get("game_build_id"); d["game_build_id"] = to
            f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8"); n_packets += 1
    for f in PROFILES.glob("*.json"):
        d = read_json(f)
        if isinstance(d, dict) and d.get("game_build") != to and (args.from_id is None or d.get("game_build") == args.from_id):
            d["game_build"] = to; write_json(f, d); n_profiles += 1
    for f in PLANS.glob("*.json"):
        d = read_json(f)
        if isinstance(d, dict) and d.get("game_build") and d.get("game_build") != to and (args.from_id is None or d.get("game_build") == args.from_id):
            d["game_build"] = to; write_json(f, d); n_plans += 1
    print(f"build id -> {to}: {n_packets} packets, {n_profiles} profiles, {n_plans} plans re-labelled")


# ------------------------------------------------------------------ capture / verify
def cmd_capture(args) -> None:
    cfg = read_json(CONFIG, {}) or {}
    if args.action == "auto":
        cfg["auto_capture"] = args.value == "on"
        write_json(CONFIG, cfg)
        print(f"auto capture {'ON: the plugin arms capture whenever you are in a map' if cfg['auto_capture'] else 'off'}")
        if args.value == "off":
            return
    line = "afk capture on" if args.action in ("on", "auto") else ("afk capture off" if args.action == "off" else "afk capture stats")
    try:
        ipc = Ipc(game_bin(args))
    except SystemExit:
        print("(game folder unknown; the setting is saved, the running game was not told)")
        return
    reply = ipc.send(line, timeout=8.0)
    print("\n".join(reply) if reply else "the game is not running or not answering (the setting is saved)")


def cmd_verify(args) -> None:
    """Replay the newest capture session's kills the same number of times,
    with experience and gold OFF, and compare the replay statistics with what
    the game dropped live. Items of these statistics runs are not ingested."""
    from collections import Counter
    from item_labels import rarity_name
    session = Path(args.session) if args.session not in (None, "latest") else latest_session_with_kills()
    if session is None:
        sys.exit("no capture session with kills found (play with capture on first)")
    profiles = build_profile(session, args.room, False)
    if not profiles:
        sys.exit("no kills in that session")
    prof = max(profiles, key=lambda p: p["kills"] + p["breaks"])
    room = prof["room"]
    if prof["kills"] + prof["breaks"] < 100:
        sys.exit(f"only {prof['kills'] + prof['breaks']} kills in {room}: too few to compare (play longer with capture on)")
    recs = read_ndjson(session)
    kills = [r for r in recs if r.get("kind") in ("kill", "break") and r.get("room") == room]
    live = [r for r in recs if r.get("kind") == "item" and r.get("ctx") == "live" and r.get("packet") in {k["packet"] for k in kills}]
    n = len(kills)
    # Verification neither replaces a real profile nor deletes old results.
    base = make_plan(prof['basis_seconds'] / 3600.0, [(room, 1.0)], f'verify_{room}', args.per_frame, 'none', profile_overrides={room: prof})
    base["exp"] = False
    rebuild_preview(base)
    bin_dir = game_bin(args)
    print(f"live sample: {session.name}, {room}: {n} kills/breaks, {len(live)} items ({100 * len(live) / n:.1f} per 100), "
          f"{prof['exp_per_min']:.0f} exp/min")
    print(f"replaying {base['preview']['calls']} calls x{args.runs} with experience and gold off (statistics only)...")
    results = []
    run_id = now_utc().strftime('%Y%m%d_%H%M%S_%f')
    for i in range(1, args.runs + 1):
        plan = dict(base); plan["expedition_id"] = f"verify_{room}_{run_id}_stat{i}"; plan["stats_only"] = True
        path = PLANS / f"{plan['expedition_id']}.json"
        prog = SESSIONS / f"{plan['expedition_id']}.progress.json"
        write_json(path, plan)
        pr = run_plan(path, plan, bin_dir, ingest=False, anywhere=args.anywhere, forgepact_ignore=True)
        if not run_succeeded(pr):
            sys.exit("a statistics run did not finish; see above")
        sp = read_ndjson(SPOOL / f"{plan['expedition_id']}.ndjson")
        items = [x for x in sp if x.get("kind") == "item"]
        summ = [x for x in sp if x.get("kind") in ("summary", "partial")][-1]
        results.append((items, summ))
    runs = len(results)
    mean_items = sum(len(it) for it, _ in results) / runs
    mean_gold = sum(float(sm.get("gold", 0)) for _, sm in results) / runs
    sd = max(1.0, len(live)) ** 0.5
    print(f"\nitems per 100 kills : live {100 * len(live) / n:6.1f}   replay {100 * mean_items / n:6.1f}  "
          f"(runs {[len(it) for it, _ in results]}; live 2-sd band {len(live) - 2 * sd:.0f}..{len(live) + 2 * sd:.0f} -> "
          f"{'consistent' if abs(mean_items - len(live)) <= 2 * sd else 'DIFFERENT'})")
    print(f"gold per kill       : replay {mean_gold / n:.2f}   (live gold is not recorded per kill; compare with your purse)")
    ra = Counter(rarity_name(r.get("rarity")) for r in live)
    rb = Counter()
    for it, _ in results:
        rb.update(rarity_name(x["item"].get("itemInfoStruct", {}).get("27")) for x in it)
    tot_b = max(1, sum(rb.values()))
    print("rarity              :  live%  replay%")
    for k in sorted(set(ra) | set(rb), key=lambda k: -(ra[k] + rb[k])):
        print(f"  {k:12s}         {100 * ra[k] / max(1, len(live)):5.1f}  {100 * rb[k] / tot_b:5.1f}")
    pk_rank = {r["packet"]: r.get("rank") for r in kills}
    kr = Counter(pk_rank[r["packet"]] for r in kills)
    lr = Counter(pk_rank.get(r["packet"]) for r in live)
    print("per rank (items/100):  kills   live  replay")
    for rk in sorted(kr, key=lambda k: -kr[k]):
        rep = sum(sum(1 for x in it if pk_rank.get(x.get("packet")) == rk) for it, _ in results) / runs
        print(f"  rank {str(rk):3s}            {kr[rk]:5d}  {100 * lr[rk] / kr[rk]:6.1f} {100 * rep / kr[rk]:7.1f}")
    filt = sum(int(sm.get("items_filtered", 0)) for _, sm in results)
    if filt:
        print(f"your loot filter would hide {100 * filt / max(1, sum(len(it) for it, _ in results)):.0f}% of the replay items")


# ------------------------------------------------------------------ main
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-bin", help="the game's bin folder (remembered in config.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile"); p.add_argument("action", choices=["build", "list"])
    p.add_argument("--session", help="capture_*.ndjson or 'latest'"); p.add_argument("--room")
    p.add_argument("--active", action="store_true", help="rate over active 30 s windows instead of wall clock")
    p.add_argument("--min-events", type=int, default=20); p.add_argument("--min-seconds", type=float, default=calibration.MIN_PROFILE_SECONDS); p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("plan"); p.add_argument("--hours", type=float, required=True)
    p.add_argument("--zone", action="append", help="ROOM[=WEIGHT], repeatable"); p.add_argument("--id")
    p.add_argument("--per-frame", type=int, default=40); p.add_argument("--no-gold", action="store_true")
    p.add_argument("--extra", action="append", help="PACKET=PER_HOUR: add a packet (boss, goblin) at a chosen rate; see `packets`")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("packets"); p.add_argument("--room"); p.add_argument("--find", help="substring of the monster key or object")
    p.set_defaults(fn=cmd_packets)

    p = sub.add_parser("preview"); p.add_argument("plan"); p.set_defaults(fn=cmd_preview)
    p = sub.add_parser("start"); p.add_argument("plan"); p.set_defaults(fn=cmd_start)
    p = sub.add_parser("cancel"); p.set_defaults(fn=cmd_cancel)
    p = sub.add_parser("claim"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--no-ingest", action="store_true")
    p.add_argument("--anywhere", action="store_true", help="replay even when not standing in a calibrated zone")
    p.add_argument("--forgepact-ignore", action="store_true", help="replay even if rate-affecting ForgePact settings changed"); p.set_defaults(fn=cmd_claim)
    p = sub.add_parser("run"); p.add_argument("plan"); p.add_argument("--no-ingest", action="store_true")
    p.add_argument("--anywhere", action="store_true", help="replay even when not standing in a calibrated zone")
    p.add_argument("--forgepact-ignore", action="store_true", help="replay even if rate-affecting ForgePact settings changed"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("migrate-build"); p.add_argument("--to", help="new build id (default: afk\build.json)"); p.add_argument("--from", dest="from_id", help="only re-label this old id")
    p.set_defaults(fn=cmd_migrate_build)
    p = sub.add_parser("events"); p.add_argument("--session", help="capture file, 'latest' or 'all'"); p.add_argument("--show", type=int, default=12)
    p.set_defaults(fn=cmd_events)
    p = sub.add_parser("capture"); p.add_argument("action", choices=["on", "off", "stats", "auto"])
    p.add_argument("value", nargs="?", choices=["on", "off"], help="for `auto`: on / off"); p.set_defaults(fn=cmd_capture)
    p = sub.add_parser("verify"); p.add_argument("--session", help="capture_*.ndjson or 'latest'"); p.add_argument("--room")
    p.add_argument("--runs", type=int, default=3); p.add_argument("--per-frame", type=int, default=40)
    p.add_argument("--anywhere", action="store_true"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("ingest"); p.add_argument("spool"); p.add_argument("--label")
    p.add_argument("--keep-filtered", action="store_true", help="also deposit items your in-game loot filter hides"); p.set_defaults(fn=cmd_ingest)

    args = ap.parse_args(argv)
    for d in (PACKETS, SESSIONS, SPOOL, PROFILES, PLANS):
        d.mkdir(parents=True, exist_ok=True)
    if args.game_bin:
        game_bin(args)          # validate and remember it right away
    args.fn(args)


if __name__ == "__main__":
    main()
