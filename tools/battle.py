"""One wave against the town (0.9): walls, towers and heroes against monster groups.

A small, deterministic combat model in one-second steps. It is AFK FARM's own
game layer: it decides how many monsters of each group die and who killed
them, and how much the walls and the keep suffer. Every reward a kill earns is
still a replay of a recorded kill packet through the game's drop routine (see
defense.py); this model never touches the game.

Geometry is one line per side. A position is the distance outside that side's
wall (negative inside the town); the keep stands KEEP_DISTANCE inside. A group
of identical monsters shares one damage pool, and damage finishes the front
monster before the next. A group spawns ``delay`` seconds into the wave,
SPAWN_DISTANCE out, and walks in:
- melee groups stop at the wall and hit it, ranged groups hit it from their reach;
- flying groups pass over the wall and go for the keep;
- burrowers dig under after BURROW_SECONDS at the wall;
- thieves (loot goblins) slip past the wall, hit nothing and escape
  THIEF_ESCAPE seconds after reaching the keep.
A wall at zero health is breached: that side's monsters walk in and hit the
keep. The wave is lost when the keep falls.

Towers stand at their side's wall, or at the keep (side 'keep': every side, but
KEEP_DISTANCE further back). Heroes stand at a side's wall and also fight
anything inside the town; a roaming hero moves to the side under the heaviest
pressure. Each shooter hits the group its priority picks among those in reach.
Damage passes a monster's shield (holy damage strips shields three times as
fast), then its resistance to the damage type (``pierce`` ignores part of a
resistance). ``targets`` > 1 hits that many monsters at once. Frost slows the
group it hits; fire and poison stop regeneration for a few seconds.

Standard library only. Inputs and outputs are plain dicts; the same inputs give
the same result on every run.
"""
from __future__ import annotations

import math

STEP = 1.0                 # seconds per step
MAX_SECONDS = 300          # a wave's five minutes; what is still alive then withdraws (counted as leaked)
SPAWN_DISTANCE = 80.0
KEEP_DISTANCE = 25.0
BURROW_SECONDS = 6.0
TELEPORT_SHARE = 0.35      # of a teleporting group, once, when it reaches the wall
SUMMON_PER_MONSTER = 3     # minions a summoner calls, once, at the wall
SPLIT_HP = 0.30            # each death of a splitter leaves two children at this share of its health
EXPLODE_WALL = 0.5         # a death within EXPLODE_RANGE of the wall: this share of its health as wall damage
EXPLODE_RANGE = 10.0
REGEN_PER_SECOND = 0.02
VAMPIRIC_PER_SECOND = 0.015
FROZEN_AURA = 0.15         # towers of a side lose 15% damage while a frozen group is within 40 of its wall
FROZEN_RANGE = 40.0
ENRAGE_BELOW = 0.5         # once a group has lost half its health, its survivors enrage
ENRAGE_MULT = 1.5
JUGGERNAUT_MULT = 2.0
DISPEL_MULT = 3.0
MAX_SLOW = 0.6
MAX_HOLD = 0.6
ROAM_RANGE = 40.0
THIEF_ESCAPE = 8.0          # a thief (loot goblin) that reaches the keep escapes this long after
SIDES = ('north', 'east', 'south', 'west')
PRIORITIES = ('first', 'strongest', 'weakest', 'flying', 'elite')


