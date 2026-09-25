"""Siege (0.7.0): hold a calibrated region against waves that keep growing.

A challenge layer over the measured pace, not a combat simulation. The hero's
calibrated kills per minute and the minute-to-minute variation measured in its
calibration decide how long the gate holds; every reward is still a replay of
the region's own kill packets through the game's drop routine.

Every WAVE_MINUTES a wave demands a kill rate. Level L's first wave demands
BASE_DEMAND x LEVEL_STEP^(L-1) kills per minute and every wave GROWTH times more.
Each wave the hero's rate is the calibrated pace times the mean of
WAVE_MINUTES one-minute factors drawn from its own calibration windows. A wave
the hero cannot keep up with damages the gate by the shortfall's share of
GATE_HP; a wave cleared with REPAIR_MARGIN to spare repairs REPAIR. The siege
falls when the gate breaks. The waves never run dry: a wave yields the kills
the hero made in it (its rate x WAVE_MINUTES, the measured pace), so a siege
pays what farming pays for as long as the gate holds, plus its special waves
and Magic Find bonus. A low level is safe with a small bonus; a high level
has a larger bonus and falls sooner.

Every 5th wave is an elite wave (the calibration's strongest ordinary monster
variants only), every 10th a treasure wave (1 + L // 5 of its kills are the
region's treasure goblins, when the calibration met any), every 25th a boss
wave when a boss of that region passed ``afk special verify``. Level L adds
+2% x L Magic Find to the saved setting (up to the x100 limit). The timeline
is drawn once at the start from a stored seed; a claim delivers the waves that
were complete by then, so claiming early never re-rolls it.
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
from copy import deepcopy
from pathlib import Path

import afk
import reward_modifiers

VERSION = 1
WAVE_MINUTES = 5
GROWTH = 1.03
BASE_DEMAND = 10.0
LEVEL_STEP = 1.15
MAX_LEVEL = 50
GATE_HP = 100.0
MAX_DAMAGE = 50.0               # a wave that goes wholly unanswered takes half the gate
FULL_EVIDENCE_WINDOWS = 30      # 30+ calibration minutes: their variation counts in full
REPAIR_MARGIN = 1.25
REPAIR = 5.0
MF_BONUS_PER_LEVEL = 0.02
ELITE_EVERY, TREASURE_EVERY, BOSS_EVERY = 5, 10, 25
ORDINARY_RANKS = (1, 2, 3, 4)
# The game's loot goblins (isGoblin = 1): Goblin_{Treasure,Rune,Ore,Orb,Shadow}_obj.
GOBLIN_KEYS = ('treasure_goblin', 'goblinrune', 'goblinshadow', 'goblinore', 'goblinorb')
GOBLIN_OBJECTS = ('Goblin_Treasure_obj', 'Goblin_Rune_obj', 'Goblin_Ore_obj', 'Goblin_Orb_obj', 'Goblin_Shadow_obj')
FALLBACK_SPREAD = 0.10          # no calibration windows: +-10% per wave
SUGGEST_MAX_FALL = 0.25         # the suggested level holds in at least 3 of 4 simulated sieges
RECORDS = 'siege-records.json'


def demand(level: int, wave: int) -> float:
    return BASE_DEMAND * LEVEL_STEP ** (level - 1) * GROWTH ** (wave - 1)


def wave_count(hours: float, wave_minutes: float = WAVE_MINUTES) -> int:
    """Whole waves in ``hours``. Planning, claims and the live view all count
    this way; the margin keeps e.g. 35 minutes (0.58333 h) at 7 waves, not 6."""
    return max(0, int(math.floor(float(hours) * 60.0 / wave_minutes + 1e-9)))


def pace_factors(profile: dict) -> list[float] | None:
    """The calibration's one-minute kill counts over their mean (1 = average minute)."""
    rates = ((profile.get('quality') or {}).get('window_rates') or [])
    rates = [float(r) for r in rates if isinstance(r, (int, float)) and not isinstance(r, bool) and math.isfinite(r) and r >= 0]
    if len(rates) < 3:
        return None
    mean = statistics.mean(rates)
    return [r / mean for r in rates] if mean > 0 else None


def _round(value: float, rng: random.Random) -> int:
    whole = math.floor(value)
    return int(whole + (1 if rng.random() < value - whole else 0))


def is_goblin(packet: dict) -> bool:
    """One of the game's five loot goblins (treasure, rune, shadow, orb, ore)."""
    return (str(packet.get('object') or '') in GOBLIN_OBJECTS
            or any(k in str(packet.get('monster_key', '')).lower() for k in GOBLIN_KEYS))


