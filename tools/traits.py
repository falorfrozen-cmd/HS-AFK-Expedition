"""Worker traits (0.8): what a worker is born with.

A worker rolls one trait when it is hired and, 30% of the time, a quirk with an
upside and a downside. Traits are AFK FARM's own game layer, like levels and
skills: they change a worker's numbers (speed, amounts, experience, trip
length, rare-find chances), never what the game creates. Skills are chosen as a
worker levels up; a trait is fixed until the Tavern retrains it.

Standard library only.
"""
from __future__ import annotations

import random

RARITIES = ('common', 'rare', 'epic', 'legendary')
# Chance of each rarity for a new candidate before the Tavern's multipliers.
BASE_ODDS = dict(common=0.60, rare=0.30, epic=0.10, legendary=0.0)
QUIRK_CHANCE = 0.30

# Effects are fractions: speed +0.08 = 8% more work per hour; time -0.08 = trips
# finish 8% sooner; rare 0.30 = rare-find chances x1.3. `long`/`short` apply to
# trips of at least 6 hours / at most 2 hours. `solo`/`team` apply alone / in a team
# trip, `team_aura` to every teammate. `max_hours` adds trip hours, `points` adds
# skill points at levels 10/20/30, `ranks` raises every skill's highest rank.
TRAITS = (
    dict(id='diligent', name='Diligent', rarity='common', text='Works 8% faster.', speed=0.08),
    dict(id='strong_back', name='Strong Back', rarity='common', text='Brings 10% more.', amount=0.10),
    dict(id='quick_learner', name='Quick Learner', rarity='common', text='Earns 15% more experience.', xp=0.15),
    dict(id='frugal', name='Frugal', rarity='common', text='Tools and retraining cost 15% less.', cost=-0.15),
    dict(id='early_riser', name='Early Riser', rarity='common', text='Trips finish 8% sooner.', time=-0.08),
    dict(id='lucky', name='Lucky', rarity='rare', text='Rare finds are 1.3 times as likely.', rare=0.30),
    dict(id='night_owl', name='Night Owl', rarity='rare', text='Works 20% faster on trips of 6 hours or more, 10% slower on trips of 2 hours or less.',
         long=0.20, short=-0.10),
    dict(id='team_player', name='Team Player', rarity='rare', text='Every teammate works 10% faster on a team trip.', team_aura=0.10),
    dict(id='lone_wolf', name='Lone Wolf', rarity='rare', text='Works 15% faster alone, 10% slower in a team.', solo=0.15, team=-0.10),
    dict(id='specialist', name='Specialist', rarity='rare', text='Works 25% faster on its favourite target.', favourite=0.25),
    dict(id='treasure_nose', name='Treasure Nose', rarity='epic', text='5% chance per trip of a bonus find one tier up.', bonus_find=0.05),
    dict(id='tireless', name='Tireless', rarity='epic', text='Trips can last 4 hours longer.', max_hours=4.0),
    dict(id='prodigy', name='Prodigy', rarity='epic', text='One more skill point at levels 10, 20 and 30.', points=1),
    dict(id='golden_hand', name='Golden Hand', rarity='legendary', text='10% better at everything.', speed=0.10, amount=0.10, xp=0.10, rare=0.10),
    dict(id='legendary_master', name='Legendary Master', rarity='legendary', text='Every skill can go one rank higher.', ranks=1),
)
QUIRKS = (
    dict(id='greedy', name='Greedy', rarity='quirk', text='Brings 15% more; tools and retraining cost 25% more.', amount=0.15, cost=0.25),
    dict(id='hasty', name='Hasty', rarity='quirk', text='Trips finish 20% sooner; brings 10% less.', time=-0.20, amount=-0.10),
    dict(id='careless', name='Careless', rarity='quirk', text='Works 10% faster; rare finds are 10% less likely.', speed=0.10, rare=-0.10),
    dict(id='lazy', name='Lazy', rarity='quirk', text='Works 10% slower; earns 20% more experience.', speed=-0.10, xp=0.20),
)
BY_ID = {t['id']: t for t in TRAITS + QUIRKS}
EFFECTS = ('speed', 'amount', 'xp', 'cost', 'time', 'rare', 'long', 'short', 'team_aura', 'solo', 'team', 'favourite',
           'bonus_find', 'max_hours', 'points', 'ranks')


def rarity_odds(rare_mult: float = 1.0, epic_mult: float = 1.0, legendary: float = 0.0) -> dict:
    """The Tavern's odds: its multipliers raise rare and epic, legendary only at its top level."""
    rare, epic = BASE_ODDS['rare'] * rare_mult, BASE_ODDS['epic'] * epic_mult
    common = max(0.0, 1.0 - rare - epic - legendary)
    return dict(common=common, rare=rare, epic=epic, legendary=legendary)


def roll(rng: random.Random, odds: dict, favourites=()) -> list[dict]:
    """One candidate's traits: a trait of the drawn rarity, and maybe a quirk."""
    pick = rng.random()
    rarity = 'common'
    for name in RARITIES:
        pick -= odds.get(name, 0.0)
        if pick < 0:
            rarity = name
            break
    pool = [t for t in TRAITS if t['rarity'] == rarity] or [t for t in TRAITS if t['rarity'] == 'common']
    trait = rng.choice(pool)
    out = [dict(id=trait['id'])]
    if trait['id'] == 'specialist' and favourites:
        out[0]['target'] = rng.choice(list(favourites))
    if rng.random() < QUIRK_CHANCE:
        out.append(dict(id=rng.choice(QUIRKS)['id'], quirk=True))
    return out


def clean(traits) -> list[dict]:
    """A worker's stored traits, unknown ids dropped (a newer file never breaks an older panel)."""
    out = []
    for t in traits if isinstance(traits, list) else []:
        if isinstance(t, dict) and t.get('id') in BY_ID:
            out.append({k: v for k, v in t.items() if k in ('id', 'quirk', 'target')})
    return out


def effects(traits, *, hours=None, team=None, target=None) -> dict:
    """The combined effect of a worker's traits for one trip.

    ``hours``: the trip's work hours (Night Owl); ``team``: True on a team trip,
    False alone, None when not about a trip; ``target``: the trip's target key
    (Specialist).
    """
    total = {k: 0.0 for k in EFFECTS}
    for t in clean(traits):
        spec = BY_ID[t['id']]
        for key in EFFECTS:
            value = spec.get(key)
            if value is None or key in ('long', 'short', 'solo', 'team', 'favourite'):
                continue
            total[key] += value
        if hours is not None:
            if hours >= 6 and spec.get('long'):
                total['speed'] += spec['long']
            if hours <= 2 and spec.get('short'):
                total['speed'] += spec['short']
        if team is True and spec.get('team'):
            total['speed'] += spec['team']
        if team is False and spec.get('solo'):
            total['speed'] += spec['solo']
        if spec.get('favourite') and target is not None and t.get('target') == target:
            total['speed'] += spec['favourite']
    return total


def describe(traits) -> list[dict]:
    """Traits for the page: name, rarity, text (and the Specialist's target)."""
    out = []
    for t in clean(traits):
        spec = BY_ID[t['id']]
        out.append(dict(id=t['id'], name=spec['name'], rarity=spec['rarity'], text=spec['text'], quirk=bool(t.get('quirk')),
                        target=t.get('target')))
    return out
