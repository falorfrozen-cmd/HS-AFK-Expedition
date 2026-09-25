"""Adventurers and goblin hunters (0.8): workers whose loot is the game's own drop.

An adventurer opens the world chests of a region, a goblin hunter catches its
loot goblins. How many they find, which ones they open or catch and how fast
they level are AFK FARM's rules (like the miner's). What they bring is not:
each opened chest or caught goblin is one recorded drop situation of that
region (a packet the plugin captured while the player played) replayed through
the game's own drop routine, exactly like a hero expedition's kills, with no
experience for the hero and the result sent to the Vault. A replay reads the
live room, so a haul is collected with an offline hero standing in the trip's
region, and only packets of the running game build can replay.

Keys are the game's: a golden chest takes a Basic Key, a crystal chest a
Crystal Key, from the camp's key rack (filled from the Vault, see vault_take.py).
Keys a trip finds go to the rack.

Standard library only.
"""
from __future__ import annotations

import random

CHEST_OBJECT = 'Chest_Drop_obj'
# args[0] of a world chest's DropItem call is its opening rarity (MEASURED
# 2026-09-24 on 23 recorded chests, matching the chest's sprite): 2 wooden,
# 3 golden (a Basic Key), 4 crystal (a Crystal Key).
CHEST_TIERS = {2: 'wooden', 3: 'golden', 4: 'crystal'}
CHESTS = (
    dict(key='wooden', name='Wooden chest', unlock=1, key_id=None, xp=6, weight=0.65),
    dict(key='golden', name='Golden chest', unlock=3, key_id=0, xp=18, weight=0.25),
    dict(key='crystal', name='Crystal chest', unlock=8, key_id=1, xp=45, weight=0.10),
)
CHEST_BY_KEY = {c['key']: c for c in CHESTS}
KEY_NAMES = {0: 'Basic Key', 1: 'Crystal Key'}
GOBLINS = (
    dict(key='treasure', object='Goblin_Treasure_obj', name='Treasure Goblin', unlock=1, xp=25, weight=0.60),
    dict(key='rune', object='Goblin_Rune_obj', name='Rune Goblin', unlock=6, xp=35, weight=0.25),
    dict(key='shadow', object='Goblin_Shadow_obj', name='Shadow Goblin', unlock=12, xp=50, weight=0.15),
    dict(key='orb', object='Goblin_Orb_obj', name='Orb Goblin', unlock=18, xp=50, weight=0.10),
    dict(key='ore', object='Goblin_Ore_obj', name='Ore Goblin', unlock=24, xp=40, weight=0.10),
)
GOBLIN_BY_KEY = {g['key']: g for g in GOBLINS}
GOBLIN_BY_OBJECT = {g['object']: g for g in GOBLINS}

BASE_SEARCHES_PER_HOUR = 4.0      # chests an adventurer comes across per hour
BASE_ENCOUNTERS_PER_HOUR = 2.0    # loot goblins a hunter meets per hour
BASE_CATCH = 0.55                 # a goblin that is not caught flees (the game's goblins escape in ~10 s)
KEY_FINDS_PER_HOUR = {0: 0.25, 1: 0.04}
SPOILS_PER_CHEST, SPOILS_PER_GOBLIN = 25, 15

