"""Trade with the other towns (0.9): wagons, markets that move, towns that grow.

Beyond the camp lie other settlements. Some are hamlets, some are cities, and
each makes some goods cheaply and needs others. The Trading Post sends wagons
to them. A wagon carries goods from the camp's stock and a purse of gold. At
the town it sells its cargo into the market, buys what it was told to with
the purse and the takings, and comes home. The goods go into the camp's stock
and the gold into the town's coffer; the player takes the coffer into the game
whenever they like.

Every market is economy.py's stock model:
- selling a lot pushes a price down and buying a lot pushes it up, and both
  recover by the hour;
- a round trip at one market always loses its margin and its tariff;
- a profitable route has to pay for the road.
Trading with a town:
- builds your standing there, which lowers its tariff;
- builds its prosperity, which grows it: deeper markets and rarer goods.
Each town also has good and bad days, drawn from the date.

All of this is AFK FARM's own layer. Its links to the game are exact:
- goods come in from the Vault or from workers, and go out to the Vault only
  as items the game itself makes;
- gold goes in through the game's purchase path and comes out through a
  receipt-checked credit (see the panel).

Standard library only. The trade state lives in workers.json (``state['trade']``).
"""
from __future__ import annotations

import math
from datetime import timedelta

import camp as C
import economy as E
import goods

SCHEMA = 1
HALF_LIFE_HOURS = 18.0
MAX_WAGON_GOLD = 100_000_000
HISTORY = 30

# Development 1-5: how deep a town's markets are and which goods it trades.
DEPTH = (0.6, 1.0, 1.6, 2.5, 4.0)
TARIFF = (0.12, 0.09, 0.06, 0.04, 0.02, 0.0)          # by standing 0-5
STANDING = (0, 250_000, 1_500_000, 6_000_000, 20_000_000, 60_000_000)    # gold traded to reach each standing
PROSPERITY = (0, 2_000_000, 8_000_000, 25_000_000, 80_000_000)          # gold traded to reach each development
# The development a town needs to trade a category.
CATEGORY_DEV = dict(ore=1, material=1, dust=2, shard=2, key=2, gem=2, tarot=3, rune=3, relic=4, jewel=4, orb=4)
RACK_KEYS = ('12:0', '12:1')     # Basic and Crystal Keys live on the adventurers' key rack, not in the stock


def base_target(value: int) -> float:
    """A good's target stock at development 2, before its town's taste: cheap goods
    are held by the thousand, the dearest by the handful."""
    return max(2.0, 150_000.0 * max(1, value) ** -0.85)


# How cheaply a town makes a category (<1) or how dearly it needs it (>1).
TOWNS = (
    dict(key='ironhold', name='Ironhold', kind='Mining town', dev=2, hours=1.5, post=1,
         taste=dict(ore=0.75, material=1.15, gem=1.1, key=1.1, dust=1.0),
         text='Dwarf-built shafts under the mountain. Ore is cheap here; cut stones and keys are not.'),
    dict(key='emberfall', name='Emberfall', kind='Forge city', dev=3, hours=2.5, post=1,
         taste=dict(ore=1.35, material=0.9, dust=0.9, rune=1.1, gem=1.05),
         text='Its forges never cool: it pays well for ore and sells fire-cut stones and forge dust.'),
    dict(key='saltmarsh', name='Saltmarsh', kind='Fishing village', dev=1, hours=1.0, post=1,
         taste=dict(ore=1.1, material=1.2, key=0.95, shard=1.0, dust=1.1),
         text='A small, poor harbour. It takes whatever it is offered, at modest prices.'),
    dict(key='duskhaven', name='Duskhaven', kind='Free port', dev=4, hours=4.0, post=2,
         taste=dict(key=0.85, tarot=0.85, orb=1.1, jewel=1.1, rune=1.0, gem=1.0, material=1.05),
         text='Every ship docks here: keys, tarot decks and curiosities from far shores.'),
    dict(key='frostmere', name='Frostmere', kind='Northern hold', dev=2, hours=3.0, post=2,
         taste=dict(ore=0.85, gem=1.25, rune=1.15, dust=1.1, material=1.2),
         text='Cold mines and colder people. Gems and runes fetch a fortune in the long nights.'),
    dict(key='sanctum', name='Sanctum of Dawn', kind='Temple city', dev=4, hours=5.0, post=3,
         taste=dict(relic=0.8, jewel=0.9, dust=0.85, gem=1.1, key=1.15, tarot=1.2),
         text='Pilgrims bring holy relics; the priests trade destiny shards, crystals and angelic dust.'),
    dict(key='cinderpit', name='Cinderpit', kind='Satanic bazaar', dev=3, hours=6.0, post=3,
         taste=dict(relic=0.75, dust=0.8, key=1.3, ore=1.2, shard=0.9, rune=1.1),
         text='A market no honest merchant admits to visiting. Satanic crystals and dice, cheap.'),
    dict(key='goldcrest', name='Goldcrest', kind='Royal capital', dev=5, hours=7.0, post=4,
         taste=dict(jewel=1.25, relic=1.15, gem=1.1, orb=1.2, rune=1.1, ore=0.95, key=1.0, material=1.0),
         text='The richest markets in the land, and the deepest pockets for fine jewels and orbs.'),
    dict(key='mirewatch', name='Mirewatch', kind='Swamp outpost', dev=1, hours=3.5, post=4,
         taste=dict(material=0.8, gem=0.85, ore=1.25, shard=1.1),
         text='Its gem-hunters wade the bogs; it has little else, and wants iron.'),
    dict(key='skyreach', name='Skyreach', kind='Mountain monastery', dev=3, hours=8.0, post=5,
         taste=dict(key=0.7, jewel=0.85, rune=0.8, relic=1.25, orb=0.9),
         text='At the end of the world: the cheapest keys and runes, the rarest jewels.'),
)
TOWN_BY_KEY = {t['key']: t for t in TOWNS}
# Wagons by Trading Post level: how many, how many units each carries, how fast.
WAGONS = (0, 1, 2, 2, 3, 4)
CAPACITY = (0, 500, 1_000, 2_000, 3_500, 6_000)
SPEED = (1.0, 1.0, 1.0, 1.1, 1.2, 1.3)

