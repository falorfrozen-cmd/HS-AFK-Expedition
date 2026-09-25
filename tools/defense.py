"""Town defense (0.9): the monsters of a region besiege the town.

The player picks a region the bestiary knows, a level and a length. Every
WAVE_MINUTES a wave marches on the town from one to four sides. The town
answers with:
- its walls, keep and towers (fortifications.py);
- up to three stationed heroes. Headquarters sets how many; each fights with
  its own measured kill pace from a calibration in that region.
battle.py resolves each wave. The walls carry their damage from wave to
wave; masons and the siege's stone budget mend them in between. The siege ends
when its time is up, when the keep falls, or when the player sounds the
retreat.

Every monster group is one recorded monster, many times over:
- one packet the plugin captured when the player killed it in the game;
- with that monster's own name, rank, speed, range, immunities and elite
  affixes.
So a pack of Fallen Angel Legions is that very Fallen Angel Legion, and each
kill is paid out later by replaying its packet through the game's drop routine,
in the region where it was recorded. Special waves bring the monsters that the
game's special content spawned there: the Abyss chest's pack, the Unholy
Siege's, a Chaos Pillar's (see bestiary.py).

What is AFK FARM's own layer: the fight itself; the tiers above the game's
Legion (Ascended, Primordial, Warlord); what an affix or an event does in the
fight; the spoils; and the level bonus to Magic Find. None of it creates
anything.

Who gets the kills:
- **A stationed hero's kills** are that hero's claim (items, experience, gold),
  like an expedition's. They replay packets from its own calibration, which
  carry its native experience: the very packet when it recorded that one,
  otherwise one of its own packets of the same monster and rank.
- **Everything else** is the town's share: the towers', the keep's, and a
  hero's kills of monsters it never met in its calibration. Any offline hero
  standing in the region collects it, with no experience, like a worker's haul.

Timeline rules:
- The timeline is drawn once from the stored seed.
- Only waves that are over can be seen; the future never shows.
- A repair or a retreat re-draws only the waves after it. The waves are seeded
  one by one, so an unchanged wave comes out the same.

Standard library only.
"""
from __future__ import annotations

import math
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import afk
import battle
import bestiary
import economy
import fortifications as F

VERSION = 1
WAVE_MINUTES = 5
SPAWN_WINDOW = 200.0            # groups arrive over the first 200 seconds of a wave, so each has time to fight
MAX_LEVEL = 60
LEVEL_STEP = 1.15
GROWTH = 1.03
BASE_BUDGET = 30.0              # region units of health in the first wave at level 1
MAX_BODIES = 4000               # past this a wave grows tougher, not bigger
MAX_GROUPS = 14
MIN_HOURS, MAX_HOURS = 0.25, 8.0
MF_BONUS_PER_LEVEL = 0.02
ELITE_EVERY, SPECIAL_EVERY, GOBLIN_EVERY, WARLORD_EVERY = 5, 7, 10, 25
SPEED_SCALE = 2.5               # the game's moveSpeed (median 2.8) to model units per second
MIN_SPEED, MAX_SPEED = 2.0, 22.0
RANGED_REACH = 25.0
THIEF_SPEED = 16.0
WALL_DAMAGE = 2.0               # wall damage per second of one Common monster (AFK FARM's layer)
SPOILS_PER_KILLS = 20           # one spoil for the camp per 20 kills (AFK FARM's layer)
WARLORD_SHARE = 0.35            # a warlord wave's boss holds this share of the wave's health

# A rank next to rank 1: the medians over 41-48 pairs of recorded packets of the
# same monster object in the same room (MEASURED 2026-09-25): health and damage.
RANK_HP = {1: 1.0, 2: 1.84, 3: 2.98, 4: 4.23}
RANK_DAMAGE = {1: 1.0, 2: 1.27, 3: 1.53, 4: 1.90}
RANK_NAMES = bestiary.RANK_NAMES
# Above the game's Legion: AFK FARM's own tiers of rank-4 monsters. Each of
# their drops is one more replay of the same Legion packet.
TIERS = (
    dict(key='normal', name='', hp=1.0, wall=1.0, extra_affixes=0, drops=1, speed=1.0),
    dict(key='ascended', name='Ascended', hp=2.5, wall=1.6, extra_affixes=1, drops=2, speed=1.05, level=15),
    dict(key='primordial', name='Primordial', hp=6.0, wall=2.5, extra_affixes=2, drops=3, speed=1.1, level=30),
    dict(key='warlord', name='Warlord', hp=30.0, wall=6.0, extra_affixes=3, drops=5, speed=0.9),
)
TIER_BY_KEY = {t['key']: t for t in TIERS}
# Monsters that fly over walls in the town's model (AFK FARM's layer, by name).
FLYERS = ('wasp', 'bee', 'imp', 'levitating', 'spirit', 'ghost', 'shade', 'chilling_head', 'bat', 'harpy', 'wisp', 'dragon',
          'raven', 'crow', 'moth', 'gargoyle', 'wraith')
SPECIAL_WAVES = dict(abyss='Abyssal Incursion', unholy='Unholy Siege', pillar='Chaos Pillars', special='Marked Host')

