"""Market math (0.9): prices follow the stock, orders cannot be split for profit,
a round trip always loses, stocks relax, seeds are stable. Pure functions only.
"""
import sys, unittest
from pathlib import Path
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import economy as E


class EconomyTests(unittest.TestCase):
    def test_scarce_goods_cost_more(self):
        self.assertAlmostEqual(E.unit_price(100, 400, 400), 100)
        self.assertGreater(E.unit_price(100, 100, 400), 199.9)
        self.assertLess(E.unit_price(100, 1600, 400), 50.1)
        self.assertAlmostEqual(E.unit_price(100, 0, 400), E.unit_price(100, 40, 400), msg='the floor price')

    def test_an_order_split_in_parts_never_costs_less_or_earns_more(self):
        base, target = 3_517, 250.0
        for stock in (60.0, 250.0, 731.5):
            whole = E.buy_cost(base, stock, target, 20)
            first = E.buy_cost(base, stock, target, 7)
            second = E.buy_cost(base, stock - 7, target, 13)
            self.assertGreaterEqual(first + second, whole)
            whole = E.sell_value(base, stock, target, 20)
            parts = E.sell_value(base, stock, target, 9) + E.sell_value(base, stock + 9, target, 11)
            self.assertLessEqual(parts, whole)
            one_by_one = sum(E.buy_cost(base, stock - i, target, 1) for i in range(20))
            self.assertGreaterEqual(one_by_one, E.buy_cost(base, stock, target, 20))

    def test_buying_and_selling_back_always_loses_the_margin(self):
        for base in (1, 7, 250, 99_999):
            for stock in (30.0, 100.0, 390.0):
                qty = min(25, E.available(stock, 100))
                if not qty:
                    continue
                paid = E.buy_cost(base, stock, 100, qty)
                back = E.sell_value(base, stock - qty, 100, qty)
                self.assertLess(back, paid)
                self.assertLessEqual(back, paid * (1 - E.MARGIN) / (1 + E.MARGIN) + 1)

    def test_markets_keep_a_reserve_and_a_ceiling(self):
        self.assertEqual(E.available(100, 400), 60)
        self.assertEqual(E.room(1590.5, 400), 9)
        with self.assertRaisesRegex(ValueError, 'only 60 to sell'):
            E.buy_cost(10, 100, 400, 61)
        with self.assertRaisesRegex(ValueError, 'only 9 more'):
            E.sell_value(10, 1590.5, 400, 10)
        self.assertEqual((E.buy_cost(10, 100, 400, 0), E.sell_value(10, 100, 400, -3)), (0, 0))

    def test_max_affordable_is_the_largest_count_that_fits(self):
        for gold in (0, 1, 999, 12_345, 10**9):
            n = E.max_affordable(137, 300, 200, gold, markup=0.05)
            self.assertLessEqual(E.buy_cost(137, 300, 200, n, 0.05), gold)
            if n < E.available(300, 200):
                self.assertGreater(E.buy_cost(137, 300, 200, n + 1, 0.05), gold)

    def test_stocks_relax_by_half_each_half_life(self):
        self.assertAlmostEqual(E.drift(0, 100, 12, 12), 50)
        self.assertAlmostEqual(E.drift(300, 100, 24, 12), 150)
        self.assertEqual(E.drift(7.5, 100, 0, 12), 7.5)
        self.assertEqual(E.drift(7.5, 100, 5, 0), 7.5)

    def test_seeds_are_stable_and_distinct(self):
        self.assertEqual(E.seed('town', 3, 'x'), E.seed('town', 3, 'x'))
        self.assertNotEqual(E.seed('town', 3, 'x'), E.seed('town', 3, 'y'))
        self.assertNotEqual(E.seed('ab', 'c'), E.seed('a', 'bc'), 'parts are separated')
        # pinned: a change here would reshuffle every saved town's merchants and events
        self.assertEqual(E.seed('fixed'), 0x992a93455c71fedd)
        self.assertEqual(E.rng('fixed').random(), 0.6164306181332422)
        self.assertEqual([E.rng('a', 1).random() for _ in range(2)], [E.rng('a', 1).random() for _ in range(2)])
        rand = E.rng('pick')
        picks = [E.weighted(rand, [('a', 1), ('b', 3), ('none', 0)]) for _ in range(4000)]
        self.assertNotIn('none', picks)
        self.assertAlmostEqual(picks.count('b') / len(picks), 0.75, delta=0.03)
        with self.assertRaises(ValueError):
            E.weighted(rand, [('a', 0)])


if __name__ == '__main__':
    unittest.main()