EVENTS = (
    dict(key='festival', name='Festival', text='Gems are worth 40% more today.', category='gem', price=1.4),
    dict(key='cave_in', name='Cave-in', text='The mines are shut: ore is worth 50% more.', category='ore', price=1.5),
    dict(key='caravan', name='Caravan arrived', text='A caravan unloaded: everything is 15% cheaper.', category=None, price=0.85),
    dict(key='key_shortage', name='Key shortage', text='Keys are worth 35% more.', category='key', price=1.35),
    dict(key='crystal_glut', name='Crystal glut', text='Rare consumables are 30% cheaper.', category='relic', price=0.7),
    dict(key='rune_fair', name='Rune fair', text='Runes are worth 30% more.', category='rune', price=1.3),
)
EVENT_CHANCE = 0.25


def new(at=None) -> dict:
    return dict(schema=SCHEMA, founded_at=C.iso(at or C.now_utc()), markets={}, towns={}, runs=[], coffer=0, history=[])


def normalize(trade) -> dict:
    base = new()
    trade = trade if isinstance(trade, dict) else {}
    out = dict(base, **{k: trade[k] for k in base if k in trade})
    out['markets'] = {k: v for k, v in (out['markets'] if isinstance(out['markets'], dict) else {}).items()
                      if k in TOWN_BY_KEY and isinstance(v, dict)}
    out['towns'] = {k: dict(traded=_int(v.get('traded')), prosperity=_int(v.get('prosperity')))
                    for k, v in (out['towns'] if isinstance(out['towns'], dict) else {}).items() if k in TOWN_BY_KEY and isinstance(v, dict)}
    out['runs'] = [r for r in out['runs'] if _sound_run(r)] if isinstance(out['runs'], list) else []
    out['coffer'] = _int(out.get('coffer'))
    out['history'] = [h for h in out['history'] if isinstance(h, dict)][:HISTORY] if isinstance(out['history'], list) else []
    out['schema'] = SCHEMA
    return out


def _int(value) -> int:
    return value if type(value) is int and value > 0 else 0


def _sound_run(r) -> bool:
    """A stored wagon settle, returned and view can read (hand edits and cut-short files are left out)."""
    if not isinstance(r, dict) or not r.get('id') or r.get('town') not in TOWN_BY_KEY or r.get('state') not in ('travelling', 'arrived'):
        return False
    try:
        for key in ('left_at', 'arrives_at', 'returns_at'):
            C.parse_iso(r[key])
    except (KeyError, TypeError, ValueError):
        return False
    for key in ('cargo', 'orders'):
        if not isinstance(r.get(key), dict) or any(not goods.known(k) or type(n) is not int or n < 1 for k, n in r[key].items()):
            return False
    if type(r.get('purse')) is not int or r['purse'] < 0:
        return False
    if r['state'] == 'arrived':
        res = r.get('result')
        need = ('earned', 'spent', 'bought', 'sold', 'unsold', 'gold_back')
        if not isinstance(res, dict) or any(k not in res for k in need):
            return False
    return True


