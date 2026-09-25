"""Travelling merchants (0.9): who stops at the Market Square, what they sell, what they want.

The game's own vendors never sell keys, fragments, materials or socketables
(STATIC 2026-09-25). The town's travelling merchants do: a Keymaster with
Angelic and dungeon keys, an Occultist with Satanic Crystals and Destiny
Shards, a Rune Scholar, a Fortune Teller with tarot cards. They also buy what
they came for, often above a town's price.

Arrivals:
- The day is cut into four watches of six hours.
- In each watch a merchant may arrive, more likely the bigger the Market
  Square. Who comes, when, for how long and with what is drawn from the
  camp's founding time and the watch, so every page agrees.
- A merchant stays a few hours, as long as a stall is free.

A merchant's wares and wants are small markets of their own (economy.py): each
unit bought raises the next one's price, each unit sold lowers it. A merchant
never sells and buys the same good, sells at or above a good's value and buys at
or below it, so no two merchants can be played against each other. Bought goods
go into the camp's stock (Basic and Crystal Keys onto the key rack); sold goods
leave it for gold in the town's coffer. Gold goes out through the town's
coffer first and the game's purchase path for the rest (the panel's receipts).
Every item stays the game's: a good the town buys becomes a real item only when
the plugin makes it, when the player sends it to the Vault.

AFK FARM's own layer. Standard library only. Visits and their trades live in
workers.json (``state['market']``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import camp as C
import economy as E
import goods

SCHEMA = 1
WATCH_HOURS = 6
LOOKBACK_WATCHES = 8              # visits drawn this far back (two days) can still be in town
HISTORY = 30
# The Market Square's level: chance of a visit per watch, stalls, the dearest ware (gold value) and a longer stay.
ARRIVAL = (0.0, 0.40, 0.50, 0.60, 0.70, 0.80)
STALLS = (0, 1, 2, 2, 3, 3)
DEAREST = (0, 5_000, 25_000, 90_000, 260_000, 10**9)
STAY_BONUS = (1.0, 1.0, 1.1, 1.2, 1.35, 1.5)

MERCHANTS = (
    dict(key='gem_cutter', name='Wandering Gem Cutter', market=1, weight=4, stay=(3, 6), wares=4, wants=2,
         sells=('gem', 'material'), buys=('ore', 'material'), greed=0.20, premium=0.04,
         text='Cuts stones by the roadside. Sells gems and jewelcrafting stones; buys ore.'),
    dict(key='prospector', name='Old Prospector', market=1, weight=4, stay=(2, 5), wares=4, wants=2,
         sells=('ore', 'dust'), buys=('material', 'gem'), greed=0.15, premium=0.04,
         text='Grizzled and dusty. Sells ore and dust by the sack; buys the stones others cut.'),
    dict(key='quartermaster', name='Royal Quartermaster', market=1, weight=3, stay=(4, 8), wares=0, wants=4,
         sells=(), buys=('ore', 'material', 'dust', 'shard'), greed=0.0, premium=0.06,
         text='Buys for the army and pays the crown\'s coin: ore, stones, dust and shards, above market.'),
    dict(key='keymaster', name='Keymaster Brann', market=2, weight=3, stay=(3, 6), wares=4, wants=2,
         sells=('key',), buys=('shard', 'relic'), greed=0.30, premium=0.02,
         text='A ring of keys to every door, the Angelic Realm\'s included, for a price.'),
    dict(key='rune_scholar', name='Rune Scholar', market=2, weight=3, stay=(3, 7), wares=4, wants=3,
         sells=('rune',), buys=('rune', 'gem'), greed=0.25, premium=0.04,
         text='Reads runes like others read letters. Trades runes; buys gems to study.'),
    dict(key='goblin_peddler', name='Goblin Peddler', market=2, weight=2, stay=(1, 3), wares=5, wants=0,
         sells=('material', 'dust', 'shard', 'key', 'rune', 'gem'), buys=(), greed=-0.04, premium=0.0,
         text='Everything is "slightly used" and cheaper than anywhere else. Gone before you ask where from.'),
    dict(key='fortune_teller', name='Fortune Teller', market=3, weight=2, stay=(3, 6), wares=4, wants=2,
         sells=('tarot',), buys=('tarot', 'dust'), greed=0.25, premium=0.04,
         text='Deals tarot cards and reads your future in them. Buys the cards you do not want.'),
    dict(key='occultist', name='Satanic Occultist', market=3, weight=2, stay=(2, 5), wares=3, wants=2,
         sells=('relic', 'dust'), buys=('key', 'jewel'), greed=0.35, premium=0.04,
         text='Satanic Crystals, Destiny Shards and dice from places best not named.'),
    dict(key='guild_envoy', name="Jewelers' Guild Envoy", market=4, weight=2, stay=(4, 8), wares=4, wants=3,
         sells=('jewel', 'orb'), buys=('material', 'gem', 'orb'), greed=0.25, premium=0.05,
         text='The guild\'s finest jewels and orbs; it buys stones and gems for its workshops.'),
)
MERCHANT_BY_KEY = {m['key']: m for m in MERCHANTS}


def new() -> dict:
    return dict(schema=SCHEMA, trades={}, history=[])


def normalize(market) -> dict:
    base = new()
    market = market if isinstance(market, dict) else {}
    out = dict(base, **{k: market[k] for k in base if k in market})
    trades = out['trades'] if isinstance(out['trades'], dict) else {}
    out['trades'] = {str(k): dict(bought=_counts(v.get('bought')), sold=_counts(v.get('sold')))
                     for k, v in trades.items() if isinstance(v, dict)}
    out['history'] = [h for h in out['history'] if isinstance(h, dict)][:HISTORY] if isinstance(out['history'], list) else []
    out['schema'] = SCHEMA
    return out


def _counts(value) -> dict:
    value = value if isinstance(value, dict) else {}
    return {str(k): n for k, n in value.items() if type(n) is int and n > 0 and goods.known(k)}


def watch_of(at) -> int:
    return int(at.timestamp() // (WATCH_HOURS * 3600))


def _visit(founded: str, watch: int, level: int) -> dict | None:
    """The merchant that arrives in ``watch``, if any: who, when, how long, wares and wants."""
    rand = E.rng('merchant', founded, watch)
    if rand.random() >= ARRIVAL[level]:
        return None
    pool = [(m['key'], m['weight']) for m in MERCHANTS if m['market'] <= level]
    m = MERCHANT_BY_KEY[E.weighted(rand, pool)]
    start = datetime.fromtimestamp(watch * WATCH_HOURS * 3600, timezone.utc) + timedelta(minutes=rand.randint(0, WATCH_HOURS * 60 // 2))
    stay = rand.uniform(*m['stay']) * STAY_BONUS[level]
    offers = []
    dearest = DEAREST[level]
    sold = set()
    for side, cats, n, spread in (('sell', m['sells'], m['wares'], (1.0, 1.15)), ('buy', m['buys'], m['wants'], (0.85, 1.0))):
        pool = sorted(k for c in cats for k in goods.of(c) if goods.value(k) <= dearest and k not in sold)
        rand.shuffle(pool)
        for key in pool[:n]:
            value = goods.value(key)
            depth = max(1.0, 30_000.0 * value ** -0.7) * (1.0 + 0.25 * level)
            offers.append(dict(key=key, side=side, qty=max(1, int(round(depth * rand.uniform(0.6, 1.2)))),
                               price=round(value * rand.uniform(*spread), 2)))
            sold.add(key)
    ident = f"{m['key']}@{watch}"
    return dict(id=ident, merchant=m['key'], name=m['name'], text=m['text'], arrives_at=C.iso(start.replace(microsecond=0)),
                leaves_at=C.iso((start + timedelta(hours=stay)).replace(microsecond=0)), offers=offers)


def visits(camp: dict, at=None) -> list[dict]:
    """Merchants in town now (up to the stalls), earliest first."""
    at = at or C.now_utc()
    level = C.level(camp, 'market')
    if level <= 0:
        return []
    here = []
    now_watch = watch_of(at)
    for w in range(now_watch - LOOKBACK_WATCHES, now_watch + 1):
        v = _visit(camp['founded_at'], w, level)
        if v and C.parse_iso(v['arrives_at']) <= at < C.parse_iso(v['leaves_at']):
            here.append(v)
    here.sort(key=lambda v: (v['arrives_at'], v['id']))
    return here[:STALLS[level]]


def find(camp: dict, visit_id: str, at=None) -> dict:
    v = next((v for v in visits(camp, at) if v['id'] == visit_id), None)
    if v is None:
        raise ValueError('That merchant has left town.')
    return v


def _offer(visit: dict, key: str, side: str) -> dict:
    o = next((o for o in visit['offers'] if o['key'] == key and o['side'] == side), None)
    if o is None:
        raise ValueError(f"{visit['name']} does not {'sell' if side == 'sell' else 'buy'} {goods.name(key)}.")
    return o


def _state(market: dict, visit_id: str) -> dict:
    return market['trades'].setdefault(visit_id, dict(bought={}, sold={}))


def quote(market: dict, visit: dict, key: str, side: str, qty: int) -> dict:
    """The gold for ``qty`` units: what the player pays (side 'sell': the merchant
    sells) or receives (side 'buy': the merchant buys). The merchant's stock is
    its offer less what was already traded."""
    m = MERCHANT_BY_KEY[visit['merchant']]
    o = _offer(visit, key, side)
    done = _state(market, visit['id'])['bought' if side == 'sell' else 'sold'].get(key, 0)
    if type(qty) is not int or qty < 1:
        raise ValueError('Trade a whole number, at least one.')
    left = o['qty'] - done
    if qty > left:
        raise ValueError(f"{visit['name']} {'has only' if side == 'sell' else 'wants only'} {max(0, left):,} more {goods.name(key)}.")
    if side == 'sell':
        # its shelf starts at its target (the first unit at its price) and empties as the
        # player buys: the last of its offer costs about 1.41 times as much
        target = 2.0 * o['qty']
        gold = E.buy_cost(o['price'], target - done, target, qty, m['greed'])
    else:
        # its wish starts at its price and shrinks as the player sells: the last unit it
        # wants fetches about 0.71 of it
        target = float(o['qty'])
        gold = E.sell_value(o['price'], target + done, target, qty, -m['premium'])
    return dict(visit=visit['id'], key=key, name=goods.name(key), side=side, qty=qty, gold=gold, left=left)


def record(market: dict, visit: dict, q: dict, at=None) -> dict:
    """A trade that happened (the gold and goods moved already): remember it once."""
    st = _state(market, visit['id'])
    book = st['bought' if q['side'] == 'sell' else 'sold']
    book[q['key']] = book.get(q['key'], 0) + q['qty']
    entry = dict(at=C.iso(at or C.now_utc()), visit=visit['id'], merchant=visit['name'], key=q['key'], name=q['name'],
                 side=q['side'], qty=q['qty'], gold=q['gold'])
    market['history'] = ([entry] + market['history'])[:HISTORY]
    return entry


def tidy(market: dict, camp: dict, at=None) -> bool:
    """Forget the trades of merchants long gone (they can never come back). True when any went."""
    at = at or C.now_utc()
    oldest = watch_of(at) - LOOKBACK_WATCHES - 1
    kept = {k: v for k, v in market['trades'].items() if k.rsplit('@', 1)[-1].isdigit() and int(k.rsplit('@', 1)[1]) >= oldest}
    changed = len(kept) != len(market['trades'])
    market['trades'] = kept
    return changed


def view(market: dict, camp: dict, at=None) -> dict:
    at = at or C.now_utc()
    level = C.level(camp, 'market')
    here = []
    for v in visits(camp, at):
        st = _state(market, v['id'])
        offers = []
        for o in v['offers']:
            done = st['bought' if o['side'] == 'sell' else 'sold'].get(o['key'], 0)
            left = o['qty'] - done
            one = quote(market, v, o['key'], o['side'], 1) if left > 0 else None
            offers.append(dict(key=o['key'], name=goods.name(o['key']), category=goods.category(o['key']), side=o['side'],
                               left=max(0, left), unit=one['gold'] if one else None, value=goods.value(o['key'])))
        here.append(dict(id=v['id'], merchant=v['merchant'], name=v['name'], text=v['text'], arrives_at=v['arrives_at'],
                         leaves_at=v['leaves_at'], offers=offers))
    nxt = None
    if level > 0:
        for w in range(watch_of(at), watch_of(at) + 12):
            v = _visit(camp['founded_at'], w, level)
            if v and C.parse_iso(v['arrives_at']) > at:
                nxt = dict(expected=True, watch_starts=C.iso(datetime.fromtimestamp(w * WATCH_HOURS * 3600, timezone.utc)))
                break
    return dict(level=level, stalls=STALLS[level], chance=ARRIVAL[level], merchants=here, history=market['history'][:10],
                next_watch=nxt, known=[dict(key=m['key'], name=m['name'], text=m['text'], market=m['market']) for m in MERCHANTS])