def ordinary_breaks_per_min(profile: dict) -> float:
    """The calibration's break rate without chest openings (chests never replay)."""
    total = sum(float(q.get('count') or 0) for q in profile.get('packets', []) if q.get('kind') == 'break')
    kept = sum(float(q.get('count') or 0) for q in profile.get('packets', []) if q.get('kind') == 'break' and not afk.is_chest(q))
    return float(profile.get('breaks_per_min') or 0) * (kept / total) if total else 0.0


def groups(profile: dict, specials=()) -> dict:
    """Which calibrated packets each kind of wave replays."""
    kills = [q for q in profile.get('packets', []) if q.get('kind') == 'kill' and q.get('rank') in ORDINARY_RANKS]
    breaks = [q for q in profile.get('packets', []) if q.get('kind') == 'break' and not afk.is_chest(q)]
    top = max((q.get('rank') for q in kills), default=None)
    elite = [q for q in kills if q.get('rank') == 4] or [q for q in kills if top is not None and q.get('rank') == top]
    goblin = [q for q in kills if is_goblin(q)]
    boss = [q for q in profile.get('packets', []) if q.get('kind') == 'kill' and q.get('hash') in set(specials)]
    pick = lambda group: [q['hash'] for q in group]
    return dict(normal=pick(kills), elite=pick(elite), goblin=pick(goblin), boss=pick(boss), breaks=pick(breaks))


def wave_kind(wave: int, available: dict) -> str:
    if wave % BOSS_EVERY == 0 and available.get('boss'):
        return 'boss'
    if wave % TREASURE_EVERY == 0 and available.get('goblin'):
        return 'treasure'
    if wave % ELITE_EVERY == 0 and available.get('elite'):
        return 'elite'
    return 'normal'