def ensure(state: dict) -> dict:
    """``state['trade']`` ready to use; a new one is founded with the camp."""
    fresh = not isinstance(state.get('trade'), dict)
    state['trade'] = normalize(state.get('trade'))
    if fresh:
        state['trade']['founded_at'] = state['camp']['founded_at']
    return state['trade']


# ------------------------------------------------------------------ a town and its market
def standing(trade: dict, town: str) -> int:
    traded = trade['towns'].get(town, {}).get('traded', 0)
    return max(i for i, need in enumerate(STANDING) if traded >= need)


def development(trade: dict, town: str) -> int:
    spec = TOWN_BY_KEY[town]
    grown = max(i for i, need in enumerate(PROSPERITY) if trade['towns'].get(town, {}).get('prosperity', 0) >= need)
    return min(5, spec['dev'] + grown)


def tariff(trade: dict, town: str) -> float:
    return TARIFF[standing(trade, town)]


def event(trade: dict, town: str, day: str) -> dict | None:
    """The town's news today (drawn from the trade's founding and the date)."""
    rand = E.rng('trade-event', trade['founded_at'], town, day)
    if rand.random() >= EVENT_CHANCE:
        return None
    return rand.choice(EVENTS)


def trades(trade: dict, town: str) -> list[str]:
    """The goods a town trades at its current development."""
    dev = development(trade, town)
    taste = TOWN_BY_KEY[town]['taste']
    return [k for k, (_, cat, _) in sorted(goods.GOODS.items()) if cat in taste and CATEGORY_DEV.get(cat, 9) <= dev]


def _target(trade: dict, town: str, key: str) -> float:
    dev = development(trade, town)
    taste = TOWN_BY_KEY[town]['taste'].get(goods.category(key), 1.0)
    # a town that makes a good cheaply holds more of it; one that needs it, less
    return base_target(goods.value(key)) * DEPTH[dev - 1] / taste


def _base_price(trade: dict, town: str, key: str, day: str) -> float:
    taste = TOWN_BY_KEY[town]['taste'].get(goods.category(key), 1.0)
    ev = event(trade, town, day)
    mult = ev['price'] if ev and ev['category'] in (None, goods.category(key)) else 1.0
    return goods.value(key) * taste * mult


def stock(trade: dict, town: str, key: str, at) -> float:
    """A good's stock at a town now: its last trade, relaxed toward its target since."""
    target = _target(trade, town, key)
    m = trade['markets'].get(town, {}).get(key)
    if not isinstance(m, dict) or not isinstance(m.get('stock'), (int, float)) or not m.get('at'):
        return target
    hours = max(0.0, (at - C.parse_iso(m['at'])).total_seconds() / 3600.0)
    return E.drift(float(m['stock']), target, hours, HALF_LIFE_HOURS)


def _set_stock(trade: dict, town: str, key: str, value: float, at) -> None:
    trade['markets'].setdefault(town, {})[key] = dict(stock=round(value, 6), at=C.iso(at))


def quote(trade: dict, town: str, key: str, at) -> dict:
    """What the town asks for one more unit and bids for one less, today."""
    day = at.strftime('%Y-%m-%d')
    base, s, target = _base_price(trade, town, key, day), stock(trade, town, key, at), _target(trade, town, key)
    tax = tariff(trade, town)
    unit = E.unit_price(base, s, target)
    return dict(key=key, name=goods.name(key), category=goods.category(key), ask=math.ceil(unit * (1 + E.MARGIN + tax)),
                bid=math.floor(unit * (1 - E.MARGIN - tax)), stock=int(s), can_buy=E.available(s, target), can_sell=E.room(s, target))


def sell(trade: dict, town: str, key: str, qty: int, at) -> tuple[int, int]:
    """Sell up to ``qty`` units into the town's market (as many as it will take);
    returns (units sold, gold)."""
    day = at.strftime('%Y-%m-%d')
    s, target = stock(trade, town, key, at), _target(trade, town, key)
    qty = min(int(qty), E.room(s, target))
    if qty <= 0:
        return 0, 0
    gold = E.sell_value(_base_price(trade, town, key, day), s, target, qty, tariff(trade, town))
    _set_stock(trade, town, key, s + qty, at)
    return qty, gold


