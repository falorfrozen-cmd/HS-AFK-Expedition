"""The Camp (0.8): buildings that make the crew stronger.

The camp is AFK FARM's own game layer, like worker levels and skills. Its
buildings change the crew's rules (how many workers, how fast they work, what
they may reach) and the Siege gate; they never create anything in the game.
Buildings cost gold, taken from the loaded hero through the game's purchase path
(the same receipts as hiring), and camp resources the workers bring home:
stone (miners), spoils (adventurers, goblin hunters) and gem dust (jewelers).
Those resources exist only in AFK FARM. Building takes real time; one site at a
time, two from Headquarters level 3.

Standard library only. The camp lives in workers.json (``state['camp']``).
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

K, M = 1_000, 1_000_000
RESOURCES = ('stone', 'spoils', 'dust')
MAX_LEVEL = 5


def _cost(gold, stone=0, spoils=0, dust=0, hours=1.0):
    return dict(gold=int(gold), stone=stone, spoils=spoils, dust=dust, hours=float(hours))


# levels[n] builds level n+1; None = already there when the camp is founded.
BUILDINGS = (
    dict(key='hq', name='Headquarters', start=1, unlock_hq=1,
         text='The camp level: every other building can rise to it. Level 3 opens a second building site.',
         levels=(None, _cost(1 * M, 400, hours=2), _cost(3 * M, 1500, 400, hours=8), _cost(8 * M, 4000, 1500, 200, 16),
                 _cost(20 * M, 10000, 4000, 800, 24))),
    dict(key='barracks', name='Barracks', start=1, unlock_hq=1, text='How many workers you keep and how many go on a team trip.',
         levels=(None, _cost(500 * K, 300, hours=1), _cost(1.5 * M, 1000, 300, hours=4), _cost(4 * M, 2500, 800, 100, 10),
                 _cost(10 * M, 6000, 2000, 400, 20))),
    dict(key='tavern', name='Tavern', start=0, unlock_hq=1,
         text='Hiring: new worker types, more candidates to choose from, better traits, retraining.',
         levels=(_cost(300 * K, 150, hours=0.5), _cost(800 * K, 500, 150, hours=2), _cost(2 * M, 1200, 500, 50, 6),
                 _cost(5 * M, 3000, 1200, 200, 12), _cost(12 * M, 7000, 3000, 600, 24))),
    dict(key='walls', name='Walls', start=0, unlock_hq=1, text="Your heroes' Siege gate: more health, better repairs.",
         levels=(_cost(400 * K, 300, hours=1), _cost(1.2 * M, 900, 100, hours=3), _cost(3 * M, 2000, 400, hours=8),
                 _cost(7 * M, 4500, 1000, 150, 14), _cost(15 * M, 9000, 2500, 500, 24))),
    dict(key='storehouse', name='Storehouse', start=1, unlock_hq=1, text="How much of each resource, the Jeweler's stock and the key rack can hold.",
         levels=(None, _cost(300 * K, 400, hours=1), _cost(1 * M, 1200, 200, hours=4), _cost(3 * M, 3000, 600, 80, 10),
                 _cost(8 * M, 7000, 1500, 300, 20))),
    dict(key='forge', name='Forge', start=0, unlock_hq=2, text='Tools for each worker: pickaxes, lockpicks, nets and chisels.',
         levels=(_cost(600 * K, 600, hours=2), _cost(1.5 * M, 1500, 200, hours=5), _cost(3.5 * M, 3000, 600, 60, 10),
                 _cost(7 * M, 6000, 1400, 200, 16), _cost(15 * M, 12000, 3000, 600, 24))),
    dict(key='training', name='Training Grounds', start=0, unlock_hq=2, text='More worker experience, cheaper skill resets, apprentices.',
         levels=(_cost(500 * K, 400, hours=2), _cost(1.2 * M, 1000, 150, hours=5), _cost(3 * M, 2200, 500, 40, 10),
                 _cost(6 * M, 4500, 1200, 150, 16), _cost(14 * M, 9000, 2800, 500, 24))),
    dict(key='jeweler_bench', name="Jeweler's Bench", start=0, unlock_hq=3, text="The Jeweler's workplace: each level opens the next tier of the game's jewel recipes.",
         levels=(_cost(1.5 * M, 1000, 300, hours=4), _cost(3 * M, 2000, 600, 100, 8), _cost(6 * M, 4000, 1200, 300, 12),
                 _cost(10 * M, 7000, 2500, 700, 18), _cost(18 * M, 12000, 5000, 1500, 24))),
    dict(key='watchtower', name='Watchtower', start=0, unlock_hq=3, text='Daily hot spots: a target where workers do better today.',
         levels=(_cost(1 * M, 800, 300, hours=3), _cost(2.5 * M, 1800, 700, 60, 6), _cost(5 * M, 3500, 1500, 200, 12),
                 _cost(9 * M, 6000, 3000, 450, 18), _cost(16 * M, 10000, 5000, 900, 24))),
)
BY_KEY = {b['key']: b for b in BUILDINGS}

HIRE_PRICES = (250 * K, 1 * M, 3 * M, 6 * M, 10 * M, 16 * M, 25 * M)
TOOL_NAMES = dict(miner='Pickaxe', adventurer='Lockpick', goblin_hunter='Net', jeweler='Chisel')
TOOL_TEXT = dict(miner='+5% digs per hour per tier', adventurer='+2% chance per tier to open a locked chest without a key',
                 goblin_hunter='+5% catch chance per tier', jeweler='+3% chance per tier of a second jewel')


# ------------------------------------------------------------------ time
def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(text):
    return datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ state
def new(at=None) -> dict:
    return dict(founded_at=iso(at or now_utc()), buildings={b['key']: b['start'] for b in BUILDINGS}, queue=[],
                resources={r: 0 for r in RESOURCES}, stock={}, keys={}, free_respec_week=None)


def _count(value) -> int:
    return value if type(value) is int and value > 0 else 0


def normalize(camp) -> dict:
    """A stored camp with every field present and numbers sane (older files, hand edits)."""
    base = new()
    camp = camp if isinstance(camp, dict) else {}
    out = dict(base, **{k: camp[k] for k in base if k in camp})
    levels = out['buildings'] if isinstance(out['buildings'], dict) else {}
    out['buildings'] = {}
    for b in BUILDINGS:
        value = levels.get(b['key'], b['start'])
        out['buildings'][b['key']] = min(MAX_LEVEL, max(b['start'], value if type(value) is int else b['start']))
    resources = out['resources'] if isinstance(out['resources'], dict) else {}
    out['resources'] = {r: _count(resources.get(r)) for r in RESOURCES}
    for field in ('stock', 'keys'):
        values = out[field] if isinstance(out[field], dict) else {}
        out[field] = {str(k): _count(v) for k, v in values.items() if _count(v)}
    out['queue'] = [q for q in out['queue'] if isinstance(q, dict) and q.get('building') in BY_KEY] if isinstance(out['queue'], list) else []
    return out


def level(camp: dict, key: str) -> int:
    return int((camp.get('buildings') or {}).get(key, BY_KEY[key]['start']))


# ------------------------------------------------------------------ effects
def effects(camp: dict) -> dict:
    """Everything the buildings change, for the current levels."""
    hq, barracks, tavern, walls = level(camp, 'hq'), level(camp, 'barracks'), level(camp, 'tavern'), level(camp, 'walls')
    store, forge, training = level(camp, 'storehouse'), level(camp, 'forge'), level(camp, 'training')
    bench, tower = level(camp, 'jeweler_bench'), level(camp, 'watchtower')
    return dict(
        sites=2 if hq >= 3 else 1, all_bonus=0.05 if hq >= 5 else 0.0,
        max_workers=2 + barracks, team_size={1: 2, 2: 2, 3: 3, 4: 3, 5: 4}[barracks],
        candidates={0: 1, 1: 2, 2: 3, 3: 3, 4: 4, 5: 4}[tavern], rare_mult=1.5 if tavern >= 2 else 1.0,
        epic_mult=2.0 if tavern >= 4 else 1.0, legendary=0.02 if tavern >= 5 else 0.0, retrain=tavern >= 3,
        daily_candidates=tavern >= 5,
        types=['miner'] + (['adventurer', 'goblin_hunter'] if tavern >= 1 else []) + (['jeweler'] if bench >= 1 else []),
        gate_hp=100.0 + 10.0 * walls, gate_repair=float(max(0, walls - 1)), gate_max_damage=45.0 if walls >= 5 else 50.0,
        resource_cap=(1000, 3000, 8000, 20000, 50000)[store - 1], stock_cap=(500, 1500, 4000, 10000, 25000)[store - 1],
        key_cap=(10, 25, 50, 100, 250)[store - 1],
        tool_tier=forge,
        xp_bonus=0.10 * training, respec_discount=0.25 if training >= 2 else 0.0, mentor=0.10 if training >= 3 else 0.0,
        weekly_free_respec=training >= 5,
        recipe_tier=bench, double_output={0: 0.0, 1: 0.0, 2: 0.0, 3: 0.05, 4: 0.05, 5: 0.10}[bench],
        hotspots={0: 0, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3}[tower], hotspot_bonus={0: 0.0, 1: 0.2, 2: 0.2, 3: 0.3, 4: 0.3, 5: 0.4}[tower],
        rare_bonus={0: 0.0, 1: 0.0, 2: 0.0, 3: 0.03, 4: 0.03, 5: 0.06}[tower],
    )


def hire_price(crew_size: int, max_workers: int):
    return HIRE_PRICES[crew_size] if crew_size < min(max_workers, len(HIRE_PRICES)) else None


def tool_cost(tier: int) -> dict:
    """What a worker's tool of ``tier`` (1-5) costs at the Forge."""
    return dict(gold=100 * K * tier, stone=200 * tier, spoils=50 * tier if tier >= 2 else 0, dust=20 * tier if tier >= 3 else 0)


