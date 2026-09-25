"""Travelling merchants (0.9): who comes to the Market Square and when, stalls, wares by level,
prices that move with every unit, the trade book, tidying and finding a merchant.
Fixed founding time and clock; nothing reads the player's data or contacts the game.
"""
import re, sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import camp, goods, merchants

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
FOUNDED = '2026-09-01T08:00:00Z'
MONTH = [T0 + timedelta(hours=h) for h in range(24 * 30)]     # hour by hour


def a_camp(market, founded=FOUNDED):
    c = camp.new(T0)
    c['founded_at'] = founded
    c['buildings']['market'] = market
    return c


def in_town(level, want=lambda v: True):
    """The first merchant in town (hour by hour from T0) that ``want`` accepts: camp, visit, time."""
    c = a_camp(level)
    for at in MONTH:
        for v in merchants.visits(c, at):
            if want(v):
                return c, v, at
    raise AssertionError('no such merchant came in a month')


def offer(v, side):
    """The visit's biggest offer on ``side`` ('sell': the merchant sells, 'buy': it buys)."""
    return max((o for o in v['offers'] if o['side'] == side), key=lambda o: o['qty'])


def trades_both(v, least=4):
    return all(any(o['side'] == side and o['qty'] >= least for o in v['offers']) for side in ('sell', 'buy'))


class ArrivalTests(unittest.TestCase):
    def test_no_merchant_comes_without_a_market_square(self):
        c = a_camp(0)
        self.assertEqual([v for at in MONTH for v in merchants.visits(c, at)], [])
        view = merchants.view(merchants.new(), c, T0)
        self.assertEqual((view['level'], view['stalls'], view['chance'], view['merchants'], view['next_watch']), (0, 0, 0.0, [], None))
        with self.assertRaisesRegex(ValueError, 'That merchant has left town'):
            merchants.find(c, 'keymaster@1', T0)

    def test_visits_are_the_same_for_the_same_camp_and_watch(self):
        week = MONTH[:24 * 7]
        seen = [merchants.visits(a_camp(3), at) for at in week]
        self.assertEqual(seen, [merchants.visits(a_camp(3), at) for at in week], 'every page agrees')
        other = [[v['id'] for v in merchants.visits(a_camp(3, founded='2026-02-02T02:02:02Z'), at)] for at in week]
        self.assertNotEqual([[v['id'] for v in here] for here in seen], other, 'another camp meets other merchants')
        known = {}
        for here, at in zip(seen, week):
            for v in here:
                self.assertEqual(known.setdefault(v['id'], v), v, 'a visit is the same every hour of its stay')
                arrives, leaves = camp.parse_iso(v['arrives_at']), camp.parse_iso(v['leaves_at'])
                self.assertTrue(arrives <= at < leaves)
                watch = datetime.fromtimestamp(int(v['id'].rsplit('@', 1)[1]) * merchants.WATCH_HOURS * 3600, timezone.utc)
                self.assertTrue(watch <= arrives <= watch + timedelta(hours=merchants.WATCH_HOURS / 2), 'it comes early in its watch')
                low, high = (hours * merchants.STAY_BONUS[3] for hours in merchants.MERCHANT_BY_KEY[v['merchant']]['stay'])
                self.assertTrue(low - 1 / 3600 <= (leaves - arrives).total_seconds() / 3600 <= high + 1 / 3600, v['id'])
        self.assertGreater(len(known), 5)

    def test_merchants_in_town_never_outnumber_the_stalls(self):
        for level in range(1, 6):
            c = a_camp(level)
            for at in MONTH[::3]:
                self.assertLessEqual(len(merchants.visits(c, at)), merchants.STALLS[level], (level, at))
        c, crowded = a_camp(1), 0
        for at in MONTH:
            here = merchants.visits(c, at)
            with patch.object(merchants, 'STALLS', (0,) + (99,) * 5):
                everyone = merchants.visits(c, at)
            self.assertEqual(here, everyone[:merchants.STALLS[1]], 'the first to arrive keep the stalls')
            crowded += len(everyone) > len(here)
        self.assertGreater(crowded, 0, 'a single stall turns merchants away now and then')

    def test_wares_never_cost_more_than_the_market_square_allows(self):
        dearest = {}
        for level in range(1, 6):
            c, top = a_camp(level), 0
            for at in MONTH[::2]:
                for v in merchants.visits(c, at):
                    m = merchants.MERCHANT_BY_KEY[v['merchant']]
                    self.assertLessEqual(m['market'], level, f"{m['name']} needs a bigger Market Square")
                    for o in v['offers']:
                        self.assertLessEqual(goods.value(o['key']), merchants.DEAREST[level], (level, o))
                        self.assertIn(goods.category(o['key']), m['sells'] if o['side'] == 'sell' else m['buys'])
                        top = max(top, goods.value(o['key']))
            dearest[level] = top
        self.assertTrue(dearest[1] < dearest[3] < dearest[5], dearest)

    def test_find_refuses_a_merchant_who_has_left(self):
        c, v, at = in_town(2)
        self.assertEqual(merchants.find(c, v['id'], at), v)
        with self.assertRaisesRegex(ValueError, 'That merchant has left town'):
            merchants.find(c, v['id'], camp.parse_iso(v['leaves_at']))
        with self.assertRaisesRegex(ValueError, 'That merchant has left town'):
            merchants.find(c, 'nobody@1', at)