ADVENTURER_TREE = (
    dict(id='keen_scout', branch='Scouting', name='Keen Scout', max=5, requires=None, text='+8% chests found per hour per rank.'),
    dict(id='pathfinder', branch='Scouting', name='Pathfinder', max=3, requires=('keen_scout', 2), text='Trips finish 4% sooner per rank.'),
    dict(id='long_road', branch='Scouting', name='Long Road', max=4, requires=None, text='+1 hour maximum trip length per rank.'),
    dict(id='lockpicking', branch='Lockwork', name='Lockpicking', max=5, requires=None,
         text='3% chance per rank to open a locked chest without a key.'),
    dict(id='gentle_hands', branch='Lockwork', name='Gentle Hands', max=3, requires=('lockpicking', 2),
         text='6% chance per rank that a key is not used up.'),
    dict(id='treasure_sense', branch='Treasure', name='Treasure Sense', max=5, requires=None, text='+2% golden chests per rank.'),
    dict(id='crystal_sense', branch='Treasure', name='Crystal Sense', max=3, requires=('treasure_sense', 3), text='+1.5% crystal chests per rank.'),
    dict(id='second_look', branch='Treasure', name='Second Look', max=3, requires=('treasure_sense', 5),
         text='1.5% chance per rank that an opened chest is searched twice (a second drop).'),
    dict(id='key_finder', branch='Keys', name='Key Finder', max=5, requires=None, text='+20% keys found per rank.'),
    dict(id='veteran', branch='Mastery', name='Veteran', max=3, requires=None, text='+10% worker experience per rank.'),
    dict(id='spoils_hunter', branch='Mastery', name='Spoils Hunter', max=3, requires=None, text='+20% spoils per rank.'),
)
GOBLIN_TREE = (
    dict(id='tracker', branch='Tracking', name='Tracker', max=5, requires=None, text='+8% goblins met per hour per rank.'),
    dict(id='swift_feet', branch='Tracking', name='Swift Feet', max=3, requires=('tracker', 2), text='Trips finish 4% sooner per rank.'),
    dict(id='long_hunt', branch='Tracking', name='Long Hunt', max=4, requires=None, text='+1 hour maximum trip length per rank.'),
    dict(id='net_master', branch='Nets', name='Net Master', max=5, requires=None, text='+4% catch chance per rank.'),
    dict(id='bola', branch='Nets', name='Bola', max=3, requires=('net_master', 3), text='10% chance per rank to catch a fleeing goblin after all.'),
    dict(id='rune_scent', branch='Quarry', name='Rune Scent', max=4, requires=None, text='+5% rune goblins per rank (once unlocked).'),
    dict(id='shadow_step', branch='Quarry', name='Shadow Step', max=4, requires=('rune_scent', 2), text='+4% shadow goblins per rank (once unlocked).'),
    dict(id='veteran', branch='Mastery', name='Veteran', max=3, requires=None, text='+10% worker experience per rank.'),
    dict(id='trophy_hunter', branch='Mastery', name='Trophy Hunter', max=3, requires=None, text='+20% spoils per rank.'),
)
TREES = {'adventurer': ADVENTURER_TREE, 'goblin_hunter': GOBLIN_TREE}


def _rank(worker: dict, node: str) -> int:
    import workers
    return workers.ranks(worker, node)


# ------------------------------------------------------------------ packet pools
_POOL_CACHE: dict = {}


def current_build() -> str | None:
    import afk
    return (afk.read_json(afk.DATA / 'build.json', {}) or {}).get('game_build')


def pools(build: str | None = None) -> dict:
    """What the recorded packets offer, by region: chests by tier and goblins by kind.

    Only packets of the running game build with the protected drop values the
    replay needs count (the plugin refuses the others)."""
    import afk
    build = build if build is not None else current_build()
    files = sorted(afk.PACKETS.glob('*.json'))
    stamp = (str(afk.PACKETS), build, len(files), max((f.stat().st_mtime_ns for f in files), default=0))
    if _POOL_CACHE.get('stamp') == stamp:
        return _POOL_CACHE['value']
    chests, goblins = {}, {}
    for f in files:
        d = afk.read_json(f)
        if not isinstance(d, dict) or not build or d.get('game_build_id') != build:
            continue
        if not isinstance(d.get('protected'), dict) or not d.get('protected'):
            continue
        h, obj, room = d.get('packet_hash') or f.stem, d.get('self_object') or '', d.get('room')
        if not room:
            continue
        if obj == CHEST_OBJECT:
            args = d.get('args') or []
            tier = CHEST_TIERS.get(_first_number(args))
            if tier:
                chests.setdefault(room, {}).setdefault(tier, []).append(h)
        elif obj in GOBLIN_BY_OBJECT:
            goblins.setdefault(room, {}).setdefault(GOBLIN_BY_OBJECT[obj]['key'], []).append(h)
    value = dict(build=build, chests={r: {t: sorted(v) for t, v in m.items()} for r, m in chests.items()},
                 goblins={r: {t: sorted(v) for t, v in m.items()} for r, m in goblins.items()})
    _POOL_CACHE.update(stamp=stamp, value=value)
    return value


def _first_number(args) -> int | None:
    """The recorded first argument (a number, possibly wrapped by the capture)."""
    if not args:
        return None
    a = args[0]
    if isinstance(a, dict):
        a = a.get('value', a.get('number'))
    try:
        value = float(a)
    except (TypeError, ValueError):
        return None
    return int(value) if value.is_integer() else None