# The game's elite affixes (runtime affix index -> name, from ForgePact's live
# checks; 22-24 and 27-29 are not identified yet) and what each does in the
# town's model: health, speed and wall damage multipliers, resistances and
# behaviours. The effects are AFK FARM's layer; the game ships no descriptions.
_ELEMENTS = ('fire', 'cold', 'lightning', 'poison')
AFFIXES: dict[int, dict] = {
    0: dict(name='Champion', hp=1.2),
    1: dict(name='Fractal', flags=('splitting',)),
    2: dict(name='Raging', wall=1.3),
    3: dict(name='Enraged', flags=('enraged',)),
    4: dict(name='Haunted', flags=('summoner',)),
    5: dict(name='Vampiric', flags=('vampiric',)),
    6: dict(name='Burst Shot', wall=1.2),
    7: dict(name='Possessed', flags=('splitting',)),
    8: dict(name='Extra Fast', speed=1.5),
    9: dict(name='Extra Strong', wall=1.5),
    10: dict(name='Stoneskin', resist=dict(physical=0.5)),
    11: dict(name='Cold Enchanted', resist=dict(cold=0.75), flags=('frozen',)),
    12: dict(name='Fire Enchanted', resist=dict(fire=0.75), flags=('exploding',)),
    13: dict(name='Lightning Enchanted', resist=dict(lightning=0.75)),
    14: dict(name='Magic Resistant', resist={e: 0.3 for e in _ELEMENTS}),
    15: dict(name='Manaburn', wall=1.15),
    16: dict(name='Multishot', wall=1.3),
    17: dict(name='Treasure Gobbler', hp=1.1),
    18: dict(name="Arcana's Curse", flags=('shielded',)),
    19: dict(name='Venomous', resist=dict(poison=0.75), wall=1.2),
    20: dict(name='Punisher', hp=1.1, wall=1.3),
    21: dict(name='Fallen Angel', hp=1.3, resist=dict(holy=0.5), flags=('regenerating',)),
    25: dict(name='Pyromaniac', resist=dict(fire=0.5), wall=1.2),
    26: dict(name='Berserker', flags=('enraged',)),
    30: dict(name='Thick Skin', hp=1.3),
    31: dict(name='Antimagus', resist={**{e: 0.4 for e in _ELEMENTS}, 'holy': 0.4}),
    32: dict(name='Colossal', hp=2.0, speed=0.8),
    33: dict(name='Stealthy', flags=('teleporting',)),
    34: dict(name='Time Lapsing', flags=('teleporting',)),
    35: dict(name='Wasped', flags=('summoner',)),
    36: dict(name='Blazing', flags=('exploding',)),
    37: dict(name='Thunder Caller', wall=1.2),
    38: dict(name='Meteoric', wall=1.4),
}
UNKNOWN_AFFIX = dict(name='Unnamed affix', hp=1.1)

EVENTS = (
    dict(key='blood_moon', name='Blood Moon', text='Monsters run 25% faster and hit the walls 15% harder this wave.', weight=3),
    dict(key='fog', name='Thick Fog', text='Towers reach 20% less far this wave.', weight=2),
    dict(key='rally', name='Rally', text='Stationed heroes fight 25% harder this wave.', weight=2),
    dict(key='supply', name='Supply Cart', text='The masons mend twice as much after this wave.', weight=2),
    dict(key='bounty', name='Bounty', text='A marked group: destroy it to the last monster for bonus spoils.', weight=2),
)
EVENT_BY_KEY = {e['key']: e for e in EVENTS}
EVENT_CHANCE, EVENT_FROM = 0.22, 3
BOUNTY_SPOILS = 40

# A melee hero steps out far enough to reach monsters shooting at its wall (30);
# a ranged one covers its side's approach. AFK FARM's layer, by class.
CLASS_ROLES = {
    # damage type, reach, targets, hits flyers, hold (share of wall damage a melee hero blocks)
    'Viking': ('physical', 30, 3, False, 0.20), 'Pyromancer': ('fire', 45, 4, True, 0.0), 'Marksman': ('physical', 55, 2, True, 0.0),
    'Pirate': ('physical', 45, 2, True, 0.0), 'Nomad': ('physical', 30, 3, False, 0.15), 'Redneck': ('physical', 40, 3, True, 0.0),
    'Necromancer': ('poison', 40, 4, True, 0.10), 'Samurai': ('physical', 30, 3, False, 0.15), 'Paladin': ('holy', 30, 3, False, 0.25),
    'Amazon': ('physical', 50, 2, True, 0.0), 'Demon Slayer': ('physical', 30, 2, False, 0.15), 'Demonspawn': ('fire', 30, 4, False, 0.15),
    'Shaman': ('lightning', 45, 4, True, 0.0), 'White Mage': ('holy', 45, 3, True, 0.0), 'Marauder': ('physical', 30, 3, False, 0.20),
    'Plague Doctor': ('poison', 40, 4, True, 0.0), 'Shield Lancer': ('physical', 30, 2, False, 0.30), 'Illusionist': ('lightning', 45, 3, True, 0.0),
    'Jotunn': ('cold', 30, 3, False, 0.25), 'Exo': ('lightning', 50, 3, True, 0.0), 'Butcher': ('physical', 30, 3, False, 0.20),
    'Stormweaver': ('lightning', 45, 4, True, 0.0), 'Bard': ('physical', 40, 3, True, 0.0), 'Prophet': ('holy', 45, 3, True, 0.0),
}
DEFAULT_ROLE = ('physical', 30, 3, False, 0.15)


# ------------------------------------------------------------------ small helpers
def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(text):
    return datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def wave_count(hours: float) -> int:
    return max(0, int(math.floor(float(hours) * 60.0 / WAVE_MINUTES + 1e-9)))


def duration_text(hours: float) -> str:
    minutes = int(round(float(hours) * 60))
    return f'{minutes} minutes' if minutes < 60 else f'{minutes / 60:g} hours'


def budget(level: int, wave: int) -> float:
    return BASE_BUDGET * LEVEL_STEP ** (level - 1) * GROWTH ** (wave - 1)


def is_flyer(entry: dict) -> bool:
    key = str(entry.get('monster_key', '')).lower()
    return any(word in key for word in FLYERS)


def affix(affix_id: int) -> dict:
    spec = AFFIXES.get(int(affix_id))
    return spec if spec else dict(UNKNOWN_AFFIX, name=f"{UNKNOWN_AFFIX['name']} {int(affix_id)}")


