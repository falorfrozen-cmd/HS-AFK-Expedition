"""Fortifications (0.9): the town's walls, keep and towers.

AFK FARM's own game layer, like the camp's buildings. They decide how a siege
goes (battle.py); they never create anything in the game. They are built and
upgraded like camp buildings, with:
- gold, through the game's purchase path with one receipt per request;
- camp resources (stone, spoils, dust);
- game materials the town took out of the Vault into its stock.

Walls. The camp's Walls building sets each side's wall health and armor, and
how many tower slots the town has. Each side can be plated with ore: a
plating level adds health and armor to that wall only. Masons mend every wall
a little between waves; out of a siege a wall heals by the hour, or at once
for stone.

The keep. Headquarters sets its health and the guns that shoot whatever gets
inside. Headquarters also sets how many heroes can be stationed.

Towers stand at a side's wall or at the keep (every side, farther back). Each
kind has a role, a damage type and a reach. Levels 1-10 multiply the damage;
level 5 chooses a specialisation.

Health and damage are in the region's unit (bestiary.py): a level-1 Ballista
shoots two rank-1 monsters' worth of health per second.

Standard library only. The fortifications live in workers.json (``state['town']``).
"""
from __future__ import annotations

import math

import battle

K, M = 1_000, 1_000_000
SIDES = battle.SIDES
PLACES = SIDES + ('keep',)
MAX_TOWER_LEVEL = 10
TOWER_GROWTH = 1.75            # damage per tower level (about six siege levels per two tower levels)
MAX_PLATING = 5

# ------------------------------------------------------------------ towers
# dps: health per second at level 1; targets: monsters hit at once; slow: the
# share of speed a hit takes for two seconds; pierce: the share of a resistance
# ignored; dispel: strips shields three times as fast.
TOWERS = (
    dict(key='ballista', name='Ballista', dtype='physical', dps=2.0, reach=60, min_reach=0, targets=1, air=True, ground=True,
         priority='strongest', text='Heavy bolts at the toughest monster in reach. Hits flyers.',
         perks=(dict(key='piercing', name='Piercing Bolts', text='Ignores half of any physical resistance.', pierce=0.5),
                dict(key='twin', name='Twin Bolts', text='Hits two monsters at once.', targets=2))),
    dict(key='mortar', name='Mortar', dtype='physical', dps=0.55, reach=75, min_reach=15, targets=6, air=False, ground=True,
         priority='first', text='Shells whole packs far out. Cannot hit anything close to the wall or flying.',
         perks=(dict(key='cluster', name='Cluster Shells', text='Hits nine monsters at once.', targets=9),
                dict(key='long_barrel', name='Long Barrel', text='Reaches 20 farther.', reach=20))),
    dict(key='brazier', name='Fire Brazier', dtype='fire', dps=0.9, reach=25, min_reach=0, targets=5, air=False, ground=True,
         priority='first', text='Burns packs at the wall. Fire stops regeneration.',
         perks=(dict(key='inferno', name='Inferno', text='Hits eight monsters at once.', targets=8),
                dict(key='white_flame', name='White Flame', text='Ignores half of any fire resistance.', pierce=0.5))),
    dict(key='frost', name='Frost Spire', dtype='cold', dps=0.45, reach=40, min_reach=0, targets=3, air=True, ground=True, slow=0.30,
         priority='first', text='Slows every monster it hits, so the other towers get more time.',
         perks=(dict(key='deep_freeze', name='Deep Freeze', text='Slows by half instead of 30%.', slow=0.20),
                dict(key='hail', name='Hailstorm', text='Hits five monsters at once.', targets=5))),
    dict(key='storm', name='Storm Coil', dtype='lightning', dps=0.7, reach=45, min_reach=0, targets=4, air=True, ground=True,
         priority='first', text='Chains lightning through a pack. Hits flyers.',
         perks=(dict(key='arc', name='Arc Chain', text='Chains through six monsters.', targets=6),
                dict(key='overcharge', name='Overcharge', text='30% more damage.', dps_mult=1.3))),
    dict(key='plague', name='Plague Totem', dtype='poison', dps=0.7, reach=35, min_reach=0, targets=4, air=False, ground=True,
         priority='strongest', text='Poisons a pack. Poison stops regeneration.',
         perks=(dict(key='miasma', name='Miasma', text='Poisons six monsters at once.', targets=6),
                dict(key='virulent', name='Virulent', text='Ignores half of any poison resistance.', pierce=0.5))),
    dict(key='harpoon', name='Sky Harpoon', dtype='physical', dps=4.0, reach=60, min_reach=0, targets=1, air=True, ground=False,
         priority='flying', text='Only hits flyers, and hits them hard.',
         perks=(dict(key='barbed', name='Barbed Hooks', text='Hits two flyers at once.', targets=2),
                dict(key='net', name='Net Launcher', text='Slows the flyers it hits by 40%.', slow=0.40))),
    dict(key='obelisk', name='Arcane Obelisk', dtype='holy', dps=1.1, reach=40, min_reach=0, targets=2, air=True, ground=True, dispel=True,
         priority='elite', text='Holy fire no monster resists. Strips shields three times as fast; aims at the rarest monster.',
         perks=(dict(key='judgement', name='Judgement', text='40% more damage to the rarest monster in reach.', dps_mult=1.4),
                dict(key='radiance', name='Radiance', text='Hits four monsters at once.', targets=4))),
)
TOWER_BY_KEY = {t['key']: t for t in TOWERS}
PERK_LEVEL = 5