def regions(worker_type: str, pool: dict | None = None) -> dict:
    """Regions a worker of ``worker_type`` can be sent to: room -> what was recorded there."""
    pool = pool or pools()
    source = pool['chests'] if worker_type == 'adventurer' else pool['goblins']
    return {room: {k: len(v) for k, v in kinds.items()} for room, kinds in source.items() if kinds}


# ------------------------------------------------------------------ trips
def max_trip_hours(worker: dict) -> float:
    import traits
    node = 'long_road' if worker.get('type') == 'adventurer' else 'long_hunt'
    return 8.0 + _rank(worker, node) + traits.effects(worker.get('traits'))['max_hours']


def time_factor(worker: dict) -> float:
    import traits
    node = 'pathfinder' if worker.get('type') == 'adventurer' else 'swift_feet'
    return max(0.5, (1.0 - 0.04 * _rank(worker, node)) * (1.0 + traits.effects(worker.get('traits'))['time']))


def start_trip(state: dict, worker: dict, region, hours, at=None, seed=None, pool: dict | None = None) -> dict:
    """Send an adventurer or goblin hunter to a recorded region.

    An adventurer takes the rack's Basic and Crystal keys along (unused ones come back)."""
    import camp, math, secrets, workers
    kind = worker.get('type')
    options = regions(kind, pool)
    if region not in options:
        raise ValueError('Choose a region where ' + ('chests were' if kind == 'adventurer' else 'loot goblins were')
                         + ' recorded (play there with recording on to add one).')
    usable = [c['key'] for c in CHESTS if worker['level'] >= c['unlock']] if kind == 'adventurer' else \
        [g['key'] for g in GOBLINS if worker['level'] >= g['unlock']]
    if not any(k in options[region] for k in usable):
        raise ValueError(f"{worker['name']} cannot open or catch anything recorded there yet.")
    hours = float(hours)
    if not math.isfinite(hours) or not workers.MIN_TRIP_HOURS <= hours <= max_trip_hours(worker):
        raise ValueError(f"Trips last from 15 minutes to {max_trip_hours(worker):g} hours for {worker['name']}.")
    keys = {}
    if kind == 'adventurer':
        for key_id in (0, 1):
            n = state['camp']['keys'].get(str(key_id), 0)
            if n:
                keys[str(key_id)] = n
                state['camp']['keys'].pop(str(key_id))
    trip = dict(region=region, work_hours=hours, real_hours=round(hours * time_factor(worker), 6), started_at=workers.iso(at or workers.now_utc()),
                seed=seed if type(seed) is int else secrets.randbits(62), delivery=None, keys=keys,
                mods=workers.trip_mods(state, worker, region, hours, at=at), build=(pool or pools())['build'])
    worker['trip'] = trip
    return trip


def return_keys(state: dict, trip: dict, left: dict | None = None) -> dict:
    """Unused keys back onto the rack (all of them when a trip is cancelled)."""
    import camp
    back = dict(trip.get('keys') or {}) if left is None else dict(left)
    for k, n in back.items():
        state['camp']['keys'][str(k)] = state['camp']['keys'].get(str(k), 0) + int(n)
    return back


def haul(worker: dict, trip: dict, work_hours: float, pool: dict | None = None) -> dict:
    """What ``work_hours`` of the trip bring, rolled from the trip's own seed:
    which recorded packets replay how often, keys used and found, XP, spoils."""
    import workers
    pool = pool or pools(trip.get('build'))
    mods = dict(workers.NEUTRAL_MODS, **(trip.get('mods') or {}))
    rng = random.Random(trip['seed'])
    room = trip['region']
    if worker['type'] == 'adventurer':
        return _adventure(worker, trip, work_hours, pool['chests'].get(room, {}), mods, rng)
    return _hunt(worker, trip, work_hours, pool['goblins'].get(room, {}), mods, rng)


def _count(rng, expected: float) -> int:
    whole = int(expected)
    return whole + (1 if rng.random() < expected - whole else 0)


