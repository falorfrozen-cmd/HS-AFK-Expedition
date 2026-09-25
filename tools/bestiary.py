"""The town's bestiary (0.9): the monsters AFK FARM recorded, region by region.

Every monster that can march on the town is one the player killed in the game
while AFK FARM was recording. Its kill packet carries the game's own facts:
- name and rarity (rank 1-4);
- full health, movement speed, whether it fights at range;
- its fire, cold and poison immunities;
- its elite affixes (the game's affix ids).
The town's waves are drawn from these entries, and each kill the defense makes
is later replayed from one of the entry's own packets through the game's drop
routine. So a region's bestiary is exactly what can attack from it and what
its kills can drop.

What is left out:
- Only packets of the running game build count, and only those carrying the
  protected drop values a replay needs (the plugin refuses the others).
- Chests, breakables and loot goblins are left out here; goblins raid through
  worker_loot's pools.
- So is anything outside ranks 1-4 (bosses, event monsters), until
  `afk special verify` has proven it replays.

Health is measured in the region's own unit, the median full health of its
rank-1 monsters, so a region's numbers compare with any other's.

Standard library only.
"""
from __future__ import annotations

import statistics
from pathlib import Path

import afk

RANKS = (1, 2, 3, 4)
# The game's kill counters are Common, Champion, Ancient and Legion, and on the
# save with the most kills those four add up exactly to the total in the rank
# order 1-4 (STATIC + MEASURED 2026-09-25, from the translations and a save;
# the in-game label of ranks 3 and 4 is not yet checked on screen).
RANK_NAMES = {1: 'Common', 2: 'Champion', 3: 'Ancient', 4: 'Legion'}
# Runtime affix slots from 40 up are flags (zones, states), not affixes.
MAX_AFFIX = 40
# Monsters a special event spawned carry `specialType` (MEASURED 2026-09-25):
# 9 and 10 died within two minutes before an Abyss chest opened; 4 are the
# Unholy Siege's (Summoning Portal) monsters; 1 is most likely a Chaos Pillar's
# pack (unconfirmed); 3 is unknown.
ORIGINS = {9: 'abyss', 10: 'abyss', 4: 'unholy', 1: 'pillar', 3: 'special'}
ORIGIN_NAMES = dict(ordinary='', abyss='Abyssal', unholy='Unholy', pillar='Chaos', special='Marked')
MIN_UNIT_SAMPLES = 1
_CACHE: dict = {}


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _flag(value) -> bool:
    return value is True or (isinstance(value, (int, float)) and not isinstance(value, bool) and value not in (0, 0.0) and value < 1000)


def _weights(room: str) -> dict:
    """Recorded kills per packet in a region: the calibrations' counts (their mix)."""
    prof = afk.DATA / 'profiles'
    files = sorted(prof.glob(f'{room}--*.json')) or ([prof / f'{room}.json'] if (prof / f'{room}.json').exists() else [])
    out = {}
    for f in files:
        p = afk.read_json(f)
        for row in (p or {}).get('packets') or []:
            h, n = row.get('hash'), _num(row.get('count'))
            if isinstance(h, str) and n:
                out[h] = out.get(h, 0.0) + n
    return out


def read_packet(path: Path) -> dict | None:
    """One packet's bestiary facts, or None when it cannot attack the town."""
    d = afk.read_json(path)
    if not isinstance(d, dict) or not d.get('monster_key') or d.get('rank') not in RANKS:
        return None
    if not str(d.get('script') or '').endswith('DropItem'):
        return None                                    # DropRiftItems, synthetic research calls
    obj = str(d.get('self_object') or '')
    if 'Chest' in obj or obj.startswith('Goblin_'):
        return None
    prot = d.get('protected') if isinstance(d.get('protected'), dict) else {}
    idx = afk.packet_entry(d)
    if not prot or not idx.get('complete'):
        return None
    snap = d.get('self_snapshot') if isinstance(d.get('self_snapshot'), dict) else {}
    hp = _num(prot.get('max_hp')) or _num(prot.get('maxHpUnscaled'))
    if not hp or hp <= 0:
        return None
    affixes = [int(a) for a in (snap.get('affixList') or []) if _num(a) is not None and 0 <= a < MAX_AFFIX]
    special = snap.get('specialType')
    origin = ORIGINS.get(int(special), 'special') if _num(special) and special > 0 else 'ordinary'
    return dict(hash=d.get('packet_hash') or path.stem, room=d.get('room'), build=d.get('game_build_id'),
                monster_key=str(d['monster_key']), object=obj, rank=int(d['rank']), name=str(snap.get('name') or d['monster_key']),
                hp=hp, damage=_num(prot.get('damage')), speed=_num(snap.get('moveSpeed')) or 0.0,
                ranged=_flag(snap.get('isRanged')), fire=_flag(snap.get('fireImmune')), cold=_flag(snap.get('coldImmune')),
                poison=_flag(snap.get('poisonImmune')), affixes=sorted(set(affixes)), exp=idx.get('exp'), origin=origin)