def _num(value, default=0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def group(spec: dict) -> dict:
    """A monster group's live state from its spec (see defense.py for the fields)."""
    n = max(0, int(spec.get('count', 0)))
    hp = max(1e-6, _num(spec.get('hp'), 1.0))       # a minion may be a tenth of a Common monster
    shield_each = max(0.0, _num(spec.get('shield')))
    return dict(
        id=str(spec['id']), side=spec.get('side', 'north') if spec.get('side') in SIDES else 'north',
        arch=spec.get('arch', 'grunt'), tier=spec.get('tier', 'normal'), name=spec.get('name') or spec.get('arch', 'monster'),
        rank=int(spec.get('rank', 1)), drops=max(0, int(spec.get('drops', 1))), loot=bool(spec.get('loot', True)),
        boss=bool(spec.get('boss')), affixes=list(spec.get('affixes') or ()), flags=set(spec.get('flags') or ()),
        n=n, alive=n, hp=hp, pool=n * hp, shield_each=shield_each, shield=shield_each * n,
        speed=max(0.5, _num(spec.get('speed'), 6.0)), reach=max(0.0, _num(spec.get('reach'))),
        wall_dps=max(0.0, _num(spec.get('wall_dps'), 1.0)), keep_dps=max(0.0, _num(spec.get('keep_dps'), 1.0)),
        resist={str(k): min(1.0, max(-1.0, _num(v))) for k, v in (spec.get('resist') or {}).items()},
        delay=max(0.0, _num(spec.get('delay'))),
        pos=None, inside=False, spawned=False, at_wall=0.0, inside_for=0.0, slow=0.0, slow_until=-1.0, no_regen_until=-1.0,
        summoned=False, teleported=False, killed_by={}, leaked=0, escaped=0)


def _live(g: dict) -> bool:
    return g['spawned'] and g['alive'] > 0


def _position(g: dict) -> float:
    return -KEEP_DISTANCE if g['inside'] else g['pos']


def _distance(g: dict, src: dict) -> float:
    home = -KEEP_DISTANCE if src.get('side') == 'keep' else 0.0
    return abs(_position(g) - home)


def _can_hit(g: dict, src: dict) -> bool:
    if 'flying' in g['flags']:
        if not src.get('air'):
            return False          # a flyer at the keep still flies: only what hits flyers can hit it
    elif not src.get('ground', True):
        return False
    side = src.get('side')
    if side != 'keep' and g['side'] != side and not (g['inside'] and src.get('mobile')):
        return False
    if g['inside'] and src.get('mobile'):
        return True
    return _num(src.get('min_reach')) <= _distance(g, src) <= _num(src.get('reach'))


def _pick(groups: list[dict], src: dict):
    prio = src.get('priority', 'first')
    best, best_key = None, None
    for g in groups:
        if not (_live(g) and _can_hit(g, src)):
            continue
        near = _position(g)
        if prio == 'strongest':
            key = (-g['hp'], near, g['id'])
        elif prio == 'weakest':
            key = (g['hp'], near, g['id'])
        elif prio == 'flying':
            key = (0 if 'flying' in g['flags'] else 1, near, g['id'])
        elif prio == 'elite':
            key = (-g['rank'], -g['hp'], near, g['id'])
        else:   # first: whoever is closest to getting in
            key = (near, g['id'])
        if best_key is None or key < best_key:
            best, best_key = g, key
    return best


def _damage(g: dict, amount: float, dtype: str, src: dict, t: float, deaths: list) -> None:
    """Apply ``amount`` of ``dtype`` damage to group ``g`` and credit its kills to ``src``."""
    if amount <= 0 or g['alive'] <= 0:
        return
    if g['shield'] > 0:
        rate = DISPEL_MULT if (dtype == 'holy' or src.get('dispel')) else 1.0
        absorbed = min(g['shield'], amount * rate)
        g['shield'] -= absorbed
        amount -= absorbed / rate
        if amount <= 1e-12:
            return
    resist = g['resist'].get(dtype, 0.0)
    if resist > 0:
        resist *= 1.0 - min(1.0, max(0.0, _num(src.get('pierce'))))
    amount *= 1.0 - resist
    if amount <= 0:
        return
    if dtype in ('poison', 'fire'):
        g['no_regen_until'] = t + 3.0
    before = g['alive']
    g['pool'] = max(0.0, g['pool'] - amount)
    alive = max(0, math.ceil(g['pool'] / g['hp'] - 1e-9))
    if alive < before:
        dead = before - alive
        g['alive'] = alive
        g['shield'] = min(g['shield'], g['shield_each'] * alive)
        g['killed_by'][src['id']] = g['killed_by'].get(src['id'], 0) + dead
        deaths.append((g, dead, t))


def _slow(g: dict, amount: float, t: float) -> None:
    if amount > 0 and g['alive'] > 0:
        current = g['slow'] if g['slow_until'] >= t else 0.0
        g['slow'] = min(MAX_SLOW, max(current, amount))
        g['slow_until'] = t + 2.0


def _roam_side(live, walls, current):
    """Where a roaming hero goes: the side with the most monster health near or
    past its wall, weighted up by how hurt that wall is. Ties stay put."""
    best, best_score = current, 0.0
    for s in SIDES:
        threat = sum(g['pool'] for g in live if g['side'] == s and (g['inside'] or _position(g) <= ROAM_RANGE))
        if threat <= 0:
            continue
        w = walls[s]
        score = threat * (2.0 - (w['hp'] / w['max'] if w['max'] > 0 else 0.0))
        if score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9 and s == current):
            best, best_score = s, score
    return best