def tool_bonus(worker_type: str, tier: int) -> float:
    per = dict(miner=0.05, adventurer=0.02, goblin_hunter=0.05, jeweler=0.03).get(worker_type, 0.0)
    return per * max(0, min(MAX_LEVEL, int(tier or 0)))


# ------------------------------------------------------------------ resources
def add(camp: dict, gains: dict) -> dict:
    """Add resources up to the Storehouse's cap; returns what was actually kept."""
    cap = effects(camp)['resource_cap']
    kept = {}
    for r in RESOURCES:
        n = max(0, int(gains.get(r, 0) or 0))
        room = max(0, cap - camp['resources'].get(r, 0))
        kept[r] = min(n, room)
        camp['resources'][r] = camp['resources'].get(r, 0) + kept[r]
    return kept


def afford(camp: dict, cost: dict) -> list[str]:
    """What is missing for ``cost`` (camp resources only; gold is the game's)."""
    return [f"{cost[r] - camp['resources'].get(r, 0):,} {r}" for r in RESOURCES if cost.get(r, 0) > camp['resources'].get(r, 0)]


def take(camp: dict, cost: dict) -> dict:
    missing = afford(camp, cost)
    if missing:
        raise ValueError('Not enough camp resources: ' + ', '.join(missing) + ' missing.')
    for r in RESOURCES:
        camp['resources'][r] -= int(cost.get(r, 0) or 0)
    return {r: int(cost.get(r, 0) or 0) for r in RESOURCES}


