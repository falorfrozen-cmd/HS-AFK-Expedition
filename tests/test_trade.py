"""Trade with the other towns (0.9): quotes and tariffs, markets that move and recover,
planning a wagon, trading on arrival, unloading once, key racks, events and growth.
Plain dicts and fixed times; nothing reads the player's data or contacts the game.
"""
import sys, unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import camp, trade

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
FOUNDED = '2026-09-01T08:00:00Z'


def setup(post=1, stock=None, keys=None):
    """A camp with a Trading Post of ``post`` and its trade, founded with the camp."""
    c = camp.new(T0)
    c.update(founded_at=FOUNDED, stock=dict(stock or {}), keys=dict(keys or {}))
    c['buildings']['trading_post'] = post
    return trade.ensure(dict(camp=c)), c


def send(tr, c, town, cargo=None, orders=None, purse=0, run_id='wagon_a', at=T0):
    return trade.start_run(tr, c, trade.plan_run(tr, c, town, cargo or {}, orders or {}, purse, at), run_id, purse, at=at)


class MarketTests(unittest.TestCase):
    def test_a_town_asks_more_than_it_bids_and_standing_lowers_its_tariff(self):
        tr, _ = setup()
        for key in ('14:27', '14:0', '12:0', '15:34'):
            q = trade.quote(tr, 'ironhold', key, T0)
            self.assertGreater(q['ask'], q['bid'], key)

        def at_standing(traded):
            tr, _ = setup()
            tr['towns']['ironhold'] = dict(traded=traded, prosperity=0)
            return trade.standing(tr, 'ironhold'), trade.tariff(tr, 'ironhold'), trade.quote(tr, 'ironhold', '14:28', T0)
        rows = [at_standing(n) for n in trade.STANDING]
        self.assertEqual([r[0] for r in rows], list(range(len(trade.STANDING))))
        self.assertEqual([r[1] for r in rows], list(trade.TARIFF))
        asks, bids = [r[2]['ask'] for r in rows], [r[2]['bid'] for r in rows]
        self.assertEqual((asks, bids), (sorted(asks, reverse=True), sorted(bids)))
        self.assertLess(asks[-1] - bids[-1], asks[0] - bids[0], 'the best standing trades at the narrowest spread')
        self.assertEqual(at_standing(trade.STANDING[1] - 1)[0], 0, 'one gold short is not enough')

    def test_selling_lowers_the_next_price_and_the_market_recovers_by_the_hour(self):
        tr, _ = setup()
        target = trade.stock(tr, 'saltmarsh', '14:27', T0)
        before = trade.quote(tr, 'saltmarsh', '14:27', T0)
        self.assertEqual(trade.sell(tr, 'saltmarsh', '14:27', 2000, T0)[0], 2000)
        after = trade.quote(tr, 'saltmarsh', '14:27', T0)
        self.assertEqual(after['stock'], int(target + 2000))
        self.assertLess(after['bid'], before['bid'])
        self.assertLess(after['ask'], before['ask'])
        later = trade.quote(tr, 'saltmarsh', '14:27', T0 + timedelta(hours=9))        # the same day
        self.assertTrue(after['bid'] < later['bid'] <= before['bid'], (after['bid'], later['bid'], before['bid']))
        self.assertAlmostEqual(trade.stock(tr, 'saltmarsh', '14:27', T0 + timedelta(hours=trade.HALF_LIFE_HOURS)), target + 1000, places=3,
                               msg='halfway back after one half-life')
        room = trade.quote(tr, 'saltmarsh', '14:28', T0)['can_sell']
        self.assertEqual(trade.sell(tr, 'saltmarsh', '14:28', 10**6, T0)[0], room, 'a market takes only what it has room for')
        self.assertEqual(trade.sell(tr, 'saltmarsh', '14:28', 5, T0), (0, 0))

    def test_town_events_are_drawn_once_per_day(self):
        tr, _ = setup()
        days = [(T0 + timedelta(days=d)).strftime('%Y-%m-%d') for d in range(400)]
        drawn = [trade.event(tr, 'ironhold', day) for day in days]
        self.assertEqual(drawn, [trade.event(tr, 'ironhold', day) for day in days])
        self.assertAlmostEqual(sum(e is not None for e in drawn) / len(days), trade.EVENT_CHANCE, delta=0.06)
        self.assertNotEqual(drawn, [trade.event(tr, 'saltmarsh', day) for day in days], 'each town has its own news')
        self.assertNotEqual(drawn, [trade.event(dict(tr, founded_at='2026-01-01T00:00:00Z'), 'ironhold', day) for day in days])
        cave_in = next(d for d, e in enumerate(drawn) if e and e['key'] == 'cave_in')
        calm = next(d for d, e in enumerate(drawn) if e is None)
        bid = lambda day, key, hour=12: trade.quote(tr, 'ironhold', key, T0 + timedelta(days=day, hours=hour - 12))['bid']
        self.assertGreater(bid(cave_in, '14:28'), bid(calm, '14:28'), 'ore is dear after a cave-in')
        self.assertEqual(bid(cave_in, '14:0'), bid(calm, '14:0'), 'and nothing else is')
        self.assertEqual(bid(cave_in, '14:28', hour=1), bid(cave_in, '14:28', hour=23), 'the whole day agrees')

    def test_trading_grows_a_town_and_its_markets(self):
        tr, _ = setup()
        self.assertEqual(trade.development(tr, 'saltmarsh'), 1)
        wares, depth = set(trade.trades(tr, 'saltmarsh')), trade.stock(tr, 'saltmarsh', '14:27', T0)
        self.assertNotIn('13:0', wares, 'fragments need development 2')
        tr['towns']['saltmarsh'] = dict(traded=0, prosperity=trade.PROSPERITY[1])
        self.assertEqual(trade.development(tr, 'saltmarsh'), 2)
        self.assertLess(wares, set(trade.trades(tr, 'saltmarsh')))
        self.assertIn('13:0', trade.trades(tr, 'saltmarsh'))
        self.assertGreater(trade.stock(tr, 'saltmarsh', '14:27', T0), depth, 'a grown town holds more')
        tr['towns']['saltmarsh']['prosperity'] = 10**12
        self.assertEqual(trade.development(tr, 'saltmarsh'), 5)
        tr, c = setup(stock={'14:27': 200})
        run = send(tr, c, 'emberfall', {'14:27': 200}, {'14:0': 5})
        trade.settle(tr, T0 + timedelta(hours=3))
        grown = run['result']['earned'] + run['result']['spent']
        self.assertEqual(tr['towns']['emberfall'], dict(traded=grown, prosperity=grown), 'what it sold and what it bought')

    def test_a_round_trip_at_one_town_always_loses(self):
        for town, key in (('ironhold', '14:27'), ('emberfall', '14:0'), ('saltmarsh', '14:28')):
            for standing in (0, len(trade.STANDING) - 1):
                tr, _ = setup()
                tr['towns'][town] = dict(traded=trade.STANDING[standing], prosperity=0)
                bought, paid = trade.buy(tr, town, key, 50, 10**9, T0)
                sold, back = trade.sell(tr, town, key, bought, T0)
                self.assertEqual((bought, sold), (50, 50))
                self.assertLess(back, paid, (town, key, standing))