def _hold(heroes, side) -> float:
    """Melee heroes at a wall block part of the damage it takes."""
    return min(MAX_HOLD, sum(max(0.0, _num(h.get('hold'))) for h in heroes if h.get('side') == side))


def _hit_wall(walls, side, amount, highlights, t, breached):
    if breached[side] or amount <= 0:
        return
    w = walls[side]
    w['hp'] -= amount
    if w['hp'] <= 0:
        w['hp'] = 0.0
        breached[side] = True
        highlights.append(dict(t=t, side=side, text=f'The {side} wall was breached'))


def _child(g: dict, suffix: str, count: int, hp_share: float, t: float, name: str) -> dict:
    c = group(dict(id=f"{g['id']}.{suffix}", side=g['side'], arch=g['arch'], tier='normal', rank=1, count=count,
                   hp=g['hp'] * hp_share, speed=g['speed'], reach=g['reach'], wall_dps=g['wall_dps'] * hp_share,
                   keep_dps=g['keep_dps'] * hp_share, resist=g['resist'], delay=t, loot=False, name=name))
    c['spawned'] = True
    c['pos'] = g['pos'] if g['pos'] is not None else 0.0
    c['inside'] = g['inside']
    if 'flying' in g['flags']:
        c['flags'].add('flying')      # a flyer's spawn flies on (over the town, it would otherwise be put back outside)
    return c