def index(build: str | None = None) -> dict:
    """{room: {entry key: entry}} for the running build, cached by the packets' stamp."""
    build = build if build is not None else current_build()
    files = sorted(afk.PACKETS.glob('*.json'))
    prof = sorted((afk.DATA / 'profiles').glob('*.json'))
    stamp = (str(afk.PACKETS), build, len(files), max((f.stat().st_mtime_ns for f in files), default=0),
             len(prof), max((f.stat().st_mtime_ns for f in prof), default=0))
    if _CACHE.get('stamp') == stamp:
        return _CACHE['value']
    rooms: dict = {}
    for f in files:
        facts = read_packet(f)
        if not facts or not build or facts['build'] != build or not facts['room']:
            continue
        rooms.setdefault(facts['room'], []).append(facts)
    value = {room: _entries(room, packets) for room, packets in sorted(rooms.items())}
    _CACHE.update(stamp=stamp, value=value)
    return value


def _entries(room: str, packets: list[dict]) -> dict:
    weights = _weights(room)
    grouped: dict = {}
    for p in packets:
        grouped.setdefault((p['monster_key'], p['rank'], p['origin']), []).append(p)
    ones = [p['hp'] for p in packets if p['rank'] == 1]
    unit = statistics.median(ones) if len(ones) >= MIN_UNIT_SAMPLES else min(p['hp'] for p in packets)
    out = {}
    for (key, rank, origin), members in sorted(grouped.items()):
        members.sort(key=lambda p: p['hash'])
        w = [max(1.0, weights.get(p['hash'], 0.0)) for p in members]
        affix_counts: dict = {}
        for p in members:
            for a in p['affixes']:
                affix_counts[a] = affix_counts.get(a, 0) + 1
        names = sorted({p['name'] for p in members})
        ident = f'{key}#{rank}' + ('' if origin == 'ordinary' else f'@{origin}')
        out[ident] = dict(
            key=ident, monster_key=key, rank=rank, rank_name=RANK_NAMES[rank], origin=origin, name=names[0], names=names,
            object=members[0]['object'], unit=unit, hp=round(statistics.median(p['hp'] for p in members) / unit, 4),
            speed=round(statistics.median(p['speed'] for p in members), 3), ranged=any(p['ranged'] for p in members),
            immune=sorted(k for k in ('fire', 'cold', 'poison') if all(p[k] for p in members)),
            affixes=dict(sorted(affix_counts.items())), affix_sets=[p['affixes'] for p in members],
            packets=[p['hash'] for p in members], weights=w, weight=sum(w), recorded=len(members))
    return out


def current_build() -> str | None:
    return (afk.read_json(afk.DATA / 'build.json', {}) or {}).get('game_build')


def region(room: str, build: str | None = None) -> dict:
    return index(build).get(room) or {}


def regions(build: str | None = None) -> list[dict]:
    """Every region the town can be attacked from, with what its bestiary holds."""
    out = []
    for room, entries in index(build).items():
        ranks = {}
        for e in entries.values():
            ranks[e['rank']] = ranks.get(e['rank'], 0) + e['recorded']
        origins = sorted({e['origin'] for e in entries.values()} - {'ordinary'})
        out.append(dict(room=room, species=len({e['monster_key'] for e in entries.values()}), entries=len(entries),
                        ranks={str(r): ranks.get(r, 0) for r in RANKS}, packets=sum(e['recorded'] for e in entries.values()),
                        special=origins))
    return out