class WagonPlanTests(unittest.TestCase):
    def test_a_wagon_needs_a_post_a_known_town_within_reach_and_a_free_wagon(self):
        tr, c = setup(post=0, stock={'14:27': 100})
        load = {'14:27': 10}
        with self.assertRaisesRegex(ValueError, 'Build a Trading Post'):
            trade.plan_run(tr, c, 'ironhold', load, {}, 0, T0)
        c['buildings']['trading_post'] = 1
        with self.assertRaisesRegex(ValueError, 'Unknown town'):
            trade.plan_run(tr, c, 'atlantis', load, {}, 0, T0)
        with self.assertRaisesRegex(ValueError, 'Duskhaven is too far: it needs Trading Post level 2'):
            trade.plan_run(tr, c, 'duskhaven', load, {}, 0, T0)
        self.assertEqual(trade.reachable(c), ['ironhold', 'emberfall', 'saltmarsh'])
        plan = trade.plan_run(tr, c, 'ironhold', load, {}, 0, T0)
        self.assertEqual(plan, dict(town='ironhold', cargo=load, orders={}, purse=0, hours=1.5))
        trade.start_run(tr, c, plan, 'wagon_a', 0, at=T0)
        with self.assertRaisesRegex(ValueError, 'Every wagon is on the road'):
            trade.plan_run(tr, c, 'ironhold', load, {}, 0, T0)
        trade.settle(tr, T0 + timedelta(hours=4))
        with self.assertRaisesRegex(ValueError, 'Every wagon is on the road'):
            trade.plan_run(tr, c, 'ironhold', load, {}, 0, T0 + timedelta(hours=4))    # back, but not unloaded
        c['buildings']['trading_post'] = 5
        self.assertEqual(trade.reachable(c), [t['key'] for t in trade.TOWNS])
        self.assertEqual(trade.plan_run(tr, c, 'goldcrest', load, {}, 0, T0)['hours'], round(7.0 / trade.SPEED[5], 4), 'faster wagons')

    def test_a_load_must_be_in_the_stock_traded_there_and_fit_the_wagon(self):
        tr, c = setup(stock={'14:27': 600, '12:2': 5})
        with self.assertRaisesRegex(ValueError, 'The camp stock has only 600 Copper Ore'):
            trade.plan_run(tr, c, 'ironhold', {'14:27': 700}, {}, 0, T0)
        with self.assertRaisesRegex(ValueError, 'A wagon carries at most 500 units each way'):
            trade.plan_run(tr, c, 'ironhold', {'14:27': 501}, {}, 0, T0)
        with self.assertRaisesRegex(ValueError, 'A wagon carries at most 500 units each way'):
            trade.plan_run(tr, c, 'ironhold', {}, {'14:28': 501}, 1000, T0)
        with self.assertRaisesRegex(ValueError, 'Saltmarsh does not trade Bifröst Key yet'):
            trade.plan_run(tr, c, 'saltmarsh', {'12:2': 1}, {}, 0, T0)
        with self.assertRaisesRegex(ValueError, 'Ironhold does not trade Ol Rune yet'):
            trade.plan_run(tr, c, 'ironhold', {}, {'15:1': 1}, 1000, T0)
        with self.assertRaisesRegex(ValueError, 'Load goods to sell or give orders to buy'):
            trade.plan_run(tr, c, 'ironhold', {}, None, 1000, T0)
        with self.assertRaisesRegex(ValueError, 'is not a trade good'):
            trade.plan_run(tr, c, 'ironhold', {'99:1': 1}, {}, 0, T0)
        for count in (0, 100_001, 1.5, True):
            with self.assertRaisesRegex(ValueError, 'Trade whole counts from 1 to 100,000'):
                trade.plan_run(tr, c, 'ironhold', {'14:27': count}, {}, 0, T0)
        with self.assertRaisesRegex(ValueError, 'must list goods and counts'):
            trade.plan_run(tr, c, 'ironhold', ['14:27'], {}, 0, T0)
        self.assertEqual(c['stock'], {'14:27': 600, '12:2': 5}, 'planning takes nothing')

    def test_rack_keys_are_no_cargo_and_the_purse_is_whole_gold_within_bounds(self):
        tr, c = setup(stock={'14:27': 100}, keys={'0': 10, '1': 10})
        for key in trade.RACK_KEYS:
            with self.assertRaisesRegex(ValueError, 'Keys on the key rack stay with the adventurers'):
                trade.plan_run(tr, c, 'ironhold', {key: 1}, {}, 0, T0)
        self.assertEqual(trade.plan_run(tr, c, 'ironhold', {}, {'12:0': 5}, 10_000, T0)['orders'], {'12:0': 5}, 'a wagon may buy them')
        for purse in (-1, trade.MAX_WAGON_GOLD + 1, 1.5, True, '100', None):
            with self.assertRaisesRegex(ValueError, 'A purse is a whole amount of gold'):
                trade.plan_run(tr, c, 'ironhold', {'14:27': 1}, {}, purse, T0)
        self.assertEqual(trade.plan_run(tr, c, 'ironhold', {'14:27': 1}, {}, trade.MAX_WAGON_GOLD, T0)['purse'], trade.MAX_WAGON_GOLD)
        with self.assertRaisesRegex(ValueError, 'Give the wagon gold'):
            trade.plan_run(tr, c, 'ironhold', {}, {'14:28': 5}, 0, T0)
        self.assertEqual(trade.plan_run(tr, c, 'ironhold', {'14:27': 50}, {'14:28': 5}, 0, T0)['purse'], 0, 'its takings can pay the orders')


