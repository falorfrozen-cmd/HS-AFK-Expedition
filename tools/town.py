"""The town's fortifications and sieges in workers.json (0.9).

``state['town']`` holds:
- the towers (kind, level, place, target priority, specialisation);
- each side's ore plating;
- the walls' and keep's health, healing by the hour;
- the fortification queue;
- the siege under way, if any, the watch (sieges started one after another,
  towers only) and the last sieges;
- how many monsters of each kind the town has slain.
The camp's buildings set the limits:
- Walls: wall health, armor and tower slots;
- Headquarters: the keep and how many heroes can be stationed;
- Siege Workshop: the highest tower level and a second build site at level 4;
- Watchtower: how much of the next wave it scouts.

Like the camp, this is AFK FARM's own layer. Gold is paid through the game's
purchase path (the panel's receipts); stone, spoils and dust come from the
camp; ore and gems come from the camp's stock (taken from the Vault or brought
by miners). A build takes real time.

Standard library only.
"""
from __future__ import annotations

from datetime import timedelta

import battle
import camp as C
import fortifications as F
import goods

SCHEMA = 1
HISTORY = 20

# Game materials a fortification level takes from the camp's stock, by tier
# band (tower levels 1-3, 4-6, 7-8, 9-10): the ores miners dig (14:27-32), the
# jewel materials the Prospector turns them into (14:0-23), and at the top the
# Satanic Crystal (14:58) and Destiny Shard Fragment (14:66). Each pair is
# ("14:id", units at the band's first level); a band's later levels take one
# more share each.
TOWER_MATERIAL = {
    'ballista': ((('14:28', 30),), (('14:6', 20),), (('14:14', 12),), (('14:21', 6), ('14:58', 2))),
    'mortar': ((('14:27', 40),), (('14:29', 25),), (('14:10', 12),), (('14:17', 6), ('14:58', 2))),
    'brazier': ((('14:0', 30),), (('14:12', 20),), (('14:13', 12),), (('14:19', 6), ('14:58', 2))),
    'frost': ((('14:3', 30),), (('14:9', 20),), (('14:23', 12),), (('14:22', 6), ('14:58', 2))),
    'storm': ((('14:2', 30),), (('14:8', 20),), (('14:15', 12),), (('14:20', 6), ('14:58', 2))),
    'plague': ((('14:1', 30),), (('14:16', 20),), (('14:5', 12),), (('14:18', 6), ('14:58', 2))),
    'harpoon': ((('14:28', 30),), (('14:7', 20),), (('14:4', 12),), (('14:21', 6), ('14:58', 2))),
    'obelisk': ((('14:11', 30),), (('14:10', 20),), (('14:44', 6),), (('14:66', 4), ('14:58', 2))),
}
# Plating a wall: Copper, Iron, Gold, Jade, then Tarethium Ore.
PLATING_MATERIAL = ('14:27', '14:28', '14:29', '14:31', '14:32')


def new() -> dict:
    return dict(schema=SCHEMA, towers={}, next_tower=1, plating={s: 0 for s in battle.SIDES},
                walls={s: dict(hp=None, at=None) for s in battle.SIDES}, keep=dict(hp=None, at=None),
                queue=[], siege=None, watch=None, history=[], slain={}, sending=None)


def under_siege(town: dict) -> bool:
    """A siege runs (the pointer of a finished one stays, settled)."""
    siege = town.get('siege')
    return bool(siege) and not siege.get('settled')


def _int(value, low=0, high=None) -> int:
    value = value if type(value) is int else low
    value = max(low, value)
    return min(high, value) if high is not None else value