def _adventure(worker, trip, hours, chests, mods, rng) -> dict:
    searches = _count(rng, BASE_SEARCHES_PER_HOUR * (1 + 0.08 * _rank(worker, 'keen_scout')) * mods['speed'] * max(0.0, hours))
    odds = dict(wooden=0.0,
                golden=CHEST_BY_KEY['golden']['weight'] + 0.02 * _rank(worker, 'treasure_sense'),
                crystal=(CHEST_BY_KEY['crystal']['weight'] + 0.015 * _rank(worker, 'crystal_sense')) * mods['rare'])
    for tier in ('golden', 'crystal'):
        if worker['level'] < CHEST_BY_KEY[tier]['unlock'] or not chests.get(tier):
            odds[tier] = 0.0
    keys = {int(k): int(v) for k, v in (trip.get('keys') or {}).items()}
    pick_lock = 0.03 * _rank(worker, 'lockpicking') + mods['tool']
    keep_key = 0.06 * _rank(worker, 'gentle_hands')
    twice = 0.015 * _rank(worker, 'second_look')
    replays, opened, locked, used, picked = {}, dict(wooden=0, golden=0, crystal=0), 0, {0: 0, 1: 0}, 0
    for _ in range(searches):
        roll, tier = rng.random(), 'wooden'
        if roll < odds['crystal']:
            tier = 'crystal'
        elif roll < odds['crystal'] + odds['golden']:
            tier = 'golden'
        if tier == 'wooden' and not chests.get('wooden'):
            continue
        spec = CHEST_BY_KEY[tier]
        if spec['key_id'] is not None:
            if keys.get(spec['key_id'], 0) > 0:
                if not (keep_key and rng.random() < keep_key):
                    keys[spec['key_id']] -= 1
                    used[spec['key_id']] += 1
            elif pick_lock and rng.random() < pick_lock:
                picked += 1
            else:
                locked += 1
                continue
        h = rng.choice(chests[tier])
        replays[h] = replays.get(h, 0) + 1 + (1 if twice and rng.random() < twice else 0)
        opened[tier] += 1
    found = {}
    for key_id, per_hour in KEY_FINDS_PER_HOUR.items():
        n = _count(rng, per_hour * (1 + 0.20 * _rank(worker, 'key_finder')) * mods['amount'] * max(0.0, hours))
        if n:
            found[key_id] = n
    chests_opened = sum(opened.values())
    xp = int(round(sum(CHEST_BY_KEY[t]['xp'] * n for t, n in opened.items()) * (1 + 0.10 * _rank(worker, 'veteran')) * mods['xp']))
    spoils = int(round(SPOILS_PER_CHEST * chests_opened * (1 + 0.20 * _rank(worker, 'spoils_hunter')) * mods['amount']))
    left = {str(k): v for k, v in keys.items() if v > 0}
    return dict(work_hours=round(hours, 6), searches=searches, opened=opened, locked=locked, picked=picked,
                keys_used={str(k): v for k, v in used.items() if v}, keys_left=left, keys_found={str(k): v for k, v in found.items()},
                replays=replays, xp=xp, resources=dict(spoils=spoils))


def _hunt(worker, trip, hours, goblins, mods, rng) -> dict:
    met = _count(rng, BASE_ENCOUNTERS_PER_HOUR * (1 + 0.08 * _rank(worker, 'tracker')) * mods['speed'] * max(0.0, hours))
    weights = {}
    for g in GOBLINS:
        if worker['level'] >= g['unlock'] and goblins.get(g['key']):
            w = g['weight']
            if g['key'] == 'rune':
                w += 0.05 * _rank(worker, 'rune_scent')
            if g['key'] in ('shadow', 'orb'):
                w = (w + 0.04 * _rank(worker, 'shadow_step')) * mods['rare']
            weights[g['key']] = w
    catch = min(0.95, BASE_CATCH + 0.04 * _rank(worker, 'net_master') + mods['tool'])
    second = 0.10 * _rank(worker, 'bola')
    replays, caught, fled = {}, {}, 0
    total = sum(weights.values())
    for _ in range(met if total else 0):
        roll, kind = rng.random() * total, None
        for key, w in weights.items():
            roll -= w
            if roll < 0:
                kind = key
                break
        kind = kind or next(iter(weights))
        if rng.random() < catch or (second and rng.random() < second):
            h = rng.choice(goblins[kind])
            replays[h] = replays.get(h, 0) + 1
            caught[kind] = caught.get(kind, 0) + 1
        else:
            fled += 1
    n = sum(caught.values())
    xp = int(round(sum(GOBLIN_BY_KEY[k]['xp'] * c for k, c in caught.items()) * (1 + 0.10 * _rank(worker, 'veteran')) * mods['xp']))
    spoils = int(round(SPOILS_PER_GOBLIN * n * (1 + 0.20 * _rank(worker, 'trophy_hunter')) * mods['amount']))
    return dict(work_hours=round(hours, 6), met=met, caught=caught, fled=fled, replays=replays, xp=xp, resources=dict(spoils=spoils))