def buy(trade: dict, town: str, key: str, qty: int, gold: int, at) -> tuple[int, int]:
    """Buy up to ``qty`` units with at most ``gold``; returns (units, gold spent)."""
    day = at.strftime('%Y-%m-%d')
    s, target = stock(trade, town, key, at), _target(trade, town, key)
    base, tax = _base_price(trade, town, key, day), tariff(trade, town)
    n = min(int(qty), E.max_affordable(base, s, target, int(gold), tax))
    if n <= 0:
        return 0, 0
    cost = E.buy_cost(base, s, target, n, tax)
    _set_stock(trade, town, key, s - n, at)
    return n, cost


def _grow(trade: dict, town: str, gold: int) -> None:
    t = trade['towns'].setdefault(town, dict(traded=0, prosperity=0))
    t['traded'] += int(gold)
    t['prosperity'] += int(gold)


# ------------------------------------------------------------------ wagons
def wagons(camp: dict) -> dict:
    post = C.level(camp, 'trading_post')
    return dict(count=WAGONS[post], capacity=CAPACITY[post], speed=SPEED[post], post=post)


def reachable(camp: dict) -> list[str]:
    post = C.level(camp, 'trading_post')
    return [t['key'] for t in TOWNS if t['post'] <= post]


def plan_run(trade: dict, camp: dict, town: str, cargo: dict, orders: dict, purse: int, at=None) -> dict:
    """Check a wagon's trip before anything is taken or paid: the town, the cargo in
    stock, the orders the town trades, the load and the purse."""
    at = at or C.now_utc()
    w = wagons(camp)
    if w['count'] <= 0:
        raise ValueError('Build a Trading Post to send wagons.')
    if town not in TOWN_BY_KEY:
        raise ValueError('Unknown town.')
    if town not in reachable(camp):
        raise ValueError(f"{TOWN_BY_KEY[town]['name']} is too far: it needs Trading Post level {TOWN_BY_KEY[town]['post']}.")
    if sum(1 for r in trade['runs'] if r['state'] in ('travelling', 'arrived')) >= w['count']:
        raise ValueError('Every wagon is on the road.')
    cargo, orders = _clean(cargo, 'cargo'), _clean(orders, 'order')
    if not cargo and not orders:
        raise ValueError('Load goods to sell or give orders to buy.')
    for key, n in cargo.items():
        if camp['stock'].get(key, 0) < n:
            raise ValueError(f"The camp stock has only {camp['stock'].get(key, 0):,} {goods.name(key)}.")
    sold = set(trades(trade, town))
    for key in list(cargo) + list(orders):
        if key not in sold:
            raise ValueError(f"{TOWN_BY_KEY[town]['name']} does not trade {goods.name(key)} yet.")
    if sum(cargo.values()) > w['capacity'] or sum(orders.values()) > w['capacity']:
        raise ValueError(f"A wagon carries at most {w['capacity']:,} units each way.")
    if type(purse) is not int or not 0 <= purse <= MAX_WAGON_GOLD:
        raise ValueError(f'A purse is a whole amount of gold up to {MAX_WAGON_GOLD:,}.')
    if orders and purse <= 0 and not cargo:
        raise ValueError('Give the wagon gold (or goods to sell) to buy with.')
    hours = TOWN_BY_KEY[town]['hours'] / w['speed']
    return dict(town=town, cargo=cargo, orders=orders, purse=purse, hours=round(hours, 4))


def _clean(raw, what) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f'The {what} must list goods and counts.')
    out = {}
    for key, n in raw.items():
        if not goods.known(key):
            raise ValueError(f'{goods.name(key)} is not a trade good.')
        if what == 'cargo' and key in RACK_KEYS:
            raise ValueError('Keys on the key rack stay with the adventurers; wagons carry goods from the stock.')
        if type(n) is not int or not 1 <= n <= 100_000:
            raise ValueError('Trade whole counts from 1 to 100,000.')
        out[str(key)] = n
    return out


def start_run(trade: dict, camp: dict, plan: dict, run_id: str, paid_from_coffer: int, payment=None, at=None) -> dict:
    """The wagon leaves: its cargo left the stock, its purse was paid (coffer first, then the game)."""
    at = at or C.now_utc()
    for key, n in plan['cargo'].items():
        camp['stock'][key] -= n
        if not camp['stock'][key]:
            del camp['stock'][key]
    run = dict(id=run_id, town=plan['town'], cargo=plan['cargo'], orders=plan['orders'], purse=plan['purse'],
               from_coffer=paid_from_coffer, payment=payment, left_at=C.iso(at),
               arrives_at=C.iso(at + timedelta(hours=plan['hours'])), returns_at=C.iso(at + timedelta(hours=2 * plan['hours'])),
               state='travelling', result=None)
    trade['runs'].append(run)
    return run