def normalize(town) -> dict:
    base = new()
    town = town if isinstance(town, dict) else {}
    out = dict(base, **{k: town[k] for k in base if k in town})
    towers = {}
    for tid, t in (out['towers'] if isinstance(out['towers'], dict) else {}).items():
        if not isinstance(t, dict) or t.get('kind') not in F.TOWER_BY_KEY:
            continue
        place = t.get('place') if t.get('place') in F.PLACES else 'keep'
        towers[str(tid)] = dict(id=str(tid), kind=t['kind'], level=_int(t.get('level'), 0, F.MAX_TOWER_LEVEL), place=place,
                                priority=t.get('priority') if t.get('priority') in battle.PRIORITIES else None,
                                perk=t.get('perk') if any(p['key'] == t.get('perk') for p in F.TOWER_BY_KEY[t['kind']]['perks']) else None,
                                built_at=t.get('built_at'))
    out['towers'] = towers
    out['next_tower'] = max(_int(out.get('next_tower'), 1), 1 + max((int(t[1:]) for t in towers if t[1:].isdigit()), default=0))
    plating = out['plating'] if isinstance(out['plating'], dict) else {}
    out['plating'] = {s: _int(plating.get(s), 0, F.MAX_PLATING) for s in battle.SIDES}
    walls = out['walls'] if isinstance(out['walls'], dict) else {}
    out['walls'] = {s: _health(walls.get(s)) for s in battle.SIDES}
    out['keep'] = _health(out['keep'])
    out['queue'] = [q for q in out['queue'] if isinstance(q, dict) and q.get('target')] if isinstance(out['queue'], list) else []
    out['siege'] = out['siege'] if isinstance(out['siege'], dict) and out['siege'].get('id') else None
    out['sending'] = out['sending'] if isinstance(out['sending'], dict) and out['sending'].get('delivery_id') else None
    out['watch'] = normalize_watch(out['watch'])
    rows = [h for h in out['history'] if isinstance(h, dict)] if isinstance(out['history'], list) else []
    out['history'] = [h for i, h in enumerate(rows) if i < HISTORY or not h.get('town_collected')]
    slain = out['slain'] if isinstance(out['slain'], dict) else {}
    out['slain'] = {str(k): v for k, v in slain.items() if type(v) is int and v > 0}
    out['schema'] = SCHEMA
    return out


def normalize_watch(value) -> dict | None:
    """The watch's settings, or None: a region, a level, hours and a stone budget per siege."""
    if not isinstance(value, dict) or not isinstance(value.get('room'), str):
        return None
    level, hours, stone = value.get('level'), value.get('hours'), value.get('stone', 0)
    if type(level) is not int or type(stone) is not int or isinstance(hours, bool) or not isinstance(hours, (int, float)):
        return None
    return dict(room=value['room'], level=level, hours=float(hours), stone=max(0, stone), since=value.get('since'),
                started=_int(value.get('started')), paused=value.get('paused') if isinstance(value.get('paused'), str) else None)


def keep_history(history: list, entry: dict) -> list:
    """The newest sieges first, HISTORY of them; a siege whose town share still waits is never dropped."""
    out = []
    for i, h in enumerate([entry] + list(history)):
        if i < HISTORY or not h.get('town_collected'):
            out.append(h)
    return out


def _health(value) -> dict:
    value = value if isinstance(value, dict) else {}
    hp = value.get('hp')
    return dict(hp=float(hp) if isinstance(hp, (int, float)) and not isinstance(hp, bool) and hp >= 0 else None,
                at=value.get('at') if isinstance(value.get('at'), str) else None)


# ------------------------------------------------------------------ limits from the camp
def limits(camp: dict) -> dict:
    walls, hq = C.level(camp, 'walls'), max(1, C.level(camp, 'hq'))
    workshop, tower = C.level(camp, 'workshop'), C.level(camp, 'watchtower')
    return dict(walls=walls, hq=hq, workshop=workshop, watchtower=tower, tower_slots=F.tower_slots(walls),
                tower_max=min(F.MAX_TOWER_LEVEL, 2 * workshop), hero_posts=F.hero_posts(hq), sites=2 if workshop >= 4 else 1)


# ------------------------------------------------------------------ health out of a siege
def wall_now(town: dict, camp: dict, side: str, at=None) -> float:
    lim = limits(camp)
    top = F.wall_max(lim['walls'], town['plating'][side])
    h = town['walls'][side]
    if h['hp'] is None or h['at'] is None:
        return top
    hours = max(0.0, ((at or C.now_utc()) - C.parse_iso(h['at'])).total_seconds() / 3600.0)
    return F.mend(min(top, h['hp']), top, hours)