# ------------------------------------------------------------------ the roster a siege draws from
def roster(entries: dict) -> dict:
    """The frozen roster of a siege: the region's bestiary entries as they are now,
    each with its recorded packets, their weights and their affixes (aligned)."""
    out = {}
    for key, e in sorted(entries.items()):
        rank = int(e['rank'])
        out[key] = dict(key=key, monster_key=e['monster_key'], rank=rank, origin=e.get('origin', 'ordinary'), name=e['name'],
                        hp=float(RANK_HP[rank]), speed=float(e.get('speed') or 2.8), ranged=bool(e.get('ranged')),
                        immune=list(e.get('immune') or ()), flyer=is_flyer(e), packets=list(e['packets']),
                        weights=[float(w) for w in e['weights']], affix_sets=[list(s) for s in e['affix_sets']], weight=float(e['weight']))
    return out


def rank_weights(level: int, kind: str, present) -> dict:
    """How often each rank marches at this level: the rarer ranks grow with it."""
    w = {1: max(5.0, 60.0 - 1.2 * level), 2: 25.0 + 0.4 * level, 3: 10.0 + 0.8 * level, 4: 3.0 + 0.9 * level}
    if kind in ('elite', 'warlord', 'final'):
        w[1], w[2] = 0.0, w[2] * 0.3
    out = {r: v for r, v in w.items() if r in present and v > 0}
    return out or {r: 1.0 for r in present}


def pick_sides(rand, level: int, kind: str) -> list[str]:
    n = 1 + (level >= 8) + (level >= 20) + (level >= 35)
    if kind in ('warlord', 'final'):
        n = 4
    sides = list(battle.SIDES)
    rand.shuffle(sides)
    return sorted(sides[:n], key=battle.SIDES.index)


def specials(ros: dict) -> list[str]:
    return sorted({e['origin'] for e in ros.values()} - {'ordinary'})