def tower_cost(level: int) -> dict:
    """What building (level 1) or raising a tower to ``level`` costs, before materials."""
    n = level - 1
    return dict(gold=int(round(100 * K * 1.8 ** n, -3)), stone=int(round(120 * 1.6 ** n, -1)),
                spoils=int(round(40 * 1.6 ** n, -1)) if level >= 3 else 0,
                dust=int(round(15 * 1.6 ** n, -1)) if level >= 6 else 0,
                hours=round(min(24.0, 0.5 * 1.5 ** n), 2))


def tower_stats(tower: dict) -> dict:
    """A built tower as a battle shooter: its kind, level, place, priority and perk."""
    spec = TOWER_BY_KEY[tower['kind']]
    level = max(1, min(MAX_TOWER_LEVEL, int(tower.get('level', 1))))
    out = dict(id=tower['id'], kind=spec['key'], name=spec['name'], side=tower.get('place', 'north'), dtype=spec['dtype'],
               dps=spec['dps'] * TOWER_GROWTH ** (level - 1), reach=float(spec['reach']), min_reach=float(spec['min_reach']),
               targets=int(spec['targets']), air=bool(spec['air']), ground=bool(spec.get('ground', True)),
               slow=float(spec.get('slow', 0.0)), pierce=0.0, dispel=bool(spec.get('dispel', False)),
               priority=tower.get('priority') if tower.get('priority') in battle.PRIORITIES else spec['priority'], level=level)
    if out['side'] == 'keep':
        out['reach'] += battle.KEEP_DISTANCE     # a keep tower stands farther back; it reaches as far past the wall
    perk = next((p for p in spec['perks'] if p['key'] == tower.get('perk')), None) if level >= PERK_LEVEL else None
    if perk:
        out['targets'] = int(perk.get('targets', out['targets']))
        out['reach'] += float(perk.get('reach', 0.0))
        out['pierce'] = max(out['pierce'], float(perk.get('pierce', 0.0)))
        out['slow'] = min(battle.MAX_SLOW, out['slow'] + float(perk.get('slow', 0.0)))
        out['dps'] *= float(perk.get('dps_mult', 1.0))
        out['perk'] = perk['key']
    return out


# ------------------------------------------------------------------ walls and keep
# The camp's Walls level (0-5): each side's wall, its armor, tower slots and masons.
WALL_HP = (0.0, 1_000.0, 2_500.0, 6_000.0, 15_000.0, 40_000.0)
WALL_ARMOR = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25)
TOWER_SLOTS = (1, 2, 4, 6, 8, 10)          # a lone keep tower before any wall stands
MASONS = (0.0, 0.02, 0.03, 0.04, 0.05, 0.06)   # share of a wall's health mended between waves
PLATING_HP, PLATING_ARMOR = 0.10, 0.02     # per plating level, on that side only
HEAL_PER_HOUR = 0.05                       # out of a siege
STONE_PER_REPAIR = 10.0                    # wall health one stone mends
# Headquarters (1-5): the keep, its guns and the heroes that can be stationed.
KEEP_HP = (2_000.0, 5_000.0, 12_500.0, 31_000.0, 78_000.0)
KEEP_DPS = (2.0, 3.6, 6.5, 11.7, 21.0)
HERO_POSTS = (0, 1, 1, 2, 3)


def wall_max(walls_level: int, plating: int = 0) -> float:
    return round(WALL_HP[max(0, min(5, walls_level))] * (1.0 + PLATING_HP * max(0, min(MAX_PLATING, plating))), 2)


def wall_armor(walls_level: int, plating: int = 0) -> float:
    return min(0.9, WALL_ARMOR[max(0, min(5, walls_level))] + PLATING_ARMOR * max(0, min(MAX_PLATING, plating)))


def plating_cost(level: int) -> dict:
    """Plating a side up to ``level`` (1-5): stone and gold; the ore comes from the recipe table."""
    n = level - 1
    return dict(gold=int(round(250 * K * 2.0 ** n, -3)), stone=int(round(300 * 1.8 ** n, -1)), hours=round(1.0 + 1.5 * n, 2))


def keep(hq_level: int) -> dict:
    i = max(1, min(5, hq_level)) - 1
    return dict(max=KEEP_HP[i], dps=KEEP_DPS[i])


def hero_posts(hq_level: int) -> int:
    return HERO_POSTS[max(1, min(5, hq_level)) - 1]


def tower_slots(walls_level: int) -> int:
    return TOWER_SLOTS[max(0, min(5, walls_level))]


def mend(hp: float, maximum: float, hours: float) -> float:
    """A wall out of a siege, ``hours`` later."""
    if maximum <= 0:
        return 0.0
    return min(maximum, max(0.0, hp) + maximum * HEAL_PER_HOUR * max(0.0, hours))


def repair_stone(missing: float) -> int:
    return max(0, math.ceil(max(0.0, missing) / STONE_PER_REPAIR - 1e-9))