def give_back(camp: dict, taken: dict) -> None:
    """Return reserved resources (a payment the game refused); may pass the cap."""
    for r in RESOURCES:
        camp['resources'][r] = camp['resources'].get(r, 0) + int((taken or {}).get(r, 0) or 0)


def add_stock(camp: dict, items: dict) -> dict:
    """Jeweler's materials ("type:id" -> units) up to the stock cap; returns what was kept."""
    cap = effects(camp)['stock_cap']
    kept = {}
    for key, n in items.items():
        room = max(0, cap - sum(camp['stock'].values()))
        kept[key] = min(max(0, int(n)), room)
        if kept[key]:
            camp['stock'][key] = camp['stock'].get(key, 0) + kept[key]
    return kept


def add_keys(camp: dict, keys: dict) -> dict:
    """Keys (base id -> count) onto the key rack up to its cap; returns what was kept."""
    cap = effects(camp)['key_cap']
    kept = {}
    for key, n in keys.items():
        room = max(0, cap - sum(camp['keys'].values()))
        kept[str(key)] = min(max(0, int(n)), room)
        if kept[str(key)]:
            camp['keys'][str(key)] = camp['keys'].get(str(key), 0) + kept[str(key)]
    return kept


# ------------------------------------------------------------------ building
def settle(camp: dict, at=None) -> list[dict]:
    """Finish every building whose time is up; returns the finished ones."""
    at = at or now_utc()
    done, left = [], []
    for q in camp['queue']:
        if q.get('ready_at') and parse_iso(q['ready_at']) <= at:
            camp['buildings'][q['building']] = max(level(camp, q['building']), int(q['to']))
            done.append(q)
        else:
            left.append(q)
    camp['queue'] = left
    return done