# ------------------------------------------------------------------ delivery
def delivery_plan(worker: dict, trip: dict, work_hours: float, character: dict, label: str, filtered: str, speed: str = 'normal',
                  at=None, pool: dict | None = None) -> dict:
    """The replay plan of a haul (planned once, never re-rolled): the recorded packets
    of the trip's region replayed through the game's drop routine, no XP, gold picked up."""
    import afk, workers
    result = haul(worker, trip, work_hours, pool)
    stamp = (at or workers.now_utc()).strftime('%Y%m%d_%H%M%S')
    ident = f"worker_{worker['id'][2:]}_{stamp}"
    room = trip['region']
    index = afk.packet_index(list(result['replays']))
    packets = [dict(hash=h, count=n, monster_key=(index.get(h) or {}).get('monster_key', ''), room=room,
                    kind='kill' if worker['type'] == 'goblin_hunter' else 'break', exp=0.0) for h, n in sorted(result['replays'].items())]
    kills = sum(p['count'] for p in packets if p['kind'] == 'kill')
    plan = dict(expedition_id=ident, created=workers.iso(at or workers.now_utc()), hours=0.0, extras=[], rate_source='worker',
                worker_id=worker['id'], worker_name=worker['name'], worker_type=worker['type'], level=worker['level'],
                zones=[dict(room=room, weight=1, minutes=0, kills=kills, breaks=sum(p['count'] for p in packets) - kills)],
                character=character, forgepact=afk.forgepact_current(), game_build=trip.get('build'), coverage=1.0,
                estimate_rates=dict(items_per_call={}, gold_per_call=None), farm_context=None,
                packets=packets, exp=False, gold='pickup', label=label, filtered_items=filtered, preview={},
                haul={k: v for k, v in result.items() if k != 'replays'}, planned_at=workers.iso(at or workers.now_utc()))
    afk.apply_delivery_speed(plan, speed)
    afk.rebuild_preview(plan)
    return plan


def apply(state: dict, worker: dict, plan: dict, result: dict, at=None) -> dict:
    """A delivered haul: XP, statistics, spoils, keys back onto the rack; the trip ends."""
    import camp, workers
    haul = plan['haul']
    before = worker['level']
    worker['xp'] = int(worker.get('xp', 0)) + int(haul['xp'])
    worker['level'] = workers.level_for(worker['xp'])[0]
    stats = worker.setdefault('stats', {})
    stats['trips'] = stats.get('trips', 0) + 1
    stats['hours'] = round(stats.get('hours', 0.0) + haul['work_hours'], 4)
    if worker['type'] == 'adventurer':
        chests = stats.setdefault('chests', {})
        for tier, n in haul['opened'].items():
            chests[tier] = chests.get(tier, 0) + n
        stats['locked'] = stats.get('locked', 0) + haul['locked']
        stats['picked'] = stats.get('picked', 0) + haul['picked']
        keys = stats.setdefault('keys_found', {})
        for k, n in haul['keys_found'].items():
            keys[k] = keys.get(k, 0) + n
        return_keys(state, worker['trip'] or {}, haul['keys_left'])
        rack = camp.add_keys(state['camp'], haul['keys_found'])
    else:
        caught = stats.setdefault('goblins_caught', {})
        for kind, n in haul['caught'].items():
            caught[kind] = caught.get(kind, 0) + n
        stats['fled'] = stats.get('fled', 0) + haul['fled']
        rack = {}
    kept = camp.add(state['camp'], haul['resources'])
    stats['spoils'] = stats.get('spoils', 0) + kept.get('spoils', 0)
    worker['trip'] = None
    entry = dict(delivery_id=plan['expedition_id'], at=workers.iso(at or workers.now_utc()), region=plan['zones'][0]['room'],
                 hours=haul['work_hours'], xp=haul['xp'], level_before=before, level_after=worker['level'],
                 calls=result.get('calls_done'), items=result.get('items'), gold=result.get('gold'), camp=kept, keys=rack)
    worker['history'] = ([entry] + list(worker.get('history') or []))[:20]
    return dict(worker=worker, levels=worker['level'] - before, entry=entry, camp=kept, keys=rack)