def settle(trade: dict, at=None) -> list[dict]:
    """Every wagon that reached its town by ``at`` trades there, in the order they arrived."""
    at = at or C.now_utc()
    due = sorted((r for r in trade['runs'] if r['state'] == 'travelling' and C.parse_iso(r['arrives_at']) <= at),
                 key=lambda r: (r['arrives_at'], r['id']))
    for r in due:
        when = C.parse_iso(r['arrives_at'])
        earned, sold = 0, {}
        for key, n in sorted(r['cargo'].items()):
            units, gold = sell(trade, r['town'], key, n, when)
            earned += gold
            sold[key] = dict(sent=n, sold=units, gold=gold)
        purse = r['purse'] + earned
        bought, spent = {}, 0
        for key, n in sorted(r['orders'].items()):
            units, cost = buy(trade, r['town'], key, n, purse - spent, when)
            if units:
                bought[key] = units
                spent += cost
        unsold = {k: v['sent'] - v['sold'] for k, v in sold.items() if v['sent'] > v['sold']}
        r['result'] = dict(earned=earned, spent=spent, bought=bought, sold={k: v['sold'] for k, v in sold.items() if v['sold']},
                           sold_gold={k: v['gold'] for k, v in sold.items() if v['sold']}, unsold=unsold,
                           gold_back=r['purse'] + earned - spent, at=r['arrives_at'])
        r['state'] = 'arrived'
        _grow(trade, r['town'], earned + spent)
    return due


def returned(trade: dict, at=None) -> list[dict]:
    at = at or C.now_utc()
    return [r for r in trade['runs'] if r['state'] == 'arrived' and C.parse_iso(r['returns_at']) <= at]


def unload(trade: dict, camp: dict, run_id: str, at=None) -> dict:
    """A wagon home: its goods into the camp's stock (past the cap: they are here), its
    gold into the coffer. Once."""
    at = at or C.now_utc()
    settle(trade, at)
    r = next((r for r in trade['runs'] if r['id'] == run_id), None)
    if r is None:
        raise ValueError('No such wagon.')
    if r['state'] != 'arrived' or C.parse_iso(r['returns_at']) > at:
        raise ValueError('The wagon is still on the road.')
    res = r['result']
    arriving = dict(res['unsold'])
    for key, n in res['bought'].items():
        arriving[key] = arriving.get(key, 0) + n
    kept = arrive(camp, arriving)
    trade['coffer'] += int(res['gold_back'])
    r['state'] = 'home'
    trade['runs'] = [x for x in trade['runs'] if x['state'] != 'home']
    entry = dict(id=r['id'], town=r['town'], at=C.iso(at), cargo=r['cargo'], sold=res['sold'], bought=res['bought'],
                 earned=res['earned'], spent=res['spent'], gold_back=res['gold_back'], purse=r['purse'])
    trade['history'] = ([entry] + trade['history'])[:HISTORY]
    return dict(entry=entry, stock=kept)


def arrive(camp: dict, items: dict) -> dict:
    """Goods that reached the camp: rack keys onto the rack, the rest into the stock
    (past the caps: they are already here)."""
    rack = {k.split(':')[1]: n for k, n in items.items() if k in RACK_KEYS and n > 0}
    rest = {k: n for k, n in items.items() if k not in RACK_KEYS and n > 0}
    kept = C.add_stock(camp, rest, force=True)
    for base, n in C.add_keys(camp, rack, force=True).items():
        kept[f'12:{base}'] = n
    return kept


# ------------------------------------------------------------------ view
def view(trade: dict, camp: dict, at=None) -> dict:
    at = at or C.now_utc()
    settle(trade, at)
    day = at.strftime('%Y-%m-%d')
    towns = []
    for t in TOWNS:
        key = t['key']
        open_ = key in reachable(camp)
        towns.append(dict(key=key, name=t['name'], kind=t['kind'], text=t['text'], hours=t['hours'], post=t['post'], reachable=open_,
                          development=development(trade, key), standing=standing(trade, key), tariff=tariff(trade, key),
                          traded=trade['towns'].get(key, {}).get('traded', 0), event=event(trade, key, day),
                          market=[quote(trade, key, g, at) for g in trades(trade, key)] if open_ else []))
    runs = []
    for r in trade['runs']:
        runs.append(dict(r, town_name=TOWN_BY_KEY[r['town']]['name'],
                         home=r['state'] == 'arrived' and C.parse_iso(r['returns_at']) <= at,
                         result=r['result'] if r['state'] == 'arrived' else None))
    return dict(wagons=wagons(camp), towns=towns, runs=runs, coffer=trade['coffer'], history=trade['history'][:10])