def next_build(camp: dict, key: str) -> dict:
    """The next level of ``key``: its level, cost, hours and whether it can start now."""
    spec = BY_KEY.get(key)
    if not spec:
        raise ValueError('Unknown building.')
    queued = [q for q in camp['queue'] if q['building'] == key]
    current = max([level(camp, key)] + [int(q['to']) for q in queued])
    to = current + 1
    if to > MAX_LEVEL:
        return dict(key=key, to=None, cost=None, hours=None, blockers=['already at its highest level'])
    cost = dict(spec['levels'][to - 1])
    hours = cost.pop('hours')
    blockers = []
    hq = level(camp, 'hq')
    if key == 'hq':
        pass
    elif to == 1 and hq < spec['unlock_hq']:
        blockers.append(f"needs Headquarters level {spec['unlock_hq']}")
    elif to > hq:
        blockers.append(f'needs Headquarters level {to}')
    if queued:
        blockers.append('already being built')
    if len(camp['queue']) >= effects(camp)['sites']:
        blockers.append('every building site is busy')
    missing = afford(camp, cost)
    if missing:
        blockers.append('missing ' + ', '.join(missing))
    return dict(key=key, to=to, cost=cost, hours=hours, blockers=blockers)


def start(camp: dict, key: str, request=None, at=None) -> dict:
    """Queue the next level of ``key`` (its resources were taken already)."""
    plan = next_build(camp, key)
    at = at or now_utc()
    entry = dict(building=key, to=plan['to'], started_at=iso(at), ready_at=iso(at + timedelta(hours=plan['hours'])), request=request)
    camp['queue'].append(entry)
    return entry


# ------------------------------------------------------------------ hot spots
def hotspots(camp: dict, targets: dict, day=None) -> list[dict]:
    """Today's hot spots: ``targets`` maps a worker type to its target keys.

    Drawn from the camp's founding time and the date, so the whole day agrees and
    nothing depends on when the page asks."""
    eff = effects(camp)
    n = eff['hotspots']
    if not n:
        return []
    day = (day or now_utc()).strftime('%Y-%m-%d')
    pool = [(t, k) for t in sorted(targets) for k in targets[t]]
    picked = []
    for i in range(min(n, len(pool))):
        digest = hashlib.sha256(f"{camp.get('founded_at')}|{day}|{i}".encode()).digest()
        choice = pool.pop(int.from_bytes(digest[:8], 'big') % len(pool))
        picked.append(dict(type=choice[0], target=choice[1], bonus=eff['hotspot_bonus']))
    return picked


def hotspot_bonus(camp: dict, targets: dict, worker_type: str, target, day=None) -> float:
    return next((h['bonus'] for h in hotspots(camp, targets, day) if h['type'] == worker_type and h['target'] == target), 0.0)


# ------------------------------------------------------------------ view
def view(camp: dict, at=None) -> dict:
    at = at or now_utc()
    eff = effects(camp)
    rows = []
    for b in BUILDINGS:
        nxt = next_build(camp, b['key'])
        rows.append(dict(key=b['key'], name=b['name'], text=b['text'], level=level(camp, b['key']), max_level=MAX_LEVEL,
                         unlock_hq=b['unlock_hq'], next=nxt))
    queue = []
    for q in camp['queue']:
        started, ready = parse_iso(q['started_at']), parse_iso(q['ready_at'])
        span = max(1.0, (ready - started).total_seconds())
        queue.append(dict(q, name=BY_KEY[q['building']]['name'], progress=round(min(1.0, max(0.0, (at - started).total_seconds() / span)), 4)))
    return dict(founded_at=camp.get('founded_at'), buildings=rows, queue=queue, sites=eff['sites'], resources=dict(camp['resources']),
                resource_cap=eff['resource_cap'], stock=dict(camp['stock']), stock_cap=eff['stock_cap'], keys=dict(camp['keys']),
                key_cap=eff['key_cap'], effects=eff)
