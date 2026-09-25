"""The Jeweler (0.8): the game's own jewelcrafting recipes, worked from the camp's stock.

The game crafts jewels and gems at the craft cube from the jewel materials the
Prospector makes (its recipe table, result types 37-41: 19 recipes, all certain).
The plugin reads that table from the running game (`afk worker recipes`); the
Jeweler works through recipes from the camp's material stock (the Gem Sense
share miners route there, never made in the game) and every jewel it makes is
created by the game's own ground-drop routine at delivery, like a miner's ore.
How fast it works, its levels and bonuses are AFK FARM's rules. It does not
touch the hero's own Jewelcrafting level.

Standard library only.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

TIERS = (   # result type -> Jeweler's Bench level and worker level it needs; XP per craft
    dict(result_type=37, tier=1, level=1, xp=10, name='Tier 1 jewels'),
    dict(result_type=38, tier=2, level=8, xp=20, name='Tier 2 jewels'),
    dict(result_type=39, tier=3, level=16, xp=35, name='Tier 3 jewels'),
    dict(result_type=40, tier=4, level=24, xp=55, name='Tier 4 jewels'),
    dict(result_type=41, tier=5, level=32, xp=80, name='Gems'),
)
TIER_BY_TYPE = {t['result_type']: t for t in TIERS}
BASE_CRAFTS_PER_HOUR = 6.0
DUST_PER_MATERIAL, DUST_PER_JEWEL = 1, 5
# Socketable (type 15) base ids, as the Item Editor catalog names them.
JEWEL_NAMES = {78: 'Angelic Gem', 79: 'Chaos Gem', 80: 'Elemental Gem', 81: 'Moonstone Gem', 82: 'Exan Jewel', 83: 'Wilrden Jewel',
               84: 'Volcon Jewel', 85: 'Aether Jewel', 86: 'Helmon Jewel', 87: 'Mariane Jewel', 88: 'Lyrcon Jewel', 89: 'Fieryzen Jewel',
               90: 'Lapis-Lazuli Jewel', 91: 'Omnipearl Jewel', 92: 'Agathetheum Jewel', 93: 'Tramal Jewel', 94: 'Pearlescento Jewel',
               95: 'Mythgonlion Jewel', 96: 'Clean Cut Jewel'}
# Jewel materials (type 14) the recipes take, as the Item Editor catalog names them.
MATERIAL_NAMES = {0: 'Bloodstone', 1: 'Snake Tooth', 2: 'Copperstone', 3: 'Liquate', 4: 'Darkstone', 5: 'Lesser Impstone', 6: 'Iron Opal',
                  7: 'Shadow Stone', 8: 'Twilight Citrine', 9: 'Ocean Agate', 10: 'Greater Impstone', 11: 'Huge Spessarite', 12: 'Cardinal Ruby',
                  13: 'Flaming Core', 14: 'Demon Tooth', 15: 'Ketamineral', 16: 'Jadenium Powder', 17: 'Satans Nail', 18: 'Inferno Stone',
                  19: 'Hellstar', 20: 'Tarethium Core', 21: 'Dark Matter', 22: 'Demon Soulstone', 23: 'Storm Opal', 44: 'Enchanted Sigil'}
RECIPES = 'jewel-recipes.json'

TREE = (
    dict(id='steady_hands', branch='Craft', name='Steady Hands', max=5, requires=None, text='+8% crafts per hour per rank.'),
    dict(id='long_bench', branch='Craft', name='Long Bench', max=4, requires=None, text='+1 hour maximum session per rank.'),
    dict(id='quick_polish', branch='Craft', name='Quick Polish', max=3, requires=('steady_hands', 2), text='Sessions finish 4% sooner per rank.'),
    dict(id='keen_cut', branch='Cutting', name='Keen Cut', max=5, requires=None, text='3% chance per rank that a craft uses no materials.'),
    dict(id='perfect_cut', branch='Cutting', name='Perfect Cut', max=3, requires=('keen_cut', 2), text='3% chance per rank of a second jewel.'),
    dict(id='dust_collector', branch='Workshop', name='Dust Collector', max=3, requires=None, text='+25% gem dust per rank.'),
    dict(id='veteran', branch='Mastery', name='Veteran', max=3, requires=None, text='+10% worker experience per rank.'),
)


def _rank(worker, node):
    import workers
    return workers.ranks(worker, node)


def key(type_, id_) -> str:
    return f'{int(type_)}:{int(id_)}'


# ------------------------------------------------------------------ recipes
def load_recipes(data) -> dict:
    """The recipes the plugin last read from the game: {build, recipes [...], at} (empty when never read)."""
    try:
        value = json.loads((Path(data) / 'models' / RECIPES).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(build=None, recipes=[], at=None)
    return value if isinstance(value, dict) and isinstance(value.get('recipes'), list) else dict(build=None, recipes=[], at=None)


def keep_recipes(data, reply: dict, build) -> dict:
    """Store the plugin's `worker recipes` answer for this game build."""
    rows = []
    for r in reply.get('recipes') or []:
        try:
            out = r['output']; ins = r['inputs']
            if r['result_type'] in TIER_BY_TYPE and out['type'] == 15 and 78 <= out['id'] <= 96 and ins:
                rows.append(dict(index=int(r['index']), result_type=int(r['result_type']), output=dict(type=15, id=int(out['id']), amount=int(out['amount'])),
                                 inputs=[dict(type=int(i['type']), id=int(i['id']), amount=int(i['amount'])) for i in ins]))
        except (KeyError, TypeError, ValueError):
            continue
    value = dict(schema=1, build=build, recipes=rows, at=reply.get('at'))
    path = Path(data) / 'models' / RECIPES
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1), encoding='utf-8')
    return value


