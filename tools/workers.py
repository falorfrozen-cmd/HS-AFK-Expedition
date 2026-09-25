"""Workers (0.7.0): miners you hire with gold and send on their own trips.

A worker is a game layer of AFK FARM, not something the game has: its levels,
skills and haul sizes are designed rules, stated here in one place. What it
brings is still made by the game: the plugin creates every stack of ore and
material through the game's own ground-drop routine (the call a mining node
uses), and the Gem Sense share of the ore goes through the Prospector's own
recipe table and dice in the game. Hiring and resetting skills cost the gold
of the hero loaded in the game, taken through the game's own purchase path.

Standard library only. State: workers.json in the AFK data folder.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
MAX_WORKERS = 3
HIRE_PRICES = (250_000, 1_000_000, 3_000_000)
RESPEC_PRICE_PER_LEVEL = 25_000
MAX_LEVEL = 50
BASE_DIGS_PER_HOUR = 20.0
BASE_TRIP_HOURS = 8.0
MIN_TRIP_HOURS = 0.25
NAME = re.compile(r"[A-Za-z0-9 '\-]{1,24}\Z")
ID = re.compile(r'w_[a-f0-9]{8}\Z')

# Ore tiers in the order the game's mining nodes know them (material bases
# 27-32, SDK ItemType Material = 14): unlock level, dig speed, ore per dig and
# worker XP per ore.
ORES = (
    dict(id=27, key='copper', name='Copper Ore', unlock=1, speed=1.00, amount=(6, 12), xp=1),
    dict(id=28, key='iron', name='Iron Ore', unlock=5, speed=0.90, amount=(5, 11), xp=2),
    dict(id=29, key='gold', name='Gold Ore', unlock=12, speed=0.80, amount=(5, 10), xp=3),
    dict(id=30, key='ruby', name='Ruby Ore', unlock=20, speed=0.70, amount=(4, 9), xp=5),
    dict(id=31, key='jade', name='Jade Ore', unlock=28, speed=0.60, amount=(4, 8), xp=7),
    dict(id=32, key='tarethium', name='Tarethium Ore', unlock=36, speed=0.50, amount=(3, 7), xp=10),
)
ORE_BY_ID = {o['id']: o for o in ORES}
MATERIAL = 14
FINDS = {'satanic_crystal_fragment': (MATERIAL, 60), 'satanic_crystal': (MATERIAL, 58), 'destiny_shard_fragment': (MATERIAL, 66)}
FIND_NAMES = {60: 'Satanic Crystal Fragment', 58: 'Satanic Crystal', 66: 'Destiny Shard Fragment'}
JEWEL_IDS = tuple(range(0, 24))      # what the Prospector turns ore into (material bases 0-23)

# The passive tree: five branches, one point per level from level 2.
TREE = (
    dict(id='swift_pick', branch='Pickwork', name='Swift Pick', max=5, requires=None, text='+5% digs per hour per rank.'),
    dict(id='deep_delver', branch='Pickwork', name='Deep Delver', max=3, requires=('swift_pick', 2), text='+10% digs per hour on Ruby, Jade and Tarethium per rank.'),
    dict(id='double_strike', branch='Pickwork', name='Double Strike', max=5, requires=('swift_pick', 5), text='3% chance per rank that a dig counts twice.'),
    dict(id='full_cart', branch='Haul', name='Full Cart', max=5, requires=None, text='+6% ore per dig per rank.'),
    dict(id='rich_veins', branch='Haul', name='Rich Veins', max=3, requires=('full_cart', 2), text='+1 ore on every dig per rank.'),
    dict(id='motherlode', branch='Haul', name='Motherlode', max=5, requires=('full_cart', 5), text='0.6% chance per rank per dig to strike a motherlode: five times the ore.'),
    dict(id='long_shift', branch='Endurance', name='Long Shift', max=4, requires=None, text='+1 hour maximum trip length per rank.'),
    dict(id='quick_hands', branch='Endurance', name='Quick Hands', max=5, requires=('long_shift', 1), text='Trips finish 4% sooner per rank; the work stays the same.'),
    dict(id='night_crew', branch='Endurance', name='Night Crew', max=2, requires=('long_shift', 4), text='+10% digs per hour per rank on trips of 6 hours or longer.'),
    dict(id='keen_eye', branch='Prospecting', name='Keen Eye', max=5, requires=None, text='0.5% chance per rank per dig to find 2-6 Satanic Crystal Fragments.'),
    dict(id='gem_sense', branch='Prospecting', name='Gem Sense', max=4, requires=('keen_eye', 1),
         text="Prospects 5% of the ore per rank with the Prospector's own recipe and chances (jewelcrafting materials); prospected ore is used up."),
    dict(id='lucky_find', branch='Prospecting', name='Lucky Find', max=3, requires=('gem_sense', 2), text='0.15% chance per rank per dig to find a Destiny Shard Fragment.'),
    dict(id='crystal_heart', branch='Prospecting', name='Crystal Heart', max=3, requires=('keen_eye', 5), text='0.04% chance per rank per dig to find a Satanic Crystal.'),
    dict(id='foreman', branch='Mastery', name='Foreman', max=3, requires=None, text='+10% worker experience per rank.'),
    dict(id='goblin_bait', branch='Mastery', name='Goblin Bait', max=3, requires=None,
         text='4% chance per rank per trip hour that an Ore Goblin leaves a bonus haul (ten digs of the trip ore).'),
)
NODES = {n['id']: n for n in TREE}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(text):
    return datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ levels
def xp_to_next(level: int) -> int:
    """Worker experience from ``level`` to the next one.

    Tuned (2026-09-24) on a simulated miner doing 16 trip hours a day on its
    best ore with a sensible skill order: Iron in half a day, Gold in 2.5 days,
    Ruby in about 9, Jade 17, Tarethium 27 and level 50 in about 54 days.
    """
    return int(round(12 * level ** 2.4))


def level_for(xp: int) -> tuple[int, int, int]:
    """(level, experience into it, experience the next level needs) for total XP."""
    level = 1
    while level < MAX_LEVEL and xp >= xp_to_next(level):
        xp -= xp_to_next(level)
        level += 1
    return level, xp, (xp_to_next(level) if level < MAX_LEVEL else 0)


def points_for(level: int) -> int:
    return max(0, level - 1)


def ranks(worker: dict, node: str) -> int:
    value = (worker.get('skills') or {}).get(node, 0)
    return value if type(value) is int and 0 <= value <= NODES[node]['max'] else 0


def spent(worker: dict) -> int:
    return sum(ranks(worker, n) for n in NODES)


def unlocked(worker: dict) -> list[dict]:
    return [o for o in ORES if worker['level'] >= o['unlock']]


# ------------------------------------------------------------------ effects
def max_trip_hours(worker: dict) -> float:
    return BASE_TRIP_HOURS + ranks(worker, 'long_shift')


def time_factor(worker: dict) -> float:
    """Real hours per hour of work (Quick Hands)."""
    return 1.0 - 0.04 * ranks(worker, 'quick_hands')


def digs_per_hour(worker: dict, ore: dict, work_hours: float) -> float:
    rate = BASE_DIGS_PER_HOUR * ore['speed'] * (1 + 0.05 * ranks(worker, 'swift_pick'))
    if ore['id'] >= 30:
        rate *= 1 + 0.10 * ranks(worker, 'deep_delver')
    if work_hours >= 6:
        rate *= 1 + 0.10 * ranks(worker, 'night_crew')
    return rate


def xp_factor(worker: dict) -> float:
    return 1 + 0.10 * ranks(worker, 'foreman')


# ------------------------------------------------------------------ state
def empty() -> dict:
    return dict(schema=SCHEMA, workers=[], payments={})


def load(data) -> dict:
    try:
        value = json.loads((Path(data) / 'workers.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return empty()
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or not isinstance(value.get('workers'), list):
        return empty()
    value.setdefault('payments', {})
    return value


def save(data, state: dict) -> None:
    path = Path(data) / 'workers.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, indent=1, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def find(state: dict, worker_id) -> dict:
    worker = next((w for w in state['workers'] if w.get('id') == worker_id), None)
    if not worker:
        raise ValueError('That worker is not on your crew.')
    return worker


def hire_price(state: dict):
    n = len(state['workers'])
    return HIRE_PRICES[n] if n < MAX_WORKERS else None


def respec_price(worker: dict) -> int:
    return RESPEC_PRICE_PER_LEVEL * worker['level']


def new_worker(state: dict, name=None, payment=None, at=None) -> dict:
    if hire_price(state) is None:
        raise ValueError(f'Your crew is full ({MAX_WORKERS} workers).')
    name = (name or f"Miner {len(state['workers']) + 1}").strip()
    if not NAME.fullmatch(name):
        raise ValueError('Names use letters, digits, spaces, apostrophes and hyphens (up to 24).')
    worker = dict(id='w_' + secrets.token_hex(4), type='miner', name=name, hired_at=iso(at or now_utc()), xp=0, level=1,
                  skills={}, trip=None, payment=payment,
                  stats=dict(trips=0, digs=0, hours=0.0, ore={}, finds={}, prospected=0, jewels=0, goblins=0, motherlodes=0),
                  history=[])
    state['workers'].append(worker)
    return worker


def rename(state: dict, worker_id, name) -> dict:
    worker = find(state, worker_id)
    name = str(name or '').strip()
    if not NAME.fullmatch(name):
        raise ValueError('Names use letters, digits, spaces, apostrophes and hyphens (up to 24).')
    worker['name'] = name
    return worker


def learn(state: dict, worker_id, node) -> dict:
    """Spend one skill point on ``node``."""
    worker = find(state, worker_id)
    if node not in NODES:
        raise ValueError('Unknown skill.')
    spec = NODES[node]
    if ranks(worker, node) >= spec['max']:
        raise ValueError(f"{spec['name']} is already at its highest rank.")
    if spent(worker) >= points_for(worker['level']):
        raise ValueError('No skill points left. Workers earn one per level.')
    if spec['requires']:
        need, rank = spec['requires']
        if ranks(worker, need) < rank:
            raise ValueError(f"{spec['name']} needs {NODES[need]['name']} rank {rank} first.")
    worker.setdefault('skills', {})[node] = ranks(worker, node) + 1
    return worker


def respec(worker: dict) -> dict:
    worker['skills'] = {}
    return worker


# ------------------------------------------------------------------ trips
def start_trip(state: dict, worker_id, ore_id, hours, at=None, seed=None) -> dict:
    worker = find(state, worker_id)
    if worker.get('trip'):
        raise ValueError(f"{worker['name']} is already on a trip.")
    ore = ORE_BY_ID.get(ore_id) if type(ore_id) is int else None
    if not ore:
        raise ValueError('Choose an ore.')
    if worker['level'] < ore['unlock']:
        raise ValueError(f"{ore['name']} needs a level {ore['unlock']} miner.")
    hours = float(hours)
    if not math.isfinite(hours) or not MIN_TRIP_HOURS <= hours <= max_trip_hours(worker):
        raise ValueError(f'Trips last from 15 minutes to {max_trip_hours(worker):g} hours for this miner.')
    trip = dict(ore=ore['id'], work_hours=hours, real_hours=round(hours * time_factor(worker), 6), started_at=iso(at or now_utc()),
                seed=seed if type(seed) is int else secrets.randbits(62), delivery=None)
    worker['trip'] = trip
    return trip


def trip_view(worker: dict, at=None) -> dict | None:
    trip = worker.get('trip')
    if not trip:
        return None
    started = parse_iso(trip['started_at'])
    elapsed = max(0.0, ((at or now_utc()) - started).total_seconds() / 3600)
    done = min(1.0, elapsed / trip['real_hours']) if trip['real_hours'] > 0 else 1.0
    return dict(ore=trip['ore'], ore_name=ORE_BY_ID[trip['ore']]['name'], work_hours=trip['work_hours'], real_hours=trip['real_hours'],
                started_at=trip['started_at'], ready_at=iso(started + timedelta(hours=trip['real_hours'])), progress=round(done, 4),
                ready=done >= 1.0, credited_work_hours=round(trip['work_hours'] * done, 6), planned=bool(trip.get('delivery')))


def cancel_trip(state: dict, worker_id) -> None:
    worker = find(state, worker_id)
    trip = worker.get('trip')
    if not trip:
        raise ValueError(f"{worker['name']} is not on a trip.")
    if trip.get('delivery'):
        raise ValueError('This haul is already being delivered; collect it instead.')
    worker['trip'] = None


def haul(worker: dict, trip: dict, work_hours: float) -> dict:
    """What ``work_hours`` of the trip bring, rolled from the trip's own seed.

    Returns items (type, id, amount) the game will create, the ore units the
    game's Prospector will roll (Gem Sense), and the worker experience.
    """
    ore = ORE_BY_ID[trip['ore']]
    rng = random.Random(trip['seed'])
    rate = digs_per_hour(worker, ore, trip['work_hours'])
    raw = rate * max(0.0, work_hours)
    digs = int(raw) + (1 if rng.random() < raw - int(raw) else 0)
    lo, hi = ore['amount']
    bonus = ranks(worker, 'rich_veins')
    cart = 1 + 0.06 * ranks(worker, 'full_cart')
    double = 0.03 * ranks(worker, 'double_strike')
    mother = 0.006 * ranks(worker, 'motherlode')
    keen = 0.005 * ranks(worker, 'keen_eye')
    lucky = 0.0015 * ranks(worker, 'lucky_find')
    heart = 0.0004 * ranks(worker, 'crystal_heart')
    ore_total, fragments, shards, crystals, motherlodes = 0, 0, 0, 0, 0

    def dig_amount():
        nonlocal motherlodes
        n = rng.randint(lo + bonus, hi + bonus) * cart
        if mother and rng.random() < mother:
            motherlodes += 1
            n *= 5
        return n

    for _ in range(digs):
        amount = dig_amount()
        if double and rng.random() < double:
            amount += dig_amount()
        ore_total += amount
        if keen and rng.random() < keen:
            fragments += rng.randint(2, 6)
        if lucky and rng.random() < lucky:
            shards += 1
        if heart and rng.random() < heart:
            crystals += 1
    goblins = 0
    bait = 0.04 * ranks(worker, 'goblin_bait')
    for _ in range(int(math.ceil(work_hours))):
        if bait and rng.random() < bait:
            goblins += 1
            ore_total += sum(rng.randint(lo + bonus, hi + bonus) for _ in range(10)) * cart
    ore_total = int(round(ore_total))
    prospect = int(round(ore_total * 0.05 * ranks(worker, 'gem_sense')))
    items = [dict(type=MATERIAL, id=ore['id'], amount=ore_total - prospect)]
    for (kind, count) in (('satanic_crystal_fragment', fragments), ('destiny_shard_fragment', shards), ('satanic_crystal', crystals)):
        if count:
            items.append(dict(type=FINDS[kind][0], id=FINDS[kind][1], amount=count))
    xp = int(round((ore_total * ore['xp'] + fragments * 5 + shards * 50 + crystals * 200) * xp_factor(worker)))
    return dict(ore=ore['id'], work_hours=round(work_hours, 6), digs=digs, ore_total=ore_total, items=[i for i in items if i['amount'] > 0],
                prospect={f"{MATERIAL}:{ore['id']}": prospect} if prospect else {}, xp=xp, goblins=goblins, motherlodes=motherlodes,
                finds=dict(fragments=fragments, shards=shards, crystals=crystals))


def delivery_plan(worker: dict, trip: dict, work_hours: float, at=None) -> dict:
    """The plugin's delivery file for a trip's haul (planned once, never re-rolled)."""
    result = haul(worker, trip, work_hours)
    stamp = (at or now_utc()).strftime('%Y%m%d_%H%M%S')
    return dict(schema=1, delivery_id=f"worker_{worker['id'][2:]}_{stamp}", worker_id=worker['id'], worker_name=worker['name'],
                level=worker['level'], planned_at=iso(at or now_utc()), **result)


def apply_delivery(state: dict, worker_id, plan: dict, result: dict, at=None) -> dict:
    """A delivered haul: the worker's experience, levels and statistics; the trip ends."""
    worker = find(state, worker_id)
    before = worker['level']
    worker['xp'] = int(worker.get('xp', 0)) + int(plan['xp'])
    worker['level'], into, need = level_for(worker['xp'])
    stats = worker.setdefault('stats', {})
    stats['trips'] = stats.get('trips', 0) + 1
    stats['digs'] = stats.get('digs', 0) + plan['digs']
    stats['hours'] = round(stats.get('hours', 0.0) + plan['work_hours'], 4)
    ores = stats.setdefault('ore', {})
    ores[str(plan['ore'])] = ores.get(str(plan['ore']), 0) + plan['ore_total']
    finds = stats.setdefault('finds', {})
    for key, amount in (result.get('created') or {}).items():
        if key != f"{MATERIAL}:{plan['ore']}":
            finds[key] = finds.get(key, 0) + int(amount)
    stats['prospected'] = stats.get('prospected', 0) + sum((plan.get('prospect') or {}).values())
    stats['jewels'] = stats.get('jewels', 0) + sum(int(v) for k, v in (result.get('prospect_outputs') or {}).items())
    stats['goblins'] = stats.get('goblins', 0) + plan.get('goblins', 0)
    stats['motherlodes'] = stats.get('motherlodes', 0) + plan.get('motherlodes', 0)
    worker['trip'] = None
    entry = dict(delivery_id=plan['delivery_id'], at=iso(at or now_utc()), ore=plan['ore'], hours=plan['work_hours'], digs=plan['digs'],
                 ore_total=plan['ore_total'], xp=plan['xp'], level_before=before, level_after=worker['level'],
                 created=result.get('created'), prospect_outputs=result.get('prospect_outputs'))
    worker['history'] = ([entry] + list(worker.get('history') or []))[:20]
    return dict(worker=worker, levels=worker['level'] - before, entry=entry)


def view(worker: dict, at=None) -> dict:
    """What the panel shows for one worker."""
    level, into, need = level_for(int(worker.get('xp', 0)))
    return dict(id=worker['id'], type=worker['type'], name=worker['name'], level=level, xp=worker.get('xp', 0), xp_into_level=into,
                xp_for_next=need, points=points_for(level) - spent(worker), skills={n: ranks(worker, n) for n in NODES if ranks(worker, n)},
                max_trip_hours=max_trip_hours(worker), time_factor=time_factor(worker), respec_price=respec_price(worker),
                ores=[dict(id=o['id'], name=o['name'], unlock=o['unlock'], unlocked=level >= o['unlock'],
                           digs_per_hour=round(digs_per_hour(worker, o, BASE_TRIP_HOURS), 2)) for o in ORES],
                trip=trip_view(worker, at), stats=worker.get('stats') or {}, history=(worker.get('history') or [])[:5],
                hired_at=worker.get('hired_at'))


def overview(state: dict, at=None) -> dict:
    return dict(workers=[view(w, at) for w in state['workers']], hire_price=hire_price(state), max_workers=MAX_WORKERS,
                tree=[dict(n) for n in TREE], ores=[dict(o) for o in ORES], find_names=FIND_NAMES)