def keep_now(town: dict, camp: dict, at=None) -> float:
    top = F.keep(limits(camp)['hq'])['max']
    h = town['keep']
    if h['hp'] is None or h['at'] is None:
        return top
    hours = max(0.0, ((at or C.now_utc()) - C.parse_iso(h['at'])).total_seconds() / 3600.0)
    return min(top, max(0.0, h['hp']) + top * F.HEAL_PER_HOUR * hours)


def set_health(town: dict, walls: dict, keep: float, at) -> None:
    for s in battle.SIDES:
        town['walls'][s] = dict(hp=round(float(walls[s]), 3), at=C.iso(at))
    town['keep'] = dict(hp=round(float(keep), 3), at=C.iso(at))


def repair_now(town: dict, camp: dict, stone: int, at=None) -> dict:
    """Spend camp stone out of a siege: the walls first (most hurt first), then the keep."""
    at = at or C.now_utc()
    if under_siege(town):
        raise ValueError('The town is under siege: repair between its waves instead.')
    lim = limits(camp)
    if type(stone) is not int or stone <= 0:
        raise ValueError('Choose how much stone to spend.')
    if camp['resources'].get('stone', 0) < stone:
        raise ValueError(f"The camp has only {camp['resources'].get('stone', 0):,} stone.")
    now = {s: wall_now(town, camp, s, at) for s in battle.SIDES}
    tops = {s: F.wall_max(lim['walls'], town['plating'][s]) for s in battle.SIDES}
    used, added = 0, {}
    for s in sorted((s for s in battle.SIDES if tops[s] > 0), key=lambda s: (now[s] / tops[s], s)):
        spend = min(F.repair_stone(tops[s] - now[s]), stone - used)
        if spend > 0:
            now[s] = min(tops[s], now[s] + spend * F.STONE_PER_REPAIR)
            added[s] = spend * F.STONE_PER_REPAIR
            used += spend
    keep = keep_now(town, camp, at)
    top = F.keep(lim['hq'])['max']
    spend = min(F.repair_stone(top - keep), stone - used)
    if spend > 0:
        keep = min(top, keep + spend * F.STONE_PER_REPAIR)
        added['keep'] = spend * F.STONE_PER_REPAIR
        used += spend
    if not used:
        raise ValueError('Every wall and the keep are whole.')
    camp['resources']['stone'] -= used
    set_health(town, now, keep, at)
    return dict(stone=used, walls=added)


# ------------------------------------------------------------------ what a level takes
def band(level: int) -> int:
    """The tier band of a tower level: 1-3, 4-6, 7-8, 9-10."""
    return 0 if level <= 3 else 1 if level <= 6 else 2 if level <= 8 else 3


def recipe(kind: str, level: int) -> dict:
    """Game materials (``"14:id" -> units``) one tower level takes from the camp's stock."""
    bands = TOWER_MATERIAL.get(kind) or ()
    if not bands or level < 1:
        return {}
    b = min(band(level), len(bands) - 1)
    step = 1 + level - (1, 4, 7, 9)[b]
    return {key: int(base * step) for key, base in bands[b]}


def plating_recipe(level: int) -> dict:
    key = PLATING_MATERIAL[min(level, len(PLATING_MATERIAL)) - 1]
    return {key: 40 * level}


def _stock_missing(camp: dict, items: dict) -> list[str]:
    out = []
    for key, n in items.items():
        have = camp['stock'].get(key, 0)
        if have < n:
            out.append(f'{n - have:,} {goods.name(key)}')
    return out


def _busy(town: dict, target: str) -> bool:
    return any(q['target'] == target for q in town['queue'])


def next_tower(town: dict, camp: dict, kind: str, place: str) -> dict:
    """A new tower of ``kind`` at ``place``: its cost and whether it can start now."""
    if kind not in F.TOWER_BY_KEY:
        raise ValueError('Unknown tower.')
    if place not in F.PLACES:
        raise ValueError('Choose a wall (north, east, south, west) or the keep.')
    lim = limits(camp)
    cost = F.tower_cost(1)
    hours = cost.pop('hours')
    items = recipe(kind, 1)
    blockers = []
    if lim['workshop'] < 1:
        blockers.append('needs a Siege Workshop')
    built = len(town['towers']) + sum(1 for q in town['queue'] if q['target'].startswith('new:'))
    if built >= lim['tower_slots']:
        blockers.append(f"every tower slot is taken ({lim['tower_slots']} at Walls level {lim['walls']})")
    return _finish_check(town, camp, lim, dict(target=f'new:{kind}:{place}', kind=kind, place=place, to=1, cost=cost, hours=hours,
                                                materials=items), blockers)