def recipe_view(recipe: dict) -> dict:
    tier = TIER_BY_TYPE[recipe['result_type']]
    return dict(recipe, name=JEWEL_NAMES.get(recipe['output']['id'], f"Socketable {recipe['output']['id']}"), tier=tier['tier'], level=tier['level'])


def usable(worker: dict, recipe: dict, bench: int) -> bool:
    tier = TIER_BY_TYPE.get(recipe['result_type'])
    return bool(tier) and bench >= tier['tier'] and worker['level'] >= tier['level']


# ------------------------------------------------------------------ sessions
def max_trip_hours(worker: dict) -> float:
    import traits
    return 8.0 + _rank(worker, 'long_bench') + traits.effects(worker.get('traits'))['max_hours']


def time_factor(worker: dict) -> float:
    import traits
    return max(0.5, (1.0 - 0.04 * _rank(worker, 'quick_polish')) * (1.0 + traits.effects(worker.get('traits'))['time']))


def affordable(stock: dict, recipe: dict) -> int:
    return min((stock.get(key(i['type'], i['id']), 0) // i['amount'] for i in recipe['inputs']), default=0)


def start_trip(state: dict, worker: dict, recipe_index, hours, recipes: dict, at=None, seed=None) -> dict:
    """A crafting session on one recipe: the materials for every planned craft leave the stock now."""
    import camp, math, secrets, workers
    recipe = next((r for r in recipes.get('recipes') or [] if r['index'] == recipe_index), None)
    if not recipe:
        raise ValueError("Choose one of the game's jewel recipes (open the game once so AFK FARM can read them).")
    bench = camp.effects(state['camp'])['recipe_tier']
    if not usable(worker, recipe, bench):
        tier = TIER_BY_TYPE[recipe['result_type']]
        raise ValueError(f"{recipe_view(recipe)['name']} needs Jeweler's Bench level {tier['tier']} and a level {tier['level']} jeweler.")
    hours = float(hours)
    if not math.isfinite(hours) or not workers.MIN_TRIP_HOURS <= hours <= max_trip_hours(worker):
        raise ValueError(f"Sessions last from 15 minutes to {max_trip_hours(worker):g} hours for {worker['name']}.")
    mods = workers.trip_mods(state, worker, f"r{recipe_index}", hours, at=at)
    wanted = int(BASE_CRAFTS_PER_HOUR * (1 + 0.08 * _rank(worker, 'steady_hands')) * mods['speed'] * hours)
    planned = min(wanted, affordable(state['camp']['stock'], recipe))
    if planned < 1:
        need = ', '.join(f"{i['amount']} x {i['type']}:{i['id']}" for i in recipe['inputs'])
        raise ValueError(f"The camp's stock cannot pay for one {recipe_view(recipe)['name']} ({need}). Send miners' Gem Sense materials to the stock.")
    taken = {}
    for i in recipe['inputs']:
        k = key(i['type'], i['id'])
        taken[k] = i['amount'] * planned
        state['camp']['stock'][k] -= taken[k]
        if not state['camp']['stock'][k]:
            state['camp']['stock'].pop(k)
    trip = dict(recipe=recipe_index, recipe_name=recipe_view(recipe)['name'], result_type=recipe['result_type'], planned=planned, taken=taken,
                work_hours=hours, real_hours=round(hours * time_factor(worker), 6), started_at=workers.iso(at or workers.now_utc()),
                seed=seed if type(seed) is int else secrets.randbits(62), delivery=None, mods=mods, build=recipes.get('build'),
                double=camp.effects(state['camp'])['double_output'])
    worker['trip'] = trip
    return trip


def return_stock(state: dict, trip: dict, share: float = 0.0) -> dict:
    """Materials of crafts not made (all of them when the session is cancelled) go back to the stock."""
    back = {}
    for k, n in (trip.get('taken') or {}).items():
        keep = int(round(n * (1 - share)))
        if keep:
            state['camp']['stock'][k] = state['camp']['stock'].get(k, 0) + keep
            back[k] = keep
    return back


def haul(worker: dict, trip: dict, work_hours: float) -> dict:
    """What ``work_hours`` of the session make, rolled from its own seed."""
    import workers
    mods = dict(workers.NEUTRAL_MODS, **(trip.get('mods') or {}))
    rng = random.Random(trip['seed'])
    share = min(1.0, max(0.0, work_hours / trip['work_hours'])) if trip['work_hours'] else 1.0
    crafts = int(trip['planned'] * share)
    free = 0.03 * _rank(worker, 'keen_cut')
    second = trip.get('double', 0.0) + 0.03 * _rank(worker, 'perfect_cut') + mods['tool']
    saved, extra = 0, 0
    for _ in range(crafts):
        if free and rng.random() < free:
            saved += 1
        if second and rng.random() < second:
            extra += 1
    used = crafts - saved
    per = {k: v // trip['planned'] for k, v in (trip.get('taken') or {}).items()}
    materials = sum(n * used for n in per.values())
    xp = int(round(TIER_BY_TYPE[trip['result_type']]['xp'] * crafts * (1 + 0.10 * _rank(worker, 'veteran')) * mods['xp']))
    dust = int(round((DUST_PER_MATERIAL * materials + DUST_PER_JEWEL * (crafts + extra)) * (1 + 0.25 * _rank(worker, 'dust_collector')) * mods['amount']))
    return dict(work_hours=round(work_hours, 6), craft_count=crafts, extra=extra, saved=saved, used=used, materials=materials,
                make=crafts + extra, xp=xp, resources=dict(dust=dust), share=round(crafts / trip['planned'], 6) if trip['planned'] else 0.0)


def delivery_plan(worker: dict, trip: dict, work_hours: float, at=None) -> dict:
    """The plugin's delivery file: the game's recipe by index, how many (`worker deliver` crafts)."""
    import workers
    result = haul(worker, trip, work_hours)
    stamp = (at or workers.now_utc()).strftime('%Y%m%d_%H%M%S')
    return dict(schema=1, delivery_id=f"worker_{worker['id'][2:]}_{stamp}", worker_id=worker['id'], worker_name=worker['name'], level=worker['level'],
                planned_at=workers.iso(at or workers.now_utc()), items=[], prospect={},
                crafts=[dict(recipe=trip['recipe'], count=result['make'])] if result['make'] else [], **result)


def apply(state: dict, worker: dict, plan: dict, result: dict, at=None) -> dict:
    """A delivered session: XP, statistics, gem dust; unused materials go back to the stock."""
    import camp, workers
    trip = worker['trip'] or {}
    before = worker['level']
    worker['xp'] = int(worker.get('xp', 0)) + int(plan['xp'])
    worker['level'] = workers.level_for(worker['xp'])[0]
    stats = worker.setdefault('stats', {})
    stats['trips'] = stats.get('trips', 0) + 1
    stats['hours'] = round(stats.get('hours', 0.0) + plan['work_hours'], 4)
    stats['crafts'] = stats.get('crafts', 0) + plan['craft_count']
    made = stats.setdefault('jewels_made', {})
    for k, n in (result.get('crafted') or {}).items():
        made[k] = made.get(k, 0) + int(n)
    # Materials of crafts not made, and of the ones Keen Cut saved, return to the stock.
    per = {k: v // trip['planned'] for k, v in (trip.get('taken') or {}).items()} if trip.get('planned') else {}
    back = {}
    for k, n in per.items():
        keep = n * (trip['planned'] - plan['used'])
        if keep:
            state['camp']['stock'][k] = state['camp']['stock'].get(k, 0) + keep
            back[k] = keep
    kept = camp.add(state['camp'], plan['resources'])
    stats['dust'] = stats.get('dust', 0) + kept.get('dust', 0)
    worker['trip'] = None
    entry = dict(delivery_id=plan['delivery_id'], at=workers.iso(at or workers.now_utc()), recipe=trip.get('recipe_name'), hours=plan['work_hours'],
                 crafts=plan['craft_count'], extra=plan['extra'], xp=plan['xp'], level_before=before, level_after=worker['level'],
                 created=result.get('crafted'), camp=kept, stock_back=back)
    worker['history'] = ([entry] + list(worker.get('history') or []))[:20]
    return dict(worker=worker, levels=worker['level'] - before, entry=entry, camp=kept, stock_back=back)
