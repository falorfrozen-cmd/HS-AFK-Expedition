"""Workers (0.7.0; traits and the camp since 0.8): a crew you hire with gold.

A worker is a game layer of AFK FARM, not something the game has: its levels,
skills, traits and haul sizes are designed rules, stated here in one place. What
it brings is still made by the game: the plugin creates every stack of ore and
material through the game's own ground-drop routine (the call a mining node
uses), and the Gem Sense share of the ore goes through the Prospector's own
recipe table and dice in the game. Hiring, retraining and resetting skills cost
the gold of the hero loaded in the game, taken through the game's own purchase
path. The camp (tools/camp.py) and traits (tools/traits.py) change the crew's
numbers, never what the game creates.

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

import camp
import traits
import worker_loot

SCHEMA = 2
MAX_WORKERS = 3                        # the crew at Barracks level 1; the camp raises it
HIRE_PRICES = camp.HIRE_PRICES
RESPEC_PRICE_PER_LEVEL = 25_000
RETRAIN_PRICE = 500_000
MAX_LEVEL = 50
BASE_DIGS_PER_HOUR = 20.0
BASE_TRIP_HOURS = 8.0
MIN_TRIP_HOURS = 0.25
NAME = re.compile(r"[A-Za-z0-9 '\-]{1,24}\Z")
ID = re.compile(r'w_[a-f0-9]{8}\Z')
TYPE_NAMES = dict(miner='Miner', adventurer='Adventurer', goblin_hunter='Goblin Hunter', jeweler='Jeweler')
ROUTES = ('vault', 'stock')             # where a miner's Gem Sense materials go: the Vault, or the Jeweler's stock
STOCK_ROUTE_READY = False               # the plugin must roll without making (route_prospect) before 'stock' is offered

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
TREES = {'miner': TREE, **worker_loot.TREES}
TARGETS = {'miner': [o['key'] for o in ORES]}      # a miner's hot spots are ores; the others' are recorded regions
NEUTRAL_MODS = dict(speed=1.0, amount=1.0, xp=1.0, rare=1.0, tool=0.0, hotspot=0.0, bonus_find=0.0)


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


def points_for(level: int, worker_traits=None) -> int:
    """Skill points at ``level``: one per level from 2, plus Prodigy's at 10, 20 and 30."""
    extra = traits.effects(worker_traits)['points'] if worker_traits else 0
    return max(0, level - 1) + int(extra) * sum(1 for gate in (10, 20, 30) if level >= gate)


def nodes_for(worker: dict) -> dict:
    tree = TREES.get(worker.get('type', 'miner'), ())
    return {n['id']: n for n in tree}


def rank_cap(worker: dict, node: str) -> int:
    spec = nodes_for(worker).get(node)
    return spec['max'] + int(traits.effects(worker.get('traits'))['ranks']) if spec else 0


def ranks(worker: dict, node: str) -> int:
    value = (worker.get('skills') or {}).get(node, 0)
    return value if type(value) is int and 0 <= value <= rank_cap(worker, node) else 0


def spent(worker: dict) -> int:
    return sum(ranks(worker, n) for n in nodes_for(worker))


def unlocked(worker: dict) -> list[dict]:
    return [o for o in ORES if worker['level'] >= o['unlock']]


# ------------------------------------------------------------------ effects
def max_trip_hours(worker: dict) -> float:
    if worker.get('type', 'miner') != 'miner':
        return worker_loot.max_trip_hours(worker)
    return BASE_TRIP_HOURS + ranks(worker, 'long_shift') + traits.effects(worker.get('traits'))['max_hours']


def time_factor(worker: dict) -> float:
    """Real hours per hour of work (Quick Hands, time traits); never below half."""
    if worker.get('type', 'miner') != 'miner':
        return worker_loot.time_factor(worker)
    return max(0.5, (1.0 - 0.04 * ranks(worker, 'quick_hands')) * (1.0 + traits.effects(worker.get('traits'))['time']))