def next_upgrade(town: dict, camp: dict, tower_id: str) -> dict:
    t = town['towers'].get(tower_id)
    if not t:
        raise ValueError('No such tower.')
    lim = limits(camp)
    to = t['level'] + 1
    if to > F.MAX_TOWER_LEVEL:
        return dict(target=f'tower:{tower_id}', to=None, cost=None, hours=None, materials={}, blockers=['already at its highest level'])
    cost = F.tower_cost(to)
    hours = cost.pop('hours')
    blockers = []
    if to > lim['tower_max']:
        blockers.append(f"needs Siege Workshop level {(to + 1) // 2}")
    return _finish_check(town, camp, lim, dict(target=f'tower:{tower_id}', kind=t['kind'], to=to, cost=cost, hours=hours,
                                                materials=recipe(t['kind'], to)), blockers)


def next_plating(town: dict, camp: dict, side: str) -> dict:
    if side not in battle.SIDES:
        raise ValueError('Choose a wall: north, east, south or west.')
    lim = limits(camp)
    to = town['plating'][side] + 1
    if to > F.MAX_PLATING:
        return dict(target=f'plating:{side}', to=None, cost=None, hours=None, materials={}, blockers=['already fully plated'])
    cost = F.plating_cost(to)
    hours = cost.pop('hours')
    blockers = []
    if lim['walls'] < 1:
        blockers.append('needs Walls')
    if to > lim['walls']:
        blockers.append(f'needs Walls level {to}')
    return _finish_check(town, camp, lim, dict(target=f'plating:{side}', side=side, to=to, cost=cost, hours=hours,
                                                materials=plating_recipe(to)), blockers)


def _finish_check(town, camp, lim, plan, blockers) -> dict:
    if _busy(town, plan['target']):
        blockers.append('already being built')
    if len(town['queue']) >= lim['sites']:
        blockers.append('the Siege Workshop is busy')
    if under_siege(town):
        blockers.append('the town is under siege')
    missing = C.afford(camp, plan['cost']) + _stock_missing(camp, plan['materials'])
    if missing:
        blockers.append('missing ' + ', '.join(missing))
    plan['blockers'] = blockers
    return plan


def take_materials(camp: dict, items: dict) -> dict:
    missing = _stock_missing(camp, items)
    if missing:
        raise ValueError('Not enough in the camp stock: ' + ', '.join(missing) + ' missing.')
    for key, n in items.items():
        camp['stock'][key] -= n
        if not camp['stock'][key]:
            del camp['stock'][key]
    return dict(items)


def give_back_materials(camp: dict, items: dict) -> None:
    for key, n in (items or {}).items():
        if n > 0:
            camp['stock'][key] = camp['stock'].get(key, 0) + int(n)


def start(town: dict, plan: dict, request=None, at=None) -> dict:
    """Queue a fortification (its gold was paid and its resources taken already)."""
    at = at or C.now_utc()
    entry = dict(target=plan['target'], to=plan['to'], started_at=C.iso(at), ready_at=C.iso(at + timedelta(hours=plan['hours'])),
                 request=request)
    town['queue'].append(entry)
    return entry


def settle(town: dict, camp: dict, at=None) -> list[dict]:
    """Finish every fortification whose time is up. New work stands whole."""
    at = at or C.now_utc()
    done, left = [], []
    for q in town['queue']:
        if not (q.get('ready_at') and C.parse_iso(q['ready_at']) <= at):
            left.append(q)
            continue
        kind, _, rest = q['target'].partition(':')
        if kind == 'new':
            tower_kind, _, place = rest.partition(':')
            tid = f"t{town['next_tower']}"
            town['next_tower'] += 1
            town['towers'][tid] = dict(id=tid, kind=tower_kind, level=1, place=place, priority=None, perk=None, built_at=q['ready_at'])
            q = dict(q, tower=tid)
        elif kind == 'tower' and rest in town['towers']:
            town['towers'][rest]['level'] = max(town['towers'][rest]['level'], int(q['to']))
        elif kind == 'plating' and rest in battle.SIDES:
            town['plating'][rest] = max(town['plating'][rest], int(q['to']))
            town['walls'][rest] = dict(hp=None, at=None)          # the plated wall stands whole
        done.append(q)
    town['queue'] = left
    return done