class WagonRunTests(unittest.TestCase):
    def test_a_wagon_takes_its_cargo_and_unloads_once_when_home(self):
        tr, c = setup(stock={'14:27': 300, '14:28': 50})
        run = send(tr, c, 'emberfall', {'14:27': 300, '14:28': 20}, {'14:0': 5})
        self.assertEqual(c['stock'], {'14:28': 30}, 'the cargo left the stock; an emptied good leaves it')
        self.assertEqual((run['state'], run['left_at'], run['arrives_at'], run['returns_at']),
                         ('travelling', '2026-09-25T12:00:00Z', '2026-09-25T14:30:00Z', '2026-09-25T17:00:00Z'))
        for when in (T0 + timedelta(hours=1), T0 + timedelta(hours=3)):          # on the way there, then on the way back
            with self.assertRaisesRegex(ValueError, 'The wagon is still on the road'):
                trade.unload(tr, c, 'wagon_a', when)
        self.assertEqual(run['state'], 'arrived')
        self.assertEqual(trade.returned(tr, T0 + timedelta(hours=4, minutes=59)), [])
        self.assertEqual(trade.returned(tr, T0 + timedelta(hours=5)), [run])
        out = trade.unload(tr, c, 'wagon_a', T0 + timedelta(hours=5))
        result = run['result']
        self.assertEqual((result['sold'], result['bought'], result['unsold']), ({'14:27': 300, '14:28': 20}, {'14:0': 5}, {}))
        self.assertEqual(c['stock'], {'14:28': 30, '14:0': 5})
        self.assertEqual((tr['coffer'], out['entry']['gold_back'], tr['runs'], tr['history'][0]['id']), (result['gold_back'], result['gold_back'], [], 'wagon_a'))
        with self.assertRaisesRegex(ValueError, 'No such wagon'):
            trade.unload(tr, c, 'wagon_a', T0 + timedelta(hours=6))
        self.assertEqual(tr['coffer'], result['gold_back'], 'paid once')

    def test_wagons_trade_on_arrival_in_the_order_they_arrive(self):
        tr, c = setup(post=2, stock={'14:27': 1000})
        late = send(tr, c, 'ironhold', {'14:27': 500}, run_id='a_late', at=T0 + timedelta(minutes=30))
        early = send(tr, c, 'ironhold', {'14:27': 500}, run_id='b_early', at=T0)
        self.assertEqual(trade.settle(tr, T0 + timedelta(hours=1)), [], 'nobody has arrived yet')
        self.assertEqual([r['id'] for r in trade.settle(tr, T0 + timedelta(hours=3))], ['b_early', 'a_late'])
        self.assertGreater(early['result']['earned'], late['result']['earned'], 'the first to arrive sold into the fresher market')
        self.assertEqual((early['result']['at'], late['result']['at']), (early['arrives_at'], late['arrives_at']))
        self.assertEqual(trade.settle(tr, T0 + timedelta(hours=3)), [], 'a wagon trades once')

    def test_a_wagon_buys_with_no_more_than_its_purse_and_its_takings(self):
        tr, c = setup(stock={'14:27': 200})
        run = send(tr, c, 'emberfall', {'14:27': 200}, {'14:0': 500}, purse=1000)
        trade.settle(tr, T0 + timedelta(hours=3))
        result = run['result']
        self.assertTrue(0 < result['bought']['14:0'] < 500, 'the gold ran out before the order did')
        self.assertLessEqual(result['spent'], 1000 + result['earned'])
        self.assertEqual(result['gold_back'], 1000 + result['earned'] - result['spent'])
        arrived = camp.parse_iso(run['arrives_at'])
        self.assertEqual(trade.buy(deepcopy(tr), 'emberfall', '14:0', 1, result['gold_back'], arrived), (0, 0), 'not one more fits in the change')
        tr, c = setup()
        purse_only = send(tr, c, 'emberfall', orders={'14:0': 500}, purse=5000)
        trade.settle(tr, T0 + timedelta(hours=3))
        self.assertEqual(purse_only['result']['earned'], 0)
        self.assertTrue(0 < purse_only['result']['spent'] <= 5000)

    def test_rack_keys_bought_away_arrive_on_the_key_rack(self):
        tr, c = setup()
        send(tr, c, 'ironhold', orders={'12:0': 4, '12:2': 1}, purse=200_000)
        out = trade.unload(tr, c, 'wagon_a', T0 + timedelta(hours=3))
        self.assertEqual((c['keys'], c['stock']), ({'0': 4}, {'12:2': 1}), 'Basic Keys on the rack, a Bifröst Key in the stock')
        self.assertEqual(out['stock'], {'12:2': 1, '12:0': 4})
        full = camp.new(T0)                                     # Storehouse 1: 25 keys, 500 units of stock
        self.assertEqual(trade.arrive(full, {'12:0': 30, '14:27': 5000}), {'14:27': 5000, '12:0': 30}, 'what arrived is here, past the caps')
        self.assertEqual((full['keys'], full['stock']), ({'0': 30}, {'14:27': 5000}))