def digs_per_hour(worker: dict, ore: dict, work_hours: float) -> float:
    rate = BASE_DIGS_PER_HOUR * ore['speed'] * (1 + 0.05 * ranks(worker, 'swift_pick'))
    if ore['id'] >= 30:
        rate *= 1 + 0.10 * ranks(worker, 'deep_delver')
    if work_hours >= 6:
        rate *= 1 + 0.10 * ranks(worker, 'night_crew')
    return rate


def xp_factor(worker: dict) -> float:
    return 1 + 0.10 * ranks(worker, 'foreman')


def trip_mods(state: dict, worker: dict, target, hours: float, team=None, at=None) -> dict:
    """The trip's multipliers from traits, the camp and today's hot spot, frozen when it starts."""
    eff = camp.effects(state['camp'])
    tr = traits.effects(worker.get('traits'), hours=hours, team=team, target=target)
    tool = camp.tool_bonus(worker.get('type', 'miner'), worker.get('tool', 0))
    hot = camp.hotspot_bonus(state['camp'], hotspot_targets() if worker.get('type', 'miner') != 'miner' else TARGETS,
                             worker.get('type', 'miner'), target, at)
    bonus = eff['all_bonus']
    return dict(speed=round(1 + tr['speed'] + hot + bonus + (tool if worker.get('type', 'miner') == 'miner' else 0.0), 6),
                amount=round(max(0.1, 1 + tr['amount'] + bonus), 6),
                xp=round((1 + tr['xp']) * (1 + eff['xp_bonus']), 6),
                rare=round(max(0.0, 1 + tr['rare'] + eff['rare_bonus']), 6),
                tool=round(tool, 6), hotspot=round(hot, 6), bonus_find=round(tr['bonus_find'], 6))


def hotspot_targets(pool: dict | None = None) -> dict:
    """What hot spots may point at: ores for miners, recorded regions for adventurers and goblin hunters."""
    pool = pool or worker_loot.pools()
    return dict(TARGETS, adventurer=sorted(worker_loot.regions('adventurer', pool)), goblin_hunter=sorted(worker_loot.regions('goblin_hunter', pool)))


# ------------------------------------------------------------------ state
def empty(at=None) -> dict:
    return dict(schema=SCHEMA, workers=[], payments={}, camp=camp.new(at), candidates={})


def migrate(value: dict) -> dict:
    """0.7.0 files (schema 1) gain a camp and trait/tool fields; nothing is lost."""
    if value.get('schema') == 1:
        value['schema'] = SCHEMA
        value.setdefault('camp', camp.new())
    for w in value.get('workers') or []:
        if isinstance(w, dict):
            w.setdefault('type', 'miner')
            w['traits'] = traits.clean(w.get('traits'))
            w['tool'] = w.get('tool') if type(w.get('tool')) is int and 0 <= w.get('tool') <= camp.MAX_LEVEL else 0
            w['route'] = w.get('route') if w.get('route') in ROUTES else 'vault'
    return value