def wave_kind(wave: int, total: int, goblins: bool, special: list[str]) -> str:
    if wave == total and total >= 6:
        return 'final'
    if wave % WARLORD_EVERY == 0:
        return 'warlord'
    if wave % GOBLIN_EVERY == 0 and goblins:
        return 'goblin'
    if wave % SPECIAL_EVERY == 0 and special:
        return 'special:' + special[(wave // SPECIAL_EVERY - 1) % len(special)]
    if wave % ELITE_EVERY == 0:
        return 'elite'
    return 'normal'


def _pool_for(ros: dict, kind: str) -> dict:
    """The entries a wave draws from: a special wave its origin's, the others the ordinary ones."""
    if kind.startswith('special:'):
        origin = kind.split(':', 1)[1]
        return {k: e for k, e in ros.items() if e['origin'] == origin}
    ordinary = {k: e for k, e in ros.items() if e['origin'] == 'ordinary'}
    return ordinary or ros


def make_wave(ros: dict, level: int, wave: int, total: int, seed: int, goblins: dict | None = None) -> dict:
    """One wave's groups, drawn from the frozen roster with the wave's own seed."""
    rand = economy.rng('defense-wave', seed, wave)
    kind = wave_kind(wave, total, bool(goblins), specials(ros))
    sides = pick_sides(rand, level, kind)
    pool = _pool_for(ros, kind)
    present = sorted({e['rank'] for e in pool.values()})
    weights = rank_weights(level, kind, present)
    affix_pool = sorted({a for e in ros.values() for s in e['affix_sets'] for a in s})
    money = budget(level, wave) * {'final': 2.0, 'warlord': 1.3, 'elite': 1.2}.get(kind, 1.3 if kind.startswith('special:') else 1.0)
    n_groups = min(MAX_GROUPS, 3 + level // 5 + (2 if kind in ('warlord', 'final') else 0))
    shares = [rand.uniform(0.5, 1.5) for _ in range(n_groups)]
    total_share = sum(shares)
    groups = []
    for i, share in enumerate(shares):
        rank = economy.weighted(rand, sorted(weights.items()))
        entry = pool[economy.weighted(rand, [(k, e['weight']) for k, e in sorted(pool.items()) if e['rank'] == rank])]
        tier = TIER_BY_KEY['normal']
        if rank == 4:
            for t in (TIER_BY_KEY['primordial'], TIER_BY_KEY['ascended']):
                if level >= t['level'] and rand.random() < min(0.6, (level - t['level'] + 1) * 0.02):
                    tier = t
                    break
        groups.append(_group(f'w{wave}g{i}', entry, tier, money * share / total_share, sides, rand, affix_pool, i, n_groups))
    if kind in ('warlord', 'final'):
        top = max(present)
        entry = pool[economy.weighted(rand, [(k, e['weight']) for k, e in sorted(pool.items()) if e['rank'] == top])]
        count = 1 + level // 20
        boss = _group(f'w{wave}boss', entry, TIER_BY_KEY['warlord'], 0.0, sides, rand, affix_pool, 0, 1, count=count)
        # A warlord grows with the wave: WARLORD_SHARE of its health, split between the warlords
        # (never less than twice its rank's health). Its wall damage grows as the square root.
        base_hp = boss['hp']
        boss['hp'] = max(RANK_HP[top] * 2.0, WARLORD_SHARE * money / count)
        scale = math.sqrt(boss['hp'] / base_hp)
        boss['wall_dps'] *= scale
        boss['keep_dps'] *= scale
        boss['shield'] = boss['hp'] * 0.4 if 'shielded' in boss['flags'] else 0.0
        boss.update(boss=True, name=f"Warlord {entry['name']}")
        groups.append(boss)
    if kind == 'goblin':
        for gk, hashes in sorted((goblins or {}).items()):
            if hashes:
                groups.append(dict(id=f'w{wave}gob_{gk}', entry=f'goblin:{gk}', packet=rand.choice(sorted(hashes)), arch='goblin', tier='normal',
                                   rank=5, name=f'{gk.title()} Goblin', count=1 + level // 10, hp=RANK_HP[2] * 2, shield=0.0,
                                   speed=THIEF_SPEED, reach=0.0, wall_dps=0.0, keep_dps=0.0, resist={}, flags=['thief'], affixes=[],
                                   delay=round(rand.uniform(20.0, 120.0), 2), side=rand.choice(sides), drops=1, loot=True, boss=False))
    bodies = sum(g['count'] for g in groups if not g.get('boss') and g['arch'] != 'goblin')
    if bodies > MAX_BODIES:
        # A tougher wave, not a bigger one: the same health in fewer, stronger monsters.
        factor = bodies / MAX_BODIES
        for g in groups:
            if g.get('boss') or g['arch'] == 'goblin':
                continue
            g['count'] = max(1, int(round(g['count'] / factor)))
            g['hp'] *= factor
            g['shield'] *= factor
            g['wall_dps'] *= math.sqrt(factor)
            g['keep_dps'] *= math.sqrt(factor)
    event = None
    if wave >= EVENT_FROM and kind == 'normal' and rand.random() < EVENT_CHANCE:
        event = economy.weighted(rand, [(e['key'], e['weight']) for e in EVENTS])
    bounty = rand.choice([g['id'] for g in groups if g['arch'] != 'goblin']) if event == 'bounty' else None
    if event == 'blood_moon':
        for g in groups:
            g['speed'] = min(MAX_SPEED, g['speed'] * 1.25)
            g['wall_dps'] *= 1.15
            g['keep_dps'] *= 1.15
    title = SPECIAL_WAVES.get(kind.split(':', 1)[1]) if kind.startswith('special:') else None
    return dict(wave=wave, kind=kind, title=title, sides=sides, event=event, bounty=bounty, groups=groups)


def _group(ident, entry, tier, health, sides, rand, affix_pool, i, n, count=None) -> dict:
    """A group of one recorded monster: its packet, its affixes, AFK FARM's tier."""
    pick = economy.weighted(rand, list(enumerate(entry['weights'])))
    affixes = sorted(set(entry['affix_sets'][pick]))
    spare = [a for a in affix_pool if a not in affixes]
    rand.shuffle(spare)
    affixes = sorted(affixes + spare[:tier['extra_affixes']])
    rank = entry['rank']
    hp = entry['hp'] * tier['hp']
    speed = entry['speed'] * SPEED_SCALE * tier['speed']
    wall = WALL_DAMAGE * RANK_DAMAGE[rank] * tier['wall']
    resist = {k: 1.0 for k in entry['immune']}
    flags = {'flying'} if entry['flyer'] else set()
    for a in affixes:
        spec = affix(a)
        hp *= spec.get('hp', 1.0)
        speed *= spec.get('speed', 1.0)
        wall *= spec.get('wall', 1.0)
        for dtype, value in (spec.get('resist') or {}).items():
            resist[dtype] = min(1.0, resist.get(dtype, 0.0) + value)
        flags.update(spec.get('flags') or ())
    n_count = count if count is not None else max(1, int(round(health / max(hp, 1e-9))))
    delay = 0.0 if i == 0 else rand.uniform(0.0, SPAWN_WINDOW) * (i / max(1, n - 1)) ** 0.5
    prefix = ' '.join(x for x in (tier['name'], bestiary.ORIGIN_NAMES.get(entry['origin'], '')) if x)
    return dict(id=ident, entry=entry['key'], packet=entry['packets'][pick], arch=entry['key'], tier=tier['key'], rank=rank,
                name=(prefix + ' ' if prefix else '') + entry['name'], count=n_count, hp=hp, shield=hp * 0.4 if 'shielded' in flags else 0.0,
                speed=max(MIN_SPEED, min(MAX_SPEED, speed)), reach=RANGED_REACH if entry['ranged'] else 0.0,
                wall_dps=wall, keep_dps=wall, resist=resist, flags=sorted(flags), affixes=affixes, delay=round(delay, 2),
                side=rand.choice(sides), drops=tier['drops'], loot=True, boss=False)


# ------------------------------------------------------------------ the defenders
def hero_shooter(slot: int, character: dict, profile: dict, stance: str = 'roam') -> dict:
    """A stationed hero as a battle shooter. Its measured kills per minute, rank
    by rank, times each rank's health is the health it clears per second."""
    pace = float(profile.get('kills_per_min') or 0)
    if not pace > 0:
        raise ValueError('This calibration has no kill pace.')
    counts = {}
    for q in profile.get('packets') or []:
        if q.get('kind') == 'kill' and q.get('rank') in RANK_HP:
            counts[q['rank']] = counts.get(q['rank'], 0.0) + float(q.get('count') or 0)
    total = sum(counts.values())
    mean_hp = sum(RANK_HP[r] * n for r, n in counts.items()) / total if total else 1.0
    dtype, reach, targets, air, hold = CLASS_ROLES.get(character.get('class_name') or '', DEFAULT_ROLE)
    return dict(id=f'hero:{slot}', slot=slot, name=character.get('name') or f'Hero {slot}', class_name=character.get('class_name'),
                dps=round(pace * mean_hp / 60.0, 6), pace=round(pace, 3), mean_hp=round(mean_hp, 4), dtype=dtype, reach=float(reach),
                min_reach=0.0, targets=targets, air=air, ground=True, hold=hold,
                side=stance if stance in battle.SIDES else 'north', roam=stance not in battle.SIDES, priority='elite')


def snapshot(town: dict, camp_levels: dict) -> dict:
    """The fortifications as they stand, frozen for a siege."""
    walls_level = max(0, min(5, int(camp_levels.get('walls', 0))))
    hq = max(1, min(5, int(camp_levels.get('hq', 1))))
    plating = town.get('plating') or {}
    walls = {s: dict(max=F.wall_max(walls_level, int(plating.get(s, 0))), armor=F.wall_armor(walls_level, int(plating.get(s, 0))))
             for s in battle.SIDES}
    towers = [F.tower_stats(t) for t in sorted((town.get('towers') or {}).values(), key=lambda t: t['id']) if int(t.get('level', 0)) >= 1]
    return dict(walls_level=walls_level, hq=hq, walls=walls, keep=F.keep(hq), towers=towers, masons=F.MASONS[walls_level])


def town_for(record: dict, state: dict, wave: dict) -> dict:
    """The battle's town for one wave: the frozen fortifications, the walls as they stand, the event."""
    snap = record['town']
    event = wave.get('event')
    towers = [dict(t, reach=t['reach'] * (0.8 if event == 'fog' else 1.0)) for t in snap['towers']]
    heroes = [dict(h['shooter'], dps=h['shooter']['dps'] * (1.25 if event == 'rally' else 1.0)) for h in record['heroes']]
    return dict(walls={s: dict(hp=state['walls'][s], max=snap['walls'][s]['max'], armor=snap['walls'][s]['armor']) for s in battle.SIDES},
                keep=dict(hp=state['keep'], max=snap['keep']['max'], dps=snap['keep']['dps']), towers=towers, heroes=heroes)


# ------------------------------------------------------------------ the siege, wave by wave
def _mend(record: dict, state: dict, event, stone_left: int) -> tuple[int, dict, int]:
    """Between waves the masons mend every wall and the keep (twice as much after a
    Supply Cart); then the stone budget tops the most hurt walls up."""
    snap = record['town']
    share = snap['masons'] * (2.0 if event == 'supply' else 1.0)
    mended = {}
    for s in battle.SIDES:
        top = snap['walls'][s]['max']
        if top <= 0:
            continue
        before = state['walls'][s]
        state['walls'][s] = min(top, before + top * share)
        mended[s] = state['walls'][s] - before
    state['keep'] = min(snap['keep']['max'], state['keep'] + snap['keep']['max'] * share)
    used = 0
    order = sorted((s for s in battle.SIDES if snap['walls'][s]['max'] > 0), key=lambda s: (state['walls'][s] / snap['walls'][s]['max'], s))
    for s in order:
        top = snap['walls'][s]['max']
        spend = min(F.repair_stone(top - state['walls'][s]), stone_left - used)
        if spend > 0:
            state['walls'][s] = min(top, state['walls'][s] + spend * F.STONE_PER_REPAIR)
            mended[s] = mended.get(s, 0.0) + spend * F.STONE_PER_REPAIR
            used += spend
    return stone_left - used, mended, used


def _row(wave: dict, result: dict) -> dict:
    """What a wave leaves behind: drops per defender and packet, the walls, highlights."""
    by_id = {g['id']: g for g in wave['groups']}
    drops, groups = {}, {}
    kills = leaked = 0
    for k in result['kills']:
        root = k['group'] if k['group'] in by_id else k['group'].split('.')[0]
        spec = by_id.get(root) or {}
        if k['loot'] and spec.get('packet'):
            kills += k['killed']
            for who, n in k['by'].items():
                if n > 0:
                    per = drops.setdefault(who, {})
                    per[spec['packet']] = per.get(spec['packet'], 0) + n * k['drops']
        leaked += k['leaked']
        if root not in groups:
            groups[root] = dict(id=root, name=spec.get('name', k['name']), entry=spec.get('entry'), tier=spec.get('tier', k['tier']),
                                rank=spec.get('rank', k['rank']), side=spec.get('side', k['side']),
                                affixes=[affix(a)['name'] for a in spec.get('affixes', [])], flags=list(spec.get('flags', [])),
                                count=spec.get('count', 0), killed=0, leaked=0, boss=bool(spec.get('boss')))
        g = groups[root]
        if k['loot']:
            g['killed'] += k['killed']
        g['leaked'] += k['leaked']
    bounty_done = False
    if wave.get('bounty') in groups:
        g = groups[wave['bounty']]
        bounty_done = g['killed'] >= g['count'] > 0 and g['leaked'] == 0
    return dict(wave=wave['wave'], kind=wave['kind'], title=wave.get('title'), sides=wave['sides'], event=wave.get('event'),
                bounty=wave.get('bounty'), bounty_done=bounty_done, kills=kills, leaked=leaked, drops=drops,
                groups=sorted(groups.values(), key=lambda g: g['id']),
                walls={s: w['hp'] for s, w in result['walls'].items()}, breached=[s for s, w in result['walls'].items() if w['breached']],
                keep=result['keep']['hp'], fell=result['keep']['fallen'], seconds=result['seconds'], highlights=result['highlights'][:12],
                spoils=kills // SPOILS_PER_KILLS + (BOUNTY_SPOILS if bounty_done else 0))


def retreat_wave(record: dict):
    return next((i['after_wave'] for i in record.get('interventions', []) if i['kind'] == 'retreat'), None)


def end_wave(record: dict) -> int:
    """The last wave of the siege: the keep's fall, a retreat, or the planned end."""
    last = record['waves_total']
    retreat = retreat_wave(record)
    if retreat is not None:
        last = min(last, retreat)
    fell = next((row['wave'] for row in record.get('timeline', []) if row['fell']), None)
    return min(last, fell) if fell is not None else last


def simulate(record: dict, start: int = 1) -> dict:
    """(Re)draw the waves from ``start`` on; the rows before it stay as they are."""
    total = record['waves_total']
    rows = [r for r in record.get('timeline', []) if r['wave'] < start]
    state = deepcopy(record['start_state'])
    stone = int(record['stone_budget'])
    if rows:
        state = dict(walls=dict(rows[-1]['after']['walls']), keep=rows[-1]['after']['keep'])
        stone = int(rows[-1]['after']['stone'])
    retreat = retreat_wave(record)
    for k in range(start, total + 1):
        if (retreat is not None and k > retreat) or (rows and rows[-1]['fell']):
            break
        for i in record.get('interventions', []):
            if i['kind'] == 'repair' and i['after_wave'] == k - 1:
                for s, hp in i['walls'].items():
                    state['walls'][s] = min(record['town']['walls'][s]['max'], state['walls'][s] + float(hp))
        wave = make_wave(record['roster'], record['level'], k, total, record['seed'], record.get('goblins'))
        row = _row(wave, battle.fight(wave, town_for(record, state, wave)))
        state = dict(walls=dict(row['walls']), keep=row['keep'])
        row['mended'], row['stone_used'] = {}, 0
        if not row['fell']:
            stone, mended, used = _mend(record, state, wave.get('event'), stone)
            row['mended'] = {s: round(v, 3) for s, v in mended.items() if v > 0}
            row['stone_used'] = used
        row['after'] = dict(walls={s: round(v, 3) for s, v in state['walls'].items()}, keep=round(state['keep'], 3), stone=stone)
        rows.append(row)
    record['timeline'] = rows
    return record


# ------------------------------------------------------------------ a siege's record
def new_record(ident: str, room: str, level, hours, *, town_snapshot: dict, walls_now: dict, keep_now: float, heroes: list[dict],
               entries: dict, goblins: dict | None, stone_budget: int, build: str | None, at=None, seed=None,
               region_name: str | None = None) -> dict:
    """A siege, drawn in full. ``heroes``: [{slot, character, profile, stance, expedition_id}]."""
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise ValueError(f'Choose a siege level from 1 to {MAX_LEVEL}.')
    hours = float(hours)
    if not math.isfinite(hours) or not MIN_HOURS <= hours <= MAX_HOURS:
        raise ValueError('A siege lasts between 15 minutes and 8 hours.')
    if not entries:
        raise ValueError('The bestiary knows no monster of that region yet: record a calibration there first.')
    if type(stone_budget) is not int or stone_budget < 0:
        raise ValueError('The stone budget must be a whole number of stone.')
    if not town_snapshot['towers'] and not heroes:
        raise ValueError('Nobody would defend the town: build a tower or station a hero.')
    total = wave_count(hours)
    at = at or now_utc()
    seed = seed if isinstance(seed, int) else secrets.randbits(62)
    ros = roster(entries)
    in_roster = {x for e in ros.values() for x in e['packets']}
    stationed = []
    for h in heroes:
        profile = h['profile']
        counts = {q['hash']: max(1.0, float(q.get('count') or 0)) for q in profile.get('packets') or [] if q.get('kind') == 'kill'}
        own = {x: counts[x] for x in sorted(set(counts) & in_roster)}
        stationed.append(dict(slot=h['slot'], character=h['character'], expedition_id=h['expedition_id'], profile_id=profile.get('id'),
                              stance=h.get('stance', 'roam'), shooter=hero_shooter(h['slot'], h['character'], profile, h.get('stance', 'roam')),
                              own=own))
    record = dict(schema=1, version=VERSION, id=ident, room=room, region=region_name or room, build=build, level=level,
                  hours=total * WAVE_MINUTES / 60.0,
                  waves_total=total, wave_minutes=WAVE_MINUTES, started_at=iso(at), seed=seed, town=town_snapshot,
                  start_state=dict(walls={s: float(walls_now[s]) for s in battle.SIDES}, keep=float(keep_now)),
                  stone_budget=stone_budget, roster=ros, goblins={k: sorted(v) for k, v in sorted((goblins or {}).items()) if v},
                  heroes=stationed, interventions=[], timeline=[], mf_bonus=round(1 + MF_BONUS_PER_LEVEL * level, 4),
                  town_claim=None, settled=None)
    return simulate(record)


def elapsed_waves(record: dict, at=None) -> int:
    """Waves the clock has finished (not capped by the siege's end)."""
    at = at or now_utc()
    seconds = (at - parse_iso(record['started_at'])).total_seconds()
    return max(0, int(math.floor(seconds / (record['wave_minutes'] * 60.0) + 1e-9)))


def done_waves(record: dict, at=None) -> int:
    return min(end_wave(record), elapsed_waves(record, at))


def is_over(record: dict, at=None) -> bool:
    return done_waves(record, at) >= end_wave(record)


def ends_at(record: dict):
    return parse_iso(record['started_at']) + timedelta(minutes=record['wave_minutes'] * end_wave(record))


def outcome(record: dict, at=None) -> str | None:
    """None while it runs; then 'held', 'fell' or 'retreated'."""
    if not is_over(record, at):
        return None
    last = record['timeline'][end_wave(record) - 1] if record['timeline'] else None
    if last and last['fell']:
        return 'fell'
    if retreat_wave(record) is not None and retreat_wave(record) < record['waves_total']:
        return 'retreated'
    return 'held'


def scout(record: dict, done: int, watchtower: int) -> dict | None:
    """What the Watchtower sees of the next wave: nothing at level 0, the kind and
    sides from 1, the event from 2, the monsters from 3."""
    if watchtower <= 0 or done >= end_wave(record):
        return None
    nxt = record['timeline'][done]
    out = dict(wave=nxt['wave'], kind=nxt['kind'], sides=nxt['sides'])
    if watchtower >= 2:
        out['event'] = nxt['event']
    if watchtower >= 3:
        out['groups'] = [dict((k, g[k]) for k in ('name', 'tier', 'rank', 'side', 'count', 'affixes', 'flags', 'boss')) for g in nxt['groups']]
    return out


def view(record: dict, at=None, watchtower: int = 0) -> dict:
    """What the player may see: waves that are over, never the future (the
    Watchtower scouts only the next one)."""
    at = at or now_utc()
    done = done_waves(record, at)
    fought = record['timeline'][:done]
    last = fought[-1] if fought else None
    walls = last['after']['walls'] if last else record['start_state']['walls']
    keep = last['after']['keep'] if last else record['start_state']['keep']
    over = done >= end_wave(record)
    totals = dict(kills=sum(r['kills'] for r in fought), leaked=sum(r['leaked'] for r in fought), spoils=sum(r['spoils'] for r in fought),
                  stone_used=sum(r.get('stone_used', 0) for r in fought), breaches=sum(len(r['breached']) for r in fought))
    shooters = {}
    for r in fought:
        for who, per in r['drops'].items():
            shooters[who] = shooters.get(who, 0) + sum(per.values())
    names = {t['id']: t['name'] for t in record['town']['towers']}
    names.update({h['shooter']['id']: h['shooter']['name'] for h in record['heroes']})
    names['keep'] = 'Keep'
    return dict(id=record['id'], room=record['room'], region=record['region'], level=record['level'], started_at=record['started_at'],
                ends_at=iso(ends_at(record)), waves_total=record['waves_total'], waves_done=done, wave=None if over else done + 1,
                next_wave_at=None if over else iso(parse_iso(record['started_at']) + timedelta(minutes=record['wave_minutes'] * (done + 1))),
                over=over, outcome=outcome(record, at),
                walls={s: dict(hp=round(walls[s], 1), max=record['town']['walls'][s]['max']) for s in battle.SIDES},
                keep=dict(hp=round(keep, 1), max=record['town']['keep']['max']),
                stone_left=last['after']['stone'] if last else record['stone_budget'], stone_budget=record['stone_budget'],
                totals=totals, by_defender=[dict(id=k, name=names.get(k, k), drops=v) for k, v in sorted(shooters.items(), key=lambda kv: -kv[1])],
                heroes=[dict(slot=h['slot'], name=h['shooter']['name'], class_name=h['shooter']['class_name'], stance=h['stance'],
                             dps=round(h['shooter']['dps'], 2), expedition_id=h['expedition_id']) for h in record['heroes']],
                last=[_public_row(r) for r in fought[-6:]], next=scout(record, done, watchtower),
                mf_bonus=record['mf_bonus'], town_collected=bool((record.get('town_claim') or {}).get('collected_at')))


def _public_row(r: dict) -> dict:
    return dict((k, r[k]) for k in ('wave', 'kind', 'sides', 'event', 'bounty_done', 'kills', 'leaked', 'breached', 'fell', 'spoils',
                                    'highlights', 'groups')) | dict(walls={s: round(v, 1) for s, v in r['after']['walls'].items()},
                                                                    keep=round(r['after']['keep'], 1), stone_used=r.get('stone_used', 0))


# ------------------------------------------------------------------ between waves: repair, retreat
def repair(record: dict, stone: int, at=None) -> dict:
    """Spend ``stone`` from the camp between waves on the most hurt walls. The waves
    still to come are drawn again from the walls as they then stand."""
    if type(stone) is not int or stone <= 0:
        raise ValueError('Choose how much stone to spend.')
    done = done_waves(record, at)
    if done >= end_wave(record):
        raise ValueError('The siege is over.')
    last = record['timeline'][done - 1] if done else None
    walls = dict(last['after']['walls'] if last else record['start_state']['walls'])
    for i in record['interventions']:
        if i['kind'] == 'repair' and i['after_wave'] == done:
            for s, hp in i['walls'].items():
                walls[s] = min(record['town']['walls'][s]['max'], walls[s] + hp)
    add, used = {}, 0
    tops = record['town']['walls']
    for s in sorted((s for s in battle.SIDES if tops[s]['max'] > 0), key=lambda s: (walls[s] / tops[s]['max'], s)):
        spend = min(F.repair_stone(tops[s]['max'] - walls[s]), stone - used)
        if spend > 0:
            add[s] = spend * F.STONE_PER_REPAIR
            used += spend
    if not used:
        raise ValueError('Every wall is whole.')
    record['interventions'].append(dict(kind='repair', after_wave=done, walls=add, stone=used, at=iso(at or now_utc())))
    simulate(record, done + 1)
    return dict(stone=used, walls=add, after_wave=done)


def retreat(record: dict, at=None) -> int:
    """Sound the retreat: the siege ends with the waves already fought."""
    done = done_waves(record, at)
    if done >= end_wave(record):
        raise ValueError('The siege is over.')
    record['interventions'].append(dict(kind='retreat', after_wave=done, at=iso(at or now_utc())))
    simulate(record, done + 1)
    return done


# ------------------------------------------------------------------ rewards
def shares(record: dict) -> dict:
    """Replays the whole siege earned: {'town': {hash: n}, 'heroes': {slot: {hash: n}}}.

    A hero's kill replays the very packet when its calibration recorded it, else
    its own packets of the same monster and rank (split by how often it met each);
    a kill of a monster it never met, and every other defender's, is the town's."""
    last = end_wave(record)
    entry_of = {x: key for key, e in record['roster'].items() for x in e['packets']}
    heroes = {h['shooter']['id']: h for h in record['heroes']}
    town, mine, moved = {}, {h['slot']: {} for h in record['heroes']}, {}
    for row in record['timeline'][:last]:
        for who, per in row['drops'].items():
            hero = heroes.get(who)
            for packet, n in per.items():
                if hero is not None and packet in hero['own']:
                    mine[hero['slot']][packet] = mine[hero['slot']].get(packet, 0) + n
                elif hero is not None and entry_of.get(packet) and any(entry_of.get(x) == entry_of[packet] for x in hero['own']):
                    key = (hero['slot'], entry_of[packet])
                    moved[key] = moved.get(key, 0) + n
                else:
                    town[packet] = town.get(packet, 0) + n
    for (slot, entry), n in sorted(moved.items()):
        hero = next(h for h in record['heroes'] if h['slot'] == slot)
        hashes = [x for x in sorted(hero['own']) if entry_of.get(x) == entry]
        for x, c in zip(hashes, afk.largest_remainder(n, [hero['own'][x] for x in hashes])):
            if c > 0:
                mine[slot][x] = mine[slot].get(x, 0) + c
    return dict(town=town, heroes=mine)


def hero_claim_plan(plan: dict, record: dict, claim_id: str) -> dict:
    """A stationed hero's claim: its replays of the whole siege, once the siege is over."""
    if not is_over(record):
        raise SystemExit('the town is still under siege; its heroes are paid when it ends (or sound the retreat)')
    slot = plan['defense']['slot']
    info = {p['hash']: p for p in plan['defense']['candidates']}
    counts = shares(record)['heroes'].get(slot, {})
    out = deepcopy(plan)
    out['expedition_id'] = claim_id
    out['scaled_from'] = plan['expedition_id']
    out['packets'] = [dict(info[h], count=n) for h, n in sorted(counts.items()) if h in info and n > 0]
    done = end_wave(record)
    out['scale'] = (done * record['wave_minutes'] / 60.0) / float(plan['hours']) if plan.get('hours') else 0.0
    for z in out['zones']:
        z['minutes'] = done * record['wave_minutes']
    out['defense_claim'] = dict(id=record['id'], level=record['level'], waves_fought=done, outcome=outcome(record),
                                replays=sum(p['count'] for p in out['packets']))
    afk.rebuild_preview(out)
    out['preview']['exp'] = int(sum(float(p.get('exp') or 0) * p['count'] for p in out['packets']))
    return out


def claim_from_plan(plan: dict, claim_id: str) -> dict:
    """A stationed hero's claim plan, from its expedition plan and the siege's record (afk.py claim)."""
    record = afk.read_json(Path(plan['defense']['record']))
    if not isinstance(record, dict) or record.get('id') != plan['defense']['id']:
        raise SystemExit('the siege record is missing; the hero\'s share cannot be worked out')
    return hero_claim_plan(plan, record, claim_id)


def town_plan(record: dict, character: dict, label: str, filtered: str, speed: str = 'normal', at=None) -> dict:
    """The town's share as a replay plan (planned once): the monsters' own packets
    through the game's drop routine in the siege's region, no experience."""
    at = at or now_utc()
    counts = shares(record)['town']
    index = afk.packet_index(list(counts))
    packets = [dict(hash=h, count=n, monster_key=(index.get(h) or {}).get('monster_key', ''), room=record['room'], kind='kill', exp=0.0)
               for h, n in sorted(counts.items())]
    kills = sum(p['count'] for p in packets)
    plan = dict(expedition_id=f"worker_defense_{record['id'].removeprefix('defense_')}", created=iso(at), hours=0.0, extras=[],
                rate_source='defense', defense_id=record['id'], zones=[dict(room=record['room'], weight=1, minutes=0, kills=kills, breaks=0)],
                character=character, forgepact=afk.forgepact_current(), game_build=record.get('build'), coverage=1.0,
                estimate_rates=dict(items_per_call={}, gold_per_call=None), farm_context=None, packets=packets, exp=False, gold='pickup',
                label=label, filtered_items=filtered, preview={}, planned_at=iso(at))
    afk.apply_delivery_speed(plan, speed)
    afk.rebuild_preview(plan)
    return plan


# ------------------------------------------------------------------ records
RECORDS = 'defense-records.json'


def load_records(data) -> dict:
    value = afk.read_json(Path(data) / RECORDS, {}) or {}
    return value if isinstance(value, dict) and value.get('schema') == 1 else dict(schema=1, regions={})


def record_result(data, record: dict) -> dict:
    """Remember a finished siege: the highest level each region was held at, and
    the most waves fought at each level. Returns what is new."""
    records = load_records(data)
    region = records['regions'].setdefault(record['room'], dict(held=0, waves={}))
    done, result = end_wave(record), outcome(record)
    new = dict(held=False, waves=False)
    if result == 'held' and record['level'] > int(region.get('held', 0)):
        region['held'] = record['level']
        new['held'] = True
    key = str(record['level'])
    if done > int(region['waves'].get(key, 0)):
        region['waves'][key] = done
        new['waves'] = True
    if new['held'] or new['waves']:
        afk.write_json(Path(data) / RECORDS, records)
    return new


# ------------------------------------------------------------------ what a level usually looks like
def forecast(record_like: dict, level: int, waves: int | None = None, runs: int = 6) -> dict:
    """Simulated sieges at ``level`` for this town, heroes and region (fresh seeds)."""
    total = record_like['waves_total'] if waves is None else waves
    fell, fought = 0, []
    for run in range(runs):
        trial = dict(record_like, level=level, waves_total=total, seed=economy.seed('forecast', level, run),
                     interventions=[], timeline=[])
        simulate(trial)
        end = end_wave(trial)
        fought.append(end)
        fell += any(r['fell'] for r in trial['timeline'])
    fought.sort()
    return dict(level=level, waves=total, fall_chance=round(fell / runs, 3), waves_median=fought[len(fought) // 2], waves_low=fought[0])


def suggest_level(record_like: dict, waves: int = 12, runs: int = 4) -> int:
    """The highest level this town usually holds for ``waves`` waves without falling."""
    lo, hi, best = 1, MAX_LEVEL, 1
    while lo <= hi:
        mid = (lo + hi) // 2
        f = forecast(record_like, mid, waves, runs)
        if f['fall_chance'] <= 0.25:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best