class StoredTradeTests(unittest.TestCase):
    def test_a_stored_trade_is_repaired_and_founded_with_the_camp(self):
        tr = trade.normalize(dict(markets={'ironhold': {}, 'atlantis': {}, 'saltmarsh': 'x'}, towns={'ironhold': dict(traded=5, prosperity=-3), 'atlantis': {}},
                                  runs=[dict(id='w', town='atlantis', state='travelling'), dict(town='ironhold'), 'x'], coffer=True, history=[1, dict(id='h')]))
        self.assertEqual((tr['markets'], tr['towns'], tr['runs'], tr['coffer'], tr['history']),
                         ({'ironhold': {}}, {'ironhold': dict(traded=5, prosperity=0)}, [], 0, [dict(id='h')]))
        state = dict(camp=camp.new(T0))
        first = trade.ensure(state)
        self.assertEqual(first['founded_at'], state['camp']['founded_at'])
        state['camp']['founded_at'] = '2027-01-01T00:00:00Z'
        self.assertEqual(trade.ensure(state)['founded_at'], first['founded_at'], 'founded once')

    def test_a_stored_trade_keeps_only_runs_it_can_settle(self):
        # Was a bug: trade.py:121-122: normalize keeps any run with an id and a known town, but settle (322), returned (349),
        # plan_run (265) and view read r['state'] and r['arrives_at'] directly, so one hand-edited or cut-short run
        # raises KeyError on every load of the town. town.normalize and camp.normalize keep only queue entries their
        # settle can read, and merchants.tidy skips keys it cannot read.
        tr = trade.normalize(dict(runs=[dict(id='wagon_x', town='ironhold')]))
        try:
            trade.settle(tr, T0)
        except KeyError as error:
            self.fail(f'a stored run without {error} breaks settle')


if __name__ == '__main__':
    unittest.main()