def timeline(pace: float, breaks_per_min: float, factors, level: int, waves: int, seed: int, available: dict):
    """The siege, wave by wave: (waves, the wave that broke the gate or None)."""
    rng = random.Random(seed)
    # A short calibration says little about how uneven the hero's minutes are
    # (a first idle minute weighs a lot in five): its variation counts in
    # proportion to its length, fully from FULL_EVIDENCE_WINDOWS minutes on.
    weight = min(1.0, len(factors) / FULL_EVIDENCE_WINDOWS) if factors else 0.0
    hp, out = GATE_HP, []
    for k in range(1, waves + 1):
        if factors:
            f = 1.0 + (statistics.mean(rng.choice(factors) for _ in range(WAVE_MINUTES)) - 1.0) * weight
        else:
            f = 1.0 + rng.uniform(-FALLBACK_SPREAD, FALLBACK_SPREAD)
        rate, need = pace * f, demand(level, k)
        kills = _round(rate * WAVE_MINUTES, rng)
        breaks = _round(breaks_per_min * WAVE_MINUTES * f, rng)
        if rate < need:
            hp -= MAX_DAMAGE * (1 - rate / need)
        elif rate >= REPAIR_MARGIN * need:
            hp = min(GATE_HP, hp + REPAIR)
        kind = wave_kind(k, available)
        row = dict(wave=k, kind=kind, demand=round(need, 3), rate=round(rate, 3), kills=kills, breaks=breaks, hp=round(max(hp, 0.0), 1),
                   held=rate >= need)
        if kind == 'treasure':
            row['goblins'] = min(kills, 1 + level // 5)
        if kind == 'boss':
            row['bosses'] = 1
        out.append(row)
        if hp <= 0:
            return out, k
    return out, None


def _totals(waves: list[dict]) -> dict:
    t = dict(normal=0, elite=0, goblin=0, boss=0, breaks=0)
    for w in waves:
        special = w.get('goblins', 0)
        if w['kind'] == 'elite':
            t['elite'] += w['kills']
        else:
            t['normal'] += w['kills'] - special
        t['goblin'] += special
        t['boss'] += w.get('bosses', 0)
        t['breaks'] += w['breaks']
    return t


def allocate(siege: dict, waves: int) -> list[dict]:
    """The packets the first ``waves`` waves replay, each group split by its measured mix."""
    info, grouped = siege['packet_info'], siege['groups']
    counts = {}
    for group, total in _totals(siege['waves'][:waves]).items():
        members = grouped.get(group) or []
        if not total or not members:
            continue
        for h, n in zip(members, afk.largest_remainder(total, [info[h]['weight'] for h in members])):
            if n > 0:
                counts[h] = counts.get(h, 0) + n
    return [dict(info[h], count=n) for h, n in counts.items()]


def report_hours(plan: dict) -> float:
    """When the siege is over: the wave that broke the gate, else the chosen time."""
    s = plan['siege']
    if s.get('fell_at'):
        return min(float(plan['hours']), s['fell_at'] * s['wave_minutes'] / 60.0)
    return float(plan['hours'])


def build_plan(profile: dict, level, hours, expedition_id: str, modifiers: dict, seed=None, specials=()) -> dict:
    """A Siege expedition plan for one calibrated region (see the module note)."""
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise ValueError(f'Choose a Siege level from 1 to {MAX_LEVEL}.')
    hours = float(hours)
    if not math.isfinite(hours) or not 0.25 <= hours <= afk.MAX_HOURS:
        raise ValueError('Duration must be between 15 minutes and 8 hours.')
    pace = float(profile.get('kills_per_min') or 0)
    if not pace > 0:
        raise ValueError('This calibration has no kill pace.')
    # A siege lasts whole waves: the armed time is exactly the planned waves, so
    # every wave can be credited and the siege always reaches its end.
    total = wave_count(hours)
    hours = total * WAVE_MINUTES / 60.0
    room = profile['room']
    plan = afk.make_plan(hours, [(room, 1)], expedition_id, 40, 'pickup', profile_overrides={room: profile})
    grouped = groups(profile, specials)
    by_hash = {q['hash']: q for q in profile['packets']}
    candidates = sorted({h for members in grouped.values() for h in members})
    # Independent rewards give every candidate its native XP once, up front.
    plan['packets'] = [dict(hash=h, count=1, monster_key=by_hash[h].get('monster_key', ''), room=room, kind=by_hash[h]['kind'],
                            exp=by_hash[h].get('exp')) for h in candidates]
    reward_modifiers.apply_to_plan(plan, profile, modifiers)
    info = {p['hash']: dict(p, weight=float(by_hash[p['hash']].get('count') or by_hash[p['hash']].get('weight') or 1)) for p in plan['packets']}
    for p in info.values():
        p.pop('count', None)
    seed = seed if isinstance(seed, int) else random.SystemRandom().randrange(1 << 62)
    factors = pace_factors(profile)
    available = {k: bool(v) for k, v in grouped.items()}
    waves, fell = timeline(pace, ordinary_breaks_per_min(profile), factors, level, total, seed, available)
    siege = dict(version=VERSION, level=level, seed=seed, wave_minutes=WAVE_MINUTES, growth=GROWTH, base_demand=BASE_DEMAND,
                 level_step=LEVEL_STEP, gate_hp=GATE_HP, pace=pace, pace_source='calibration windows' if factors else 'fixed spread',
                 waves=waves, fell_at=fell, groups=grouped, packet_info=info, magic_find_bonus=1 + MF_BONUS_PER_LEVEL * level)
    plan['mode'] = 'siege'
    plan['siege'] = siege
    plan['packets'] = allocate(siege, len(waves))
    for p in plan['packets']:
        p.pop('weight', None)
    mods = plan['reward_modifiers']
    mods['magic_find'] = min(100.0, mods['magic_find'] * siege['magic_find_bonus'])
    plan['effective_magic_find'] = float((profile.get('reward_baseline') or {}).get('magic_find') or 0) * mods['magic_find']
    afk.rebuild_preview(plan)
    plan['preview']['exp'] = int(sum(p['exp'] * p['count'] for p in plan['packets']))
    plan['preview']['items_estimate'] = None; plan['preview']['gold_estimate'] = None
    return plan


def claim_plan(plan: dict, credited_hours: float, claim_id: str) -> dict:
    """The waves complete by the credited time, as a normal claim plan."""
    s = plan['siege']
    done = max(0, min(len(s['waves']), wave_count(credited_hours, s['wave_minutes'])))
    out = deepcopy(plan)
    out['expedition_id'] = claim_id
    out['scaled_from'] = plan['expedition_id']
    out['scale'] = (done * s['wave_minutes'] / 60.0) / float(plan['hours']) if plan.get('hours') else 0.0
    out['packets'] = []
    for p in allocate(s, done):
        p.pop('weight', None)
        out['packets'].append(p)
    for z in out['zones']:
        z['minutes'] = done * s['wave_minutes']
    fought = s['waves'][:done]
    fell = bool(s.get('fell_at')) and done >= s['fell_at']
    out['siege_claim'] = dict(level=s['level'], waves_fought=done, waves_held=sum(1 for w in fought if w['held']), fell=fell,
                              hp=fought[-1]['hp'] if fought else s['gate_hp'],
                              elite_waves=sum(1 for w in fought if w['kind'] == 'elite'),
                              treasure_waves=sum(1 for w in fought if w['kind'] == 'treasure'),
                              boss_waves=sum(1 for w in fought if w['kind'] == 'boss'))
    afk.rebuild_preview(out)
    out['preview']['exp'] = int(sum(p['exp'] * p['count'] for p in out['packets']))
    return out


def live_view(plan: dict, elapsed_hours: float) -> dict:
    """What the player may see while it runs: completed waves only, never the future."""
    s = plan['siege']
    done = max(0, min(len(s['waves']), wave_count(max(0.0, elapsed_hours), s['wave_minutes'])))
    fought = s['waves'][:done]
    fell = bool(s.get('fell_at')) and done >= s['fell_at']
    over = fell or done >= len(s['waves'])
    upcoming = next(({'wave': w['wave'], 'kind': w['kind']} for w in s['waves'][done:] if w['kind'] != 'normal'), None) if not over else None
    return dict(level=s['level'], waves_done=done, wave=None if over else done + 1, hp=fought[-1]['hp'] if fought else s['gate_hp'],
                gate_hp=s['gate_hp'], fell=fell, fell_at=s['fell_at'] if fell else None, over=over,
                kills=sum(w['kills'] for w in fought), held=sum(1 for w in fought if w['held']),
                last=[dict((k, w[k]) for k in ('wave', 'kind', 'held', 'hp')) for w in fought[-5:]], next_special=upcoming,
                magic_find_bonus=s['magic_find_bonus'])


def forecast(profile: dict, level: int, hours: float, runs: int = 120, specials=()) -> dict:
    """What a Siege at this level usually looks like for this calibration (simulated)."""
    pace = float(profile.get('kills_per_min') or 0)
    if not pace > 0:
        raise ValueError('This calibration has no kill pace.')
    factors = pace_factors(profile)
    available = {k: bool(v) for k, v in groups(profile, specials).items()}
    total = wave_count(hours)
    reached, kills, falls = [], [], 0
    for run in range(runs):
        waves, fell = timeline(pace, 0.0, factors, level, total, 7919 * run + level, available)
        reached.append(len(waves)); kills.append(sum(w['kills'] for w in waves)); falls += fell is not None
    reached.sort()
    ordinary = pace * total * WAVE_MINUTES
    q = lambda p: reached[min(len(reached) - 1, int(p * len(reached)))]
    return dict(level=level, waves_total=total, waves_median=q(0.5), waves_low=q(0.1), waves_high=q(0.9),
                fall_chance=falls / runs, kill_share=(statistics.mean(kills) / ordinary) if ordinary else 0.0,
                first_wave_demand=round(demand(level, 1), 2), pace=round(pace, 2), magic_find_bonus=1 + MF_BONUS_PER_LEVEL * level,
                elite=available['elite'], treasure=available['goblin'], boss=available['boss'])


def suggest_level(profile: dict, hours: float = 2.0) -> int:
    """The highest level whose siege usually lasts the chosen time with the gate
    still standing. Lasting is not enough: a gate that breaks on the very last
    wave also "lasts", and at the next level up it broke in every run
    (MEASURED 2026-09-24: Suh, Act 2-5, 30 min - level 35 fell in 40 of 40)."""
    lo, hi, best = 1, MAX_LEVEL, 1
    total = wave_count(hours)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = forecast(profile, mid, hours, runs=40)
        if f['waves_median'] >= total and f['fall_chance'] <= SUGGEST_MAX_FALL:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


# ------------------------------------------------------------------ records
def load_records(data) -> dict:
    value = afk.read_json(Path(data) / RECORDS, {}) or {}
    return value if isinstance(value, dict) and value.get('schema') == 1 else dict(schema=1, heroes={})


def best(data, hero: str, room: str, level: int) -> int:
    return int(((load_records(data)['heroes'].get(hero) or {}).get(room) or {}).get(str(level), 0))


def record_claim(data, hero: str, room: str, claim: dict) -> bool:
    """Remember a settled siege; True when it is the hero's best at that level and region."""
    records = load_records(data)
    rooms = records['heroes'].setdefault(hero, {})
    levels = rooms.setdefault(room, {})
    key = str(claim['level'])
    previous = int(levels.get(key, 0))
    if claim['waves_fought'] > previous:
        levels[key] = claim['waves_fought']
        afk.write_json(Path(data) / RECORDS, records)
        return True
    return False