def load(data, at=None) -> dict:
    try:
        value = json.loads((Path(data) / 'workers.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return empty(at)
    if not isinstance(value, dict) or value.get('schema') not in (1, SCHEMA) or not isinstance(value.get('workers'), list):
        return empty(at)
    value = migrate(value)
    value.setdefault('payments', {})
    value['candidates'] = value.get('candidates') if isinstance(value.get('candidates'), dict) else {}
    value['camp'] = camp.normalize(value.get('camp'))
    camp.settle(value['camp'], at)
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


def max_workers(state: dict) -> int:
    return camp.effects(state['camp'])['max_workers']


def hire_price(state: dict):
    return camp.hire_price(len(state['workers']), max_workers(state))


def respec_price(worker: dict, state: dict | None = None) -> int:
    discount = camp.effects(state['camp'])['respec_discount'] if state else 0.0
    return int(round(RESPEC_PRICE_PER_LEVEL * worker['level'] * (1 - discount)))


def cost_factor(worker: dict) -> float:
    """Frugal and Greedy change what tools and retraining cost this worker."""
    return max(0.25, 1 + traits.effects(worker.get('traits'))['cost'])


def retrain_price(worker: dict) -> int:
    return int(round(RETRAIN_PRICE * cost_factor(worker)))


def new_worker(state: dict, name=None, payment=None, at=None, worker_type='miner', worker_traits=None) -> dict:
    if hire_price(state) is None:
        raise ValueError(f'Your crew is full ({max_workers(state)} workers). Build the Barracks up for more.')
    if worker_type not in TYPE_NAMES:
        raise ValueError('Unknown worker type.')
    count = sum(1 for w in state['workers'] if w.get('type', 'miner') == worker_type) + 1
    name = (name or f"{TYPE_NAMES[worker_type]} {count}").strip()
    if not NAME.fullmatch(name):
        raise ValueError('Names use letters, digits, spaces, apostrophes and hyphens (up to 24).')
    worker = dict(id='w_' + secrets.token_hex(4), type=worker_type, name=name, hired_at=iso(at or now_utc()), xp=0, level=1,
                  skills={}, traits=traits.clean(worker_traits), tool=0, route='vault', trip=None, payment=payment,
                  stats=dict(trips=0, digs=0, hours=0.0, ore={}, finds={}, prospected=0, jewels=0, goblins=0, motherlodes=0, stone=0),
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
    nodes = nodes_for(worker)
    if node not in nodes:
        raise ValueError('Unknown skill.')
    spec = nodes[node]
    if ranks(worker, node) >= rank_cap(worker, node):
        raise ValueError(f"{spec['name']} is already at its highest rank.")
    if spent(worker) >= points_for(worker['level'], worker.get('traits')):
        raise ValueError('No skill points left. Workers earn one per level.')
    if spec['requires']:
        need, rank = spec['requires']
        if ranks(worker, need) < rank:
            raise ValueError(f"{spec['name']} needs {nodes[need]['name']} rank {rank} first.")
    worker.setdefault('skills', {})[node] = ranks(worker, node) + 1
    return worker


def respec(worker: dict) -> dict:
    worker['skills'] = {}
    return worker


def set_route(state: dict, worker_id, route) -> dict:
    """Where a miner's Gem Sense materials go: 'vault' (made in the game) or 'stock' (the Jeweler's)."""
    worker = find(state, worker_id)
    if worker.get('type', 'miner') != 'miner':
        raise ValueError('Only miners have jewelcrafting materials to send.')
    if route not in ROUTES:
        raise ValueError("Choose 'vault' or 'stock'.")
    if route == 'stock' and not STOCK_ROUTE_READY:
        raise ValueError("The Jeweler's stock opens with the Jeweler.")
    if route == 'stock' and 'jeweler' not in camp.effects(state['camp'])['types']:
        raise ValueError("Build the Jeweler's Bench first.")
    worker['route'] = route
    return worker


# ------------------------------------------------------------------ hiring
def candidates(state: dict, worker_type: str, at=None) -> list[dict]:
    """The Tavern's candidates for ``worker_type``: the same list until one is hired
    (or, at Tavern level 5, until the day changes)."""
    eff = camp.effects(state['camp'])
    if worker_type not in eff['types']:
        raise ValueError(f"{TYPE_NAMES.get(worker_type, 'That worker')} needs a better camp (Tavern, Jeweler's Bench).")
    day = (at or now_utc()).strftime('%Y-%m-%d')
    entry = state['candidates'].get(worker_type)
    stale = (not isinstance(entry, dict) or len(entry.get('list') or []) != eff['candidates']
             or (eff['daily_candidates'] and entry.get('day') != day))
    if stale:
        roll_candidates(state, worker_type, at)
        entry = state['candidates'][worker_type]
    return entry['list']


def describe_candidates(state: dict, worker_type: str, at=None) -> list[dict]:
    """The Tavern's candidates with their traits spelled out, and what hiring one costs now."""
    price = hire_price(state)
    return [dict(slot=c['slot'], type=worker_type, type_name=TYPE_NAMES[worker_type], traits=traits.describe(c['traits']), price=price)
            for c in candidates(state, worker_type, at)]


def roll_candidates(state: dict, worker_type: str, at=None) -> list[dict]:
    eff = camp.effects(state['camp'])
    rng = random.Random(secrets.randbits(62))
    odds = traits.rarity_odds(eff['rare_mult'], eff['epic_mult'], eff['legendary'])
    rows = [dict(slot=i, traits=traits.roll(rng, odds, TARGETS.get(worker_type, ()))) for i in range(eff['candidates'])]
    state['candidates'][worker_type] = dict(list=rows, day=(at or now_utc()).strftime('%Y-%m-%d'))
    return rows


def retrain(state: dict, worker_id, which: str) -> dict:
    """Reroll the worker's trait ('trait') or remove its quirk ('quirk') after the Tavern took the gold."""
    worker = find(state, worker_id)
    eff = camp.effects(state['camp'])
    current = traits.clean(worker.get('traits'))
    if which == 'quirk':
        if not any(t.get('quirk') for t in current):
            raise ValueError(f"{worker['name']} has no quirk.")
        worker['traits'] = [t for t in current if not t.get('quirk')]
        return worker
    rng = random.Random(secrets.randbits(62))
    fresh = traits.roll(rng, traits.rarity_odds(eff['rare_mult'], eff['epic_mult'], eff['legendary']), TARGETS.get(worker['type'], ()))
    worker['traits'] = [fresh[0]] + [t for t in current if t.get('quirk')]
    return worker


# ------------------------------------------------------------------ trips
def start_trip(state: dict, worker_id, ore_id, hours, at=None, seed=None, pool: dict | None = None) -> dict:
    """Send a worker out: a miner to an ore (``ore_id``), an adventurer or goblin hunter to a recorded region."""
    worker = find(state, worker_id)
    if worker.get('trip'):
        raise ValueError(f"{worker['name']} is already on a trip.")
    if worker.get('type', 'miner') != 'miner':
        return worker_loot.start_trip(state, worker, ore_id, hours, at, seed, pool)
    ore = ORE_BY_ID.get(ore_id) if type(ore_id) is int else None
    if not ore:
        raise ValueError('Choose an ore.')
    if worker['level'] < ore['unlock']:
        raise ValueError(f"{ore['name']} needs a level {ore['unlock']} miner.")
    hours = float(hours)
    if not math.isfinite(hours) or not MIN_TRIP_HOURS <= hours <= max_trip_hours(worker):
        raise ValueError(f'Trips last from 15 minutes to {max_trip_hours(worker):g} hours for this miner.')
    trip = dict(ore=ore['id'], work_hours=hours, real_hours=round(hours * time_factor(worker), 6), started_at=iso(at or now_utc()),
                seed=seed if type(seed) is int else secrets.randbits(62), delivery=None,
                mods=trip_mods(state, worker, ore['key'], hours, at=at), route=worker.get('route', 'vault'))
    worker['trip'] = trip
    return trip


def trip_view(worker: dict, at=None) -> dict | None:
    trip = worker.get('trip')
    if not trip:
        return None
    started = parse_iso(trip['started_at'])
    elapsed = max(0.0, ((at or now_utc()) - started).total_seconds() / 3600)
    done = min(1.0, elapsed / trip['real_hours']) if trip['real_hours'] > 0 else 1.0
    out = dict(work_hours=trip['work_hours'], real_hours=trip['real_hours'],
               started_at=trip['started_at'], ready_at=iso(started + timedelta(hours=trip['real_hours'])), progress=round(done, 4),
               ready=done >= 1.0, credited_work_hours=round(trip['work_hours'] * done, 6), planned=bool(trip.get('delivery')),
               mods=dict(NEUTRAL_MODS, **(trip.get('mods') or {})))
    if 'ore' in trip:
        out.update(ore=trip['ore'], ore_name=ORE_BY_ID[trip['ore']]['name'], target_name=ORE_BY_ID[trip['ore']]['name'], route=trip.get('route', 'vault'))
    else:
        out.update(region=trip['region'], target_name=trip['region'], keys=dict(trip.get('keys') or {}))
    return out


def cancel_trip(state: dict, worker_id) -> None:
    worker = find(state, worker_id)
    trip = worker.get('trip')
    if not trip:
        raise ValueError(f"{worker['name']} is not on a trip.")
    if trip.get('delivery'):
        raise ValueError('This haul is already being delivered; collect it instead.')
    worker_loot.return_keys(state, trip)
    worker['trip'] = None


def haul(worker: dict, trip: dict, work_hours: float) -> dict:
    """What ``work_hours`` of the trip bring, rolled from the trip's own seed.

    Returns items (type, id, amount) the game will create, the ore units the
    game's Prospector will roll (Gem Sense), the worker experience and the stone
    the camp gets (one per ore mined).
    """
    ore = ORE_BY_ID[trip['ore']]
    mods = dict(NEUTRAL_MODS, **(trip.get('mods') or {}))
    rng = random.Random(trip['seed'])
    rate = digs_per_hour(worker, ore, trip['work_hours']) * mods['speed']
    raw = rate * max(0.0, work_hours)
    digs = int(raw) + (1 if rng.random() < raw - int(raw) else 0)
    lo, hi = ore['amount']
    bonus = ranks(worker, 'rich_veins')
    cart = (1 + 0.06 * ranks(worker, 'full_cart')) * mods['amount']
    double = 0.03 * ranks(worker, 'double_strike')
    mother = 0.006 * ranks(worker, 'motherlode')
    keen = 0.005 * ranks(worker, 'keen_eye') * mods['rare']
    lucky = 0.0015 * ranks(worker, 'lucky_find') * mods['rare']
    heart = 0.0004 * ranks(worker, 'crystal_heart') * mods['rare']
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
    # Treasure Nose: now and then a bonus find one tier up (ten digs of the next ore; fragments on Tarethium).
    nose = mods['bonus_find'] and work_hours >= trip['work_hours'] - 1e-9 and rng.random() < mods['bonus_find']
    if nose:
        better = next((o for o in ORES if o['id'] == ore['id'] + 1), None)
        if better:
            items.append(dict(type=MATERIAL, id=better['id'], amount=sum(rng.randint(*better['amount']) for _ in range(10))))
        else:
            fragments += rng.randint(2, 6)
    for (kind, count) in (('satanic_crystal_fragment', fragments), ('destiny_shard_fragment', shards), ('satanic_crystal', crystals)):
        if count:
            items.append(dict(type=FINDS[kind][0], id=FINDS[kind][1], amount=count))
    xp = int(round((ore_total * ore['xp'] + fragments * 5 + shards * 50 + crystals * 200) * xp_factor(worker) * mods['xp']))
    return dict(ore=ore['id'], work_hours=round(work_hours, 6), digs=digs, ore_total=ore_total, items=[i for i in items if i['amount'] > 0],
                prospect={f"{MATERIAL}:{ore['id']}": prospect} if prospect else {}, xp=xp, goblins=goblins, motherlodes=motherlodes,
                finds=dict(fragments=fragments, shards=shards, crystals=crystals), bonus_find=bool(nose),
                resources=dict(stone=ore_total))


def delivery_plan(worker: dict, trip: dict, work_hours: float, at=None) -> dict:
    """The plugin's delivery file for a trip's haul (planned once, never re-rolled).

    ``route_prospect``: the Gem Sense materials the game rolls are recorded for
    the Jeweler's stock instead of being made (the miner's route was 'stock')."""
    result = haul(worker, trip, work_hours)
    stamp = (at or now_utc()).strftime('%Y%m%d_%H%M%S')
    return dict(schema=1, delivery_id=f"worker_{worker['id'][2:]}_{stamp}", worker_id=worker['id'], worker_name=worker['name'],
                level=worker['level'], planned_at=iso(at or now_utc()), route_prospect=trip.get('route') == 'stock', **result)


def apply_delivery(state: dict, worker_id, plan: dict, result: dict, at=None) -> dict:
    """A delivered haul: experience, levels, statistics, the camp's stone (and routed
    materials); the trip ends. Idle crew mates learn from it at Training Grounds 3."""
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
    outputs = {k: int(v) for k, v in (result.get('prospect_outputs') or {}).items()}
    stats['jewels'] = stats.get('jewels', 0) + sum(outputs.values())
    stats['goblins'] = stats.get('goblins', 0) + plan.get('goblins', 0)
    stats['motherlodes'] = stats.get('motherlodes', 0) + plan.get('motherlodes', 0)
    kept = camp.add(state['camp'], plan.get('resources') or {})
    stats['stone'] = stats.get('stone', 0) + kept.get('stone', 0)
    stocked = camp.add_stock(state['camp'], outputs) if plan.get('route_prospect') else {}
    mentor = camp.effects(state['camp'])['mentor']
    taught = {}
    if mentor and plan['xp']:
        for mate in state['workers']:
            if mate is not worker and not mate.get('trip'):
                gain = int(plan['xp'] * mentor)
                mate['xp'] = int(mate.get('xp', 0)) + gain
                mate['level'] = level_for(mate['xp'])[0]
                taught[mate['id']] = gain
    worker['trip'] = None
    entry = dict(delivery_id=plan['delivery_id'], at=iso(at or now_utc()), ore=plan['ore'], hours=plan['work_hours'], digs=plan['digs'],
                 ore_total=plan['ore_total'], xp=plan['xp'], level_before=before, level_after=worker['level'],
                 created=result.get('created'), prospect_outputs=result.get('prospect_outputs'), camp=kept, stock=stocked)
    worker['history'] = ([entry] + list(worker.get('history') or []))[:20]
    return dict(worker=worker, levels=worker['level'] - before, entry=entry, camp=kept, stock=stocked, taught=taught)


def view(worker: dict, at=None, state: dict | None = None) -> dict:
    """What the panel shows for one worker."""
    level, into, need = level_for(int(worker.get('xp', 0)))
    nodes = nodes_for(worker)
    return dict(id=worker['id'], type=worker.get('type', 'miner'), type_name=TYPE_NAMES.get(worker.get('type', 'miner')), name=worker['name'],
                level=level, xp=worker.get('xp', 0), xp_into_level=into, xp_for_next=need,
                points=points_for(level, worker.get('traits')) - spent(worker),
                skills={n: ranks(worker, n) for n in nodes if ranks(worker, n)}, traits=traits.describe(worker.get('traits')),
                tool=worker.get('tool', 0), tool_name=camp.TOOL_NAMES.get(worker.get('type', 'miner')), route=worker.get('route', 'vault'),
                max_trip_hours=max_trip_hours(worker), time_factor=time_factor(worker),
                respec_price=respec_price(worker, state), retrain_price=retrain_price(worker),
                ores=[dict(id=o['id'], name=o['name'], unlock=o['unlock'], unlocked=level >= o['unlock'],
                           digs_per_hour=round(digs_per_hour(worker, o, BASE_TRIP_HOURS), 2)) for o in ORES] if worker.get('type', 'miner') == 'miner' else [],
                chests=[dict(key=c['key'], name=c['name'], unlock=c['unlock'], unlocked=level >= c['unlock'], key_name=worker_loot.KEY_NAMES.get(c['key_id']))
                        for c in worker_loot.CHESTS] if worker.get('type') == 'adventurer' else [],
                goblins=[dict(key=g['key'], name=g['name'], unlock=g['unlock'], unlocked=level >= g['unlock'])
                         for g in worker_loot.GOBLINS] if worker.get('type') == 'goblin_hunter' else [],
                trip=trip_view(worker, at), stats=worker.get('stats') or {}, history=(worker.get('history') or [])[:5],
                hired_at=worker.get('hired_at'))


def overview(state: dict, at=None) -> dict:
    return dict(workers=[view(w, at, state) for w in state['workers']], hire_price=hire_price(state), max_workers=max_workers(state),
                tree=[dict(n) for n in TREE], trees={k: [dict(n) for n in v] for k, v in TREES.items()}, ores=[dict(o) for o in ORES],
                find_names=FIND_NAMES, types=TYPE_NAMES, camp=camp.view(state['camp'], at),
                traits=[dict(t) for t in traits.TRAITS], quirks=[dict(q) for q in traits.QUIRKS])