def arrange(town: dict, tower_id: str, place=None, priority=None, perk=None) -> dict:
    """Move a tower, change what it aims at, or choose its specialisation (level 5+, once)."""
    t = town['towers'].get(tower_id)
    if not t:
        raise ValueError('No such tower.')
    if under_siege(town) and place is not None and place != t['place']:
        raise ValueError('Towers cannot move during a siege.')
    if place is not None:
        if place not in F.PLACES:
            raise ValueError('Choose a wall (north, east, south, west) or the keep.')
        t['place'] = place
    if priority is not None:
        if priority not in battle.PRIORITIES:
            raise ValueError('Choose a target: ' + ', '.join(battle.PRIORITIES) + '.')
        t['priority'] = priority
    if perk is not None:
        spec = F.TOWER_BY_KEY[t['kind']]
        if t['level'] < F.PERK_LEVEL:
            raise ValueError(f'A tower chooses its specialisation at level {F.PERK_LEVEL}.')
        if t.get('perk'):
            raise ValueError('This tower has chosen its specialisation.')
        if not any(p['key'] == perk for p in spec['perks']):
            raise ValueError('Choose one of this tower\'s specialisations.')
        t['perk'] = perk
    return t


# ------------------------------------------------------------------ view
def view(town: dict, camp: dict, at=None) -> dict:
    at = at or C.now_utc()
    lim = limits(camp)
    walls = {}
    for s in battle.SIDES:
        top = F.wall_max(lim['walls'], town['plating'][s])
        walls[s] = dict(hp=round(wall_now(town, camp, s, at), 1), max=top, armor=F.wall_armor(lim['walls'], town['plating'][s]),
                        plating=town['plating'][s], next_plating=_public_plan(next_plating(town, camp, s)))
    towers = []
    for tid, t in sorted(town['towers'].items(), key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 0):
        spec = F.TOWER_BY_KEY[t['kind']]
        stats = F.tower_stats(t)
        towers.append(dict(id=tid, kind=t['kind'], name=spec['name'], text=spec['text'], level=t['level'], place=t['place'],
                           priority=stats['priority'], perk=t.get('perk'), perks=[dict(p) for p in spec['perks']] if t['level'] >= F.PERK_LEVEL else [],
                           dps=round(stats['dps'], 3), dtype=stats['dtype'], reach=stats['reach'], targets=stats['targets'],
                           air=stats['air'], ground=stats['ground'], next=_public_plan(next_upgrade(town, camp, tid))))
    kinds = [dict(key=k['key'], name=k['name'], text=k['text'], dtype=k['dtype'], reach=k['reach'], targets=k['targets'], air=k['air'],
                  ground=k.get('ground', True), dps=k['dps'], perks=[dict(p) for p in k['perks']],
                  build=_public_plan(next_tower(town, camp, k['key'], 'keep'))) for k in F.TOWERS]
    queue = [dict(q, done_in_seconds=max(0, int((C.parse_iso(q['ready_at']) - at).total_seconds()))) for q in town['queue']]
    keep = F.keep(lim['hq'])
    return dict(limits=lim, walls=walls, keep=dict(hp=round(keep_now(town, camp, at), 1), max=keep['max'], dps=keep['dps']),
                towers=towers, tower_kinds=kinds, queue=queue, siege=town.get('siege'), history=town['history'][:10],
                slain=sum(town['slain'].values()), repair_stone_per_hp=1 / F.STONE_PER_REPAIR)


def _public_plan(plan: dict) -> dict:
    out = {k: v for k, v in plan.items() if k in ('to', 'cost', 'hours', 'blockers')}
    out['materials'] = [dict(key=k, name=goods.name(k), count=n) for k, n in (plan.get('materials') or {}).items()]
    return out
