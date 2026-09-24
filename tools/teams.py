"""Team trips (0.8): workers who set out together help each other.

A team is two to four idle workers (the Barracks sets how many) who leave at the
same time for the same number of hours. Each still does its own work at its own
target (an ore, a recorded region, a jewel recipe) and is collected as before;
together they earn a team experience bonus, Team Player auras reach teammates,
Lone Wolves work worse, and some crews have a synergy drawn from how the game's
own things go together (ore goblins ambush miners, goblins and chests of a region
draw each other). Synergies are AFK FARM's rules, like everything else a worker
does; what the game makes is unchanged.

Standard library only. Teams live in workers.json (``state['teams']``).
"""
from __future__ import annotations

import contextlib
import secrets

TEAM_XP = 0.10
SYNERGIES = (
    dict(key='goblin_patrol', name='Goblin Patrol', types=('miner', 'goblin_hunter'),
         text='Ore goblins ambush miners: the hunter meets 25% more goblins, the miners bring 10% more ore.',
         bonus=dict(goblin_hunter=dict(speed=0.25), miner=dict(amount=0.10))),
    dict(key='treasure_trail', name='Treasure Trail', types=('adventurer', 'goblin_hunter'),
         text="Goblins and chests of a region draw each other: rare finds are 20% more likely for both.",
         bonus=dict(adventurer=dict(rare=0.20), goblin_hunter=dict(rare=0.20))),
    dict(key='on_site_cutting', name='On-site Cutting', types=('miner', 'jeweler'),
         text='The jeweler cuts beside the miners: it works 20% faster, their rare finds are 10% more likely.',
         bonus=dict(jeweler=dict(speed=0.20), miner=dict(rare=0.10))),
    dict(key='deep_vein', name='Deep Vein', types=('adventurer', 'miner'),
         text='The adventurer scouts veins, the miners open paths: +10% ore, +10% chests found.',
         bonus=dict(miner=dict(amount=0.10), adventurer=dict(speed=0.10))),
    dict(key='full_caravan', name='Full Caravan', types=('miner', 'adventurer', 'goblin_hunter', 'jeweler'),
         text='All four trades together: everyone works 10% faster.',
         bonus={t: dict(speed=0.10) for t in ('miner', 'adventurer', 'goblin_hunter', 'jeweler')}),
)

# The team a trip is started for, read by workers.trip_mods while start() runs.
CURRENT: dict | None = None


def active(types) -> list[dict]:
    """The synergies a crew of these worker types has."""
    have = set(types)
    return [s for s in SYNERGIES if set(s['types']) <= have]


def bonus_for(worker_type: str, synergies: list[dict]) -> dict:
    total = dict(speed=0.0, amount=0.0, rare=0.0)
    for s in synergies:
        for key, value in (s['bonus'].get(worker_type) or {}).items():
            total[key] += value
    return total


@contextlib.contextmanager
def starting(context: dict):
    global CURRENT
    CURRENT = context
    try:
        yield
    finally:
        CURRENT = None


def start(state: dict, members: list[dict], hours, at=None, pool=None, recipes=None) -> dict:
    """Send a team out. ``members``: [{worker, target}] (an ore id, a region or a recipe index).

    Every member's trip starts, or none does (a refusal undoes the ones started)."""
    import camp, traits, workers
    size = camp.effects(state['camp'])['team_size']
    ids = [m.get('worker') for m in members if isinstance(m, dict)]
    if not 2 <= len(ids) <= size:
        raise ValueError(f'A team is 2 to {size} workers (the Barracks sets the size).')
    if len(set(ids)) != len(ids):
        raise ValueError('A worker can only join a team once.')
    crew = [workers.find(state, i) for i in ids]
    busy = [w['name'] for w in crew if w.get('trip')]
    if busy:
        raise ValueError(', '.join(busy) + ' ' + ('is' if len(busy) == 1 else 'are') + ' already on a trip.')
    synergies = active(w.get('type', 'miner') for w in crew)
    auras = {w['id']: traits.effects(w.get('traits'))['team_aura'] for w in crew}
    team_id = 't_' + secrets.token_hex(4)
    started = []
    try:
        for w, m in zip(crew, members):
            aura = sum(v for i, v in auras.items() if i != w['id'])
            context = dict(team=team_id, bonus=bonus_for(w.get('type', 'miner'), synergies), aura=aura, xp=TEAM_XP)
            with starting(context):
                trip = workers.start_trip(state, w['id'], m.get('target'), hours, at=at, pool=pool, recipes=recipes)
            trip['team'] = team_id
            started.append(w['id'])
    except (ValueError, KeyError, TypeError):
        for i in started:
            workers.cancel_trip(state, i)
        raise
    team = dict(id=team_id, members=ids, types=[w.get('type', 'miner') for w in crew], hours=float(hours),
                started_at=workers.iso(at or workers.now_utc()), synergies=[s['key'] for s in synergies])
    state.setdefault('teams', {})[team_id] = team
    return team


def view(state: dict) -> list[dict]:
    """Teams still out (a team ends when every member is back and collected)."""
    import workers
    out = []
    for team in list((state.get('teams') or {}).values()):
        members = [w for w in state['workers'] if (w.get('trip') or {}).get('team') == team['id']]
        if not members:
            continue
        out.append(dict(team, out=[w['id'] for w in members], names={w['id']: w['name'] for w in state['workers'] if w['id'] in team['members']},
                        synergy_names=[s['name'] for s in SYNERGIES if s['key'] in team['synergies']]))
    return out


def tidy(state: dict) -> None:
    """Forget teams whose members are all back."""
    teams = state.get('teams') or {}
    for team_id in list(teams):
        if not any((w.get('trip') or {}).get('team') == team_id for w in state['workers']):
            teams.pop(team_id)