class PriceTests(unittest.TestCase):
    def test_buying_from_a_merchant_costs_more_with_each_unit(self):
        c, v, at = in_town(2, trades_both)
        o, market = offer(v, 'sell'), merchants.new()
        first = merchants.quote(market, v, o['key'], 'sell', 1)
        self.assertEqual((first['visit'], first['key'], first['side'], first['qty'], first['left']), (v['id'], o['key'], 'sell', 1, o['qty']))
        self.assertGreaterEqual(merchants.quote(market, v, o['key'], 'sell', 2)['gold'], 2 * first['gold'] - 1, 'no discount for two at once')
        merchants.record(market, v, merchants.quote(market, v, o['key'], 'sell', o['qty'] // 2), at)
        self.assertGreater(merchants.quote(market, v, o['key'], 'sell', 1)['gold'], first['gold'], 'the shelf empties, the price rises')

    def test_selling_to_a_merchant_pays_less_with_each_unit(self):
        c, v, at = in_town(2, trades_both)
        o, market = offer(v, 'buy'), merchants.new()
        first = merchants.quote(market, v, o['key'], 'buy', 1)
        self.assertLessEqual(merchants.quote(market, v, o['key'], 'buy', 2)['gold'], 2 * first['gold'] + 1, 'no bonus for two at once')
        merchants.record(market, v, merchants.quote(market, v, o['key'], 'buy', o['qty'] // 2), at)
        self.assertLess(merchants.quote(market, v, o['key'], 'buy', 1)['gold'], first['gold'], 'its wish shrinks, so does the price')

    def test_a_merchant_has_only_so_much_and_wants_only_so_much(self):
        c, v, at = in_town(2, trades_both)
        sell, buy, market = offer(v, 'sell'), offer(v, 'buy'), merchants.new()
        name = re.escape(v['name'])
        with self.assertRaisesRegex(ValueError, f"{name} has only {sell['qty']:,} more {re.escape(goods.name(sell['key']))}"):
            merchants.quote(market, v, sell['key'], 'sell', sell['qty'] + 1)
        merchants.record(market, v, merchants.quote(market, v, sell['key'], 'sell', sell['qty']), at)
        with self.assertRaisesRegex(ValueError, f'{name} has only 0 more'):
            merchants.quote(market, v, sell['key'], 'sell', 1)
        with self.assertRaisesRegex(ValueError, f"{name} wants only {buy['qty']:,} more"):
            merchants.quote(market, v, buy['key'], 'buy', buy['qty'] + 1)
        for qty in (0, -1, 1.5, True, '2'):
            with self.assertRaisesRegex(ValueError, 'Trade a whole number, at least one'):
                merchants.quote(market, v, buy['key'], 'buy', qty)
        stranger = next(k for k in sorted(goods.GOODS) if all(o['key'] != k for o in v['offers']))
        for side, verb in (('sell', 'sell'), ('buy', 'buy')):
            with self.assertRaisesRegex(ValueError, f'{name} does not {verb} {re.escape(goods.name(stranger))}'):
                merchants.quote(market, v, stranger, side, 1)

    def test_record_moves_the_counts_and_keeps_a_short_history(self):
        c, v, at = in_town(2, trades_both)
        sell, buy, market = offer(v, 'sell'), offer(v, 'buy'), merchants.new()
        bought = merchants.quote(market, v, sell['key'], 'sell', 2)
        entry = merchants.record(market, v, bought, at)
        self.assertEqual(entry, dict(at=camp.iso(at), visit=v['id'], merchant=v['name'], key=sell['key'], name=goods.name(sell['key']),
                                     side='sell', qty=2, gold=bought['gold']))
        merchants.record(market, v, merchants.quote(market, v, buy['key'], 'buy', 1), at)
        self.assertEqual(market['trades'], {v['id']: dict(bought={sell['key']: 2}, sold={buy['key']: 1})})
        self.assertEqual((merchants.quote(market, v, sell['key'], 'sell', 1)['left'], merchants.quote(market, v, buy['key'], 'buy', 1)['left']),
                         (sell['qty'] - 2, buy['qty'] - 1))
        self.assertEqual([h['side'] for h in market['history']], ['buy', 'sell'], 'newest first')
        for i in range(merchants.HISTORY + 5):
            merchants.record(market, v, dict(bought, qty=1), at + timedelta(minutes=i))
        self.assertEqual(len(market['history']), merchants.HISTORY)
        self.assertEqual(market['history'][0]['at'], camp.iso(at + timedelta(minutes=merchants.HISTORY + 4)))


class StoredMarketTests(unittest.TestCase):
    def test_tidy_forgets_merchants_long_gone_and_a_stored_market_is_repaired(self):
        now = merchants.watch_of(T0)
        oldest = now - merchants.LOOKBACK_WATCHES - 1
        market = merchants.normalize(dict(
            trades={f'keymaster@{oldest - 1}': dict(bought={'12:2': 1}), f'gem_cutter@{oldest}': dict(sold={'14:27': 3}),
                    f'occultist@{now}': dict(bought={'14:58': 1, '99:9': 4, '12:2': -1, '12:1': True}, sold='x'), 'bad': 'x', 'junk': {}},
            history=[1, dict(id='kept')]))
        self.assertEqual(market['trades'][f'occultist@{now}'], dict(bought={'14:58': 1}, sold={}))
        self.assertNotIn('bad', market['trades'])
        self.assertEqual(market['history'], [dict(id='kept')])
        c = a_camp(2)
        self.assertTrue(merchants.tidy(market, c, T0))
        self.assertEqual(sorted(market['trades']), sorted([f'gem_cutter@{oldest}', f'occultist@{now}']),
                         'a merchant from before the lookback can never be in town again; one that could, stays')
        self.assertFalse(merchants.tidy(market, c, T0), 'nothing more to forget')
        for at in MONTH[:48]:
            for v in merchants.visits(c, at):
                merchants.record(market, v, dict(key=v['offers'][0]['key'], name='', side=v['offers'][0]['side'], qty=1, gold=0), at)
                merchants.tidy(market, c, at)
                self.assertIn(v['id'], market['trades'], 'the book of a merchant in town is kept')
        self.assertEqual(merchants.normalize('junk'), merchants.new())


class RegressionTests(unittest.TestCase):
    """Bugs the unit tests found (2026-09-25), fixed."""

    def test_buying_from_a_merchant_and_selling_straight_back_never_pays(self):
        # Was a bug: merchants.py:173-180 with the offers drawn at 113-120: a merchant's shelf starts at twice its target
        # stock (about 0.71 of its price, plus greed) and its wish at half its target (about 1.41 of its price, plus
        # premium). The Gem Cutter, Rune Scholar, Fortune Teller and Guild Envoy can sell and want the same good in
        # one visit, so the player buys a unit and sells it straight back at a profit, again and again: gold from
        # nothing, against economy.py's rule that a round trip at one place always loses.
        checked = set()
        for level in (2, 3, 4, 5):
            c = a_camp(level)
            for at in MONTH[::2]:
                for v in merchants.visits(c, at):
                    both = {o['key'] for o in v['offers'] if o['side'] == 'sell'} & {o['key'] for o in v['offers'] if o['side'] == 'buy'}
                    for key in sorted(k for k in both if (v['id'], k) not in checked):
                        checked.add((v['id'], key))
                        market = merchants.new()
                        paid = merchants.quote(market, v, key, 'sell', 1)['gold']
                        back = merchants.quote(market, v, key, 'buy', 1)['gold']
                        self.assertLess(back, paid, f"{v['name']} pays {back:,} for the {goods.name(key)} it sold for {paid:,}")


if __name__ == '__main__':
    unittest.main()
