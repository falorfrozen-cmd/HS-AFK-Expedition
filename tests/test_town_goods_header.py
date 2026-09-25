"""TownGoods.hpp (0.9): the plugin's list of what a town delivery may make is
generated from tools/goods.py by tools/gen_town_goods.py and must match it.

After a change to goods.py: run `py -3 -B tools/gen_town_goods.py`, then
rebuild the plugin. Standard library only; nothing contacts the game.
"""
import re, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import gen_town_goods as G
import goods


def accepted(header_text):
    """The (type, id) pairs the generated C++ accepts, read back from its text."""
    pairs = set()
    for item_type, body in re.findall(r'case (\d+):[^\n]*\n\s*return ([^;]*);', header_text):
        for first, last in re.findall(r'\(id >= (\d+) && id <= (\d+)\)', body):
            pairs.update((int(item_type), i) for i in range(int(first), int(last) + 1))
        pairs.update((int(item_type), int(i)) for i in re.findall(r'\bid == (\d+)', body))
    return pairs


class TownGoodsHeaderTests(unittest.TestCase):
    def test_the_header_is_up_to_date(self):
        self.assertTrue(G.HEADER.is_file(), f'{G.HEADER} is missing: run py -3 -B tools/gen_town_goods.py')
        self.assertEqual(G.HEADER.read_text(encoding='utf-8'), G.render(),
                         'TownGoods.hpp is out of date: run py -3 -B tools/gen_town_goods.py and rebuild the plugin')

    def test_the_header_accepts_exactly_the_goods(self):
        text = G.HEADER.read_text(encoding='utf-8')
        wanted = {tuple(int(part) for part in key.split(':')) for key in goods.GOODS}
        self.assertEqual(accepted(text), wanted)
        count = re.search(r'kTownGoodCount = (\d+);', text)
        self.assertIsNotNone(count)
        self.assertEqual(int(count.group(1)), len(wanted))

    def test_consecutive_ids_become_ranges(self):
        self.assertEqual(G.ranges([8, 7, 0, 1, 2, 5, 2]), [(0, 2), (5, 5), (7, 8)])
        self.assertEqual(G.ranges([]), [])
        text = G.render({'12:0': None, '12:1': None, '12:33': None, '15:4': None})
        self.assertIn('(id >= 0 && id <= 1) || id == 33;', text)
        self.assertIn('kTownGoodCount = 4;', text)
        self.assertEqual(accepted(text), {(12, 0), (12, 1), (12, 33), (15, 4)})

    def test_long_rows_wrap_and_mean_the_same(self):
        spread = {(14, i) for i in range(0, 40, 2)}   # 20 single ids: five rows of four
        text = G.render({f'{t}:{i}': None for t, i in spread})
        self.assertEqual(accepted(text), spread)
        self.assertEqual(text.count('            || '), 4)
        self.assertEqual(text.count(';\n    default:'), 1)

    def test_only_native_stackables_may_be_listed(self):
        for other in ('6:27', '16:1', '11:0'):
            with self.assertRaisesRegex(ValueError, 'not a native stackable'):
                G.render({other: None})
        for single in ('14:67', '14:59', '13:58', '13:64'):
            with self.assertRaisesRegex(ValueError, 'single item'):
                G.render({single: None})
        for junk in ('14', '14:x', ':3', '14:-1'):
            with self.assertRaisesRegex(ValueError, 'type:id'):
                G.render({junk: None})
        with self.assertRaisesRegex(ValueError, 'No goods'):
            G.render({})


if __name__ == '__main__':
    unittest.main()