def fight(wave: dict, town: dict) -> dict:
    """Resolve one wave.

    ``town``: walls {side: {hp, max, armor}}, keep {hp, max, dps}, towers and
    heroes (lists of shooters: id, side, dps, dtype, reach, min_reach, targets,
    air, ground, slow, pierce, dispel, priority; heroes also mobile, roam, hold).
    ``wave``: groups (see ``group``). Neither is modified. Returns kills per
    group (and who made them), the walls and keep afterwards, and highlights.
    """
    walls = {}
    for s in SIDES:
        spec = (town.get('walls') or {}).get(s) or {}
        walls[s] = dict(hp=max(0.0, _num(spec.get('hp'))), max=max(0.0, _num(spec.get('max'))),
                        armor=min(0.9, max(0.0, _num(spec.get('armor')))))
    kspec = town.get('keep') or {}
    keep = dict(hp=max(0.0, _num(kspec.get('hp'), 1.0)), max=max(1.0, _num(kspec.get('max'), 1.0)), dps=max(0.0, _num(kspec.get('dps'))))
    towers = sorted((dict(x, mobile=False, roam=False) for x in town.get('towers') or ()), key=lambda x: str(x['id']))
    heroes = sorted((dict(x, mobile=True) for x in town.get('heroes') or ()), key=lambda x: str(x['id']))
    keep_gun = dict(id='keep', side='keep', dtype='physical', reach=5.0, air=True, ground=True, mobile=True)
    groups = sorted((group(g) for g in wave.get('groups') or ()), key=lambda g: (g['delay'], g['id']))
    breached = {s: walls[s]['max'] > 0 and walls[s]['hp'] <= 0 for s in SIDES}
    for s in SIDES:
        if walls[s]['max'] <= 0:
            breached[s] = True      # no wall on that side at all
    highlights, deaths = [], []
    seq = 0
    t = 0.0
    while t < MAX_SECONDS:
        for g in groups:
            if not g['spawned'] and g['delay'] <= t:
                g['spawned'], g['pos'] = True, SPAWN_DISTANCE
        live = [g for g in groups if _live(g)]
        if not live and all(g['spawned'] for g in groups):
            break
        # ---- move
        born = []
        for g in live:
            if g['inside']:
                continue
            speed = g['speed'] * (1.0 - (g['slow'] if g['slow_until'] >= t else 0.0))
            if 'flying' in g['flags'] or 'thief' in g['flags']:
                g['pos'] -= speed * STEP
                if g['pos'] <= -KEEP_DISTANCE:
                    g['inside'] = True
                continue
            open_side = breached[g['side']]
            stop = 0.0 if open_side or g['reach'] <= 0 else g['reach']
            g['pos'] = max(stop, g['pos'] - speed * STEP)
            if g['pos'] > stop + 1e-9:
                continue
            if open_side:
                g['inside'] = True
                continue
            g['at_wall'] += STEP
            melee = g['reach'] <= 0
            if 'teleporting' in g['flags'] and melee and not g['teleported']:
                g['teleported'] = True
                jump = int(g['alive'] * TELEPORT_SHARE)
                if jump:
                    seq += 1
                    inner = dict(g, id=f"{g['id']}.t{seq}", n=jump, alive=jump, pool=jump * g['hp'], shield=g['shield_each'] * jump,
                                 inside=True, killed_by={}, flags=set(g['flags']), resist=dict(g['resist']), affixes=list(g['affixes']))
                    g['n'] -= jump
                    g['alive'] -= jump
                    g['pool'] -= jump * g['hp']
                    g['shield'] = min(g['shield'], g['shield_each'] * g['alive'])
                    born.append(inner)
                    highlights.append(dict(t=t, side=g['side'], text=f"{jump} {g['name']} blinked past the {g['side']} wall"))
            if 'summoner' in g['flags'] and melee and not g['summoned'] and g['alive'] > 0:
                g['summoned'] = True
                seq += 1
                born.append(_child(g, f's{seq}', g['alive'] * SUMMON_PER_MONSTER, 0.10, t, 'Summoned minion'))
                highlights.append(dict(t=t, side=g['side'], text=f"{g['name']} called {g['alive'] * SUMMON_PER_MONSTER} minions"))
            if 'burrowing' in g['flags'] and melee and g['at_wall'] >= BURROW_SECONDS and g['alive'] > 0:
                g['inside'] = True
                highlights.append(dict(t=t, side=g['side'], text=f"{g['alive']} {g['name']} dug under the {g['side']} wall"))
        groups.extend(born)
        for g in live:
            if 'thief' in g['flags'] and g['inside']:
                g['inside_for'] += STEP
                if g['inside_for'] >= THIEF_ESCAPE:
                    g['escaped'] += g['alive']
                    highlights.append(dict(t=t, side=g['side'], text=f"{g['alive']} {g['name']} escaped with their loot"))
                    g['alive'] = 0
                    g['pool'] = 0.0
                    g['shield'] = 0.0
        live = [g for g in groups if _live(g)]
        # ---- shoot
        chill = {s: any('frozen' in g['flags'] and g['side'] == s and not g['inside'] and _position(g) <= FROZEN_RANGE for g in live)
                 for s in SIDES}
        for h in heroes:
            if h.get('roam'):
                h['side'] = _roam_side(live, walls, h.get('side') if h.get('side') in SIDES else 'north')
        for src in towers + heroes:
            if src.get('side') not in SIDES and src.get('side') != 'keep':
                continue
            target = _pick(live, src)
            if target is None:
                continue
            dps = max(0.0, _num(src.get('dps')))
            if not src['mobile'] and src['side'] in SIDES and chill[src['side']]:
                dps *= 1.0 - FROZEN_AURA
            hits = min(max(1, int(_num(src.get('targets'), 1))), target['alive'])
            _damage(target, dps * STEP * hits, src.get('dtype', 'physical'), src, t, deaths)
            _slow(target, _num(src.get('slow')), t)
        if keep['dps'] > 0:
            target = _pick([g for g in live if g['inside']], keep_gun)
            if target is not None:
                _damage(target, keep['dps'] * STEP, 'physical', keep_gun, t, deaths)
        # ---- deaths: splitters leave children, exploders hurt the wall
        for g, dead, when in deaths:
            if 'splitting' in g['flags'] and g['loot']:
                seq += 1
                groups.append(_child(g, f'c{seq}', dead * 2, SPLIT_HP, when, f"{g['name']} spawn"))
            if ('exploding' in g['flags'] and 'flying' not in g['flags'] and not g['inside'] and g['pos'] is not None
                    and g['pos'] <= EXPLODE_RANGE):
                _hit_wall(walls, g['side'], dead * g['hp'] * EXPLODE_WALL, highlights, when, breached)
        deaths.clear()
        live = [g for g in groups if _live(g)]
        # ---- heal
        for g in live:
            attacking = g['inside'] or ('flying' not in g['flags'] and g['pos'] <= (g['reach'] if not breached[g['side']] else 0.0) + 1e-9)
            heal = 0.0
            if 'regenerating' in g['flags'] and g['no_regen_until'] < t:
                heal += REGEN_PER_SECOND
            if 'vampiric' in g['flags'] and attacking:
                heal += VAMPIRIC_PER_SECOND
            if heal:
                g['pool'] = min(g['alive'] * g['hp'], g['pool'] + heal * g['hp'] * g['alive'] * STEP)
        # ---- monsters hit the walls and the keep
        for g in live:
            mult = ENRAGE_MULT if ('enraged' in g['flags'] and g['pool'] < ENRAGE_BELOW * g['n'] * g['hp']) else 1.0
            if 'juggernaut' in g['flags']:
                mult *= JUGGERNAUT_MULT
            if 'thief' in g['flags']:
                continue
            if g['inside']:
                keep['hp'] -= g['alive'] * g['keep_dps'] * mult * STEP
            elif 'flying' not in g['flags'] and not breached[g['side']] and g['pos'] <= g['reach'] + 1e-9:
                w = walls[g['side']]
                amount = g['alive'] * g['wall_dps'] * mult * STEP * (1.0 - w['armor']) * (1.0 - _hold(heroes, g['side']))
                _hit_wall(walls, g['side'], amount, highlights, t, breached)
        if keep['hp'] <= 0:
            keep['hp'] = 0.0
            highlights.append(dict(t=t, side=None, text='The keep fell'))
            t += STEP
            break
        t += STEP
    for g in groups:
        if g['spawned'] and g['alive'] > 0:
            g['leaked'] = g['alive']
        elif not g['spawned']:
            g['leaked'] = g['alive']
    return _result(groups, walls, keep, highlights, t, breached)


def _result(groups, walls, keep, highlights, t, breached):
    kills = []
    for g in groups:
        g['leaked'] += g['escaped']
        dead = g['n'] - g['alive'] - g['escaped']
        if dead <= 0 and not g['leaked']:
            continue
        kills.append(dict(group=g['id'], name=g['name'], arch=g['arch'], tier=g['tier'], rank=g['rank'], side=g['side'],
                          drops=g['drops'] if g['loot'] else 0, loot=g['loot'], boss=g['boss'], affixes=list(g['affixes']),
                          killed=dead, leaked=g['leaked'], by=dict(sorted(g['killed_by'].items()))))
    return dict(seconds=t, kills=kills,
                walls={s: dict(hp=round(w['hp'], 3), max=w['max'], breached=breached[s]) for s, w in walls.items()},
                keep=dict(hp=round(max(0.0, keep['hp']), 3), max=keep['max'], fallen=keep['hp'] <= 0),
                highlights=highlights[:40])
