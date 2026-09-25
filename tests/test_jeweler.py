"""The Jeweler (0.8): the game's jewel recipes read by the plugin, sessions paid from the
camp's stock, crafts delivered through the game, and miners' materials routed to the stock.
A fake plugin answers like the real one; nothing contacts the game or Windows.
"""
import json, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, camp, panel, worker_jeweler as J, workers as W

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
HERO = dict(identity_version=2, slot=1, name='Suh', **{'class': 8})
# The first two of the game's jewel recipes (index, result type, output, inputs), as the plugin reports them.
GAME = [dict(index=40, result_type=37, output=dict(type=15, id=82, amount=1), inputs=[dict(type=14, id=3, amount=3), dict(type=14, id=2, amount=2)]),
        dict(index=41, result_type=37, output=dict(type=15, id=83, amount=1), inputs=[dict(type=14, id=2, amount=4)]),
        dict(index=58, result_type=41, output=dict(type=15, id=78, amount=1), inputs=[dict(type=14, id=20, amount=2), dict(type=14, id=44, amount=1)]),
        dict(index=9, result_type=53, output=dict(type=15, id=82, amount=1), inputs=[dict(type=14, id=3, amount=1)]),
        dict(index=10, result_type=37, output=dict(type=6, id=82, amount=1), inputs=[dict(type=14, id=3, amount=1)])]


def jeweler_state(level=1, bench=1, stock=None, **skills):
    st = W.empty(T0); st['camp']['buildings'].update(hq=3, jeweler_bench=bench, storehouse=3)
    st['camp']['stock'] = dict(stock if stock is not None else {'14:3': 300, '14:2': 300})
    w = W.new_worker(st, 'Opal', worker_type='jeweler', at=T0)
    w['xp'] = sum(W.xp_to_next(l) for l in range(1, level)); w['level'] = level; w['skills'] = dict(skills)
    return st, w


class RecipeTests(unittest.TestCase):
    def test_only_the_games_jewel_recipes_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            kept = J.keep_recipes(tmp, dict(recipes=GAME + [dict(index='x')], at='now'), 'B')
            self.assertEqual([r['index'] for r in kept['recipes']], [40, 41, 58], 'other result types and non-socketable outputs are dropped')
            self.assertEqual(J.load_recipes(tmp)['build'], 'B')
        view = J.recipe_view(kept['recipes'][0])
        self.assertEqual((view['name'], view['tier'], view['level']), ('Exan Jewel', 1, 1))
        self.assertEqual(J.recipe_view(kept['recipes'][2])['name'], 'Angelic Gem')
        st, w = jeweler_state(level=40, bench=4)
        self.assertTrue(J.usable(w, kept['recipes'][0], 4)); self.assertFalse(J.usable(w, kept['recipes'][2], 4), 'gems need the Bench at 5')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.recipes = dict(build='B', recipes=[r for r in GAME if r['result_type'] in (37, 41) and r['output']['type'] == 15])

    def test_a_session_pays_its_materials_up_front_from_the_stock(self):
        st, w = jeweler_state()
        trip = W.start_trip(st, w['id'], 40, 2, at=T0, seed=1, recipes=self.recipes)
        self.assertEqual(trip['planned'], 12, 'six crafts an hour')
        self.assertEqual(trip['taken'], {'14:3': 36, '14:2': 24})
        self.assertEqual(st['camp']['stock'], {'14:3': 264, '14:2': 276})
        W.cancel_trip(st, w['id'])
        self.assertEqual(st['camp']['stock'], {'14:3': 300, '14:2': 300}, 'a cancelled session gives every material back')
        st2, w2 = jeweler_state(stock={'14:2': 7})
        self.assertEqual(W.start_trip(st2, w2['id'], 41, 8, at=T0, seed=1, recipes=self.recipes)['planned'], 1, 'the stock limits the session')
        st3, w3 = jeweler_state(stock={})
        with self.assertRaisesRegex(ValueError, 'cannot pay for one Exan Jewel'):
            W.start_trip(st3, w3['id'], 40, 1, at=T0, recipes=self.recipes)
        with self.assertRaisesRegex(ValueError, "needs Jeweler's Bench level 5 and a level 32 jeweler"):
            W.start_trip(st3, w3['id'], 58, 1, at=T0, recipes=self.recipes)
        with self.assertRaisesRegex(ValueError, "Choose one of the game's jewel recipes"):
            W.start_trip(st3, w3['id'], 999, 1, at=T0, recipes=self.recipes)

    def test_an_early_collect_makes_fewer_jewels_and_returns_the_rest(self):
        st, w = jeweler_state(keen_cut=0)
        trip = W.start_trip(st, w['id'], 40, 2, at=T0, seed=3, recipes=self.recipes)
        plan = J.delivery_plan(w, trip, 1.0, at=T0)
        self.assertEqual((plan['craft_count'], plan['items']), (6, []))
        self.assertEqual(plan['crafts'], [dict(recipe=40, count=plan['make'])], "the plugin gets the game's recipe index and how many")
        self.assertEqual(plan['make'], plan['craft_count'] + plan['extra'])
        out = J.apply(st, w, plan, dict(state='done', crafted={'15:82': plan['make']}), at=T0)
        self.assertEqual(out['stock_back'], {'14:3': 18, '14:2': 12}); self.assertEqual(st['camp']['stock'], {'14:3': 282, '14:2': 288})
        self.assertEqual(w['stats']['jewels_made'], {'15:82': plan['make']}); self.assertIsNone(w['trip'])
        self.assertEqual(out['camp']['dust'], plan['resources']['dust'])
        self.assertEqual(plan['resources']['dust'], 1 * 30 + 5 * plan['make'], 'one dust per material, five per jewel')

    def test_skills_save_materials_and_cut_second_jewels(self):
        st, w = jeweler_state(level=20, bench=5, keen_cut=5, perfect_cut=3)
        trip = W.start_trip(st, w['id'], 40, 8, at=T0, seed=8, recipes=self.recipes)
        h = J.haul(w, trip, 8.0)
        self.assertGreater(h['saved'], 0); self.assertGreater(h['extra'], 0)
        self.assertEqual(h['used'], h['craft_count'] - h['saved'])
        plan = J.delivery_plan(w, trip, 8.0, at=T0)
        self.assertEqual(plan['craft_count'], h['craft_count'])


class RouteTests(unittest.TestCase):
    def test_a_miners_materials_fill_the_stock_only_when_the_game_rolled_without_making_them(self):
        st = W.empty(T0)
        m = W.new_worker(st, 'Brom', at=T0); m['level'] = 20; m['skills'] = dict(keen_eye=1, gem_sense=4)
        with self.assertRaisesRegex(ValueError, "Build the Jeweler's Bench first"):
            W.set_route(st, m['id'], 'stock')
        st['camp']['buildings'].update(hq=3, jeweler_bench=1)
        W.set_route(st, m['id'], 'stock')
        trip = W.start_trip(st, m['id'], 27, 2, at=T0, seed=2)
        plan = W.delivery_plan(m, trip, 2.0, at=T0)
        self.assertTrue(plan['route_prospect'])
        old = W.apply_delivery(st, m['id'], plan, dict(created={}, prospect_outputs={'14:2': 5}), at=T0)
        self.assertEqual((old['stock'], st['camp']['stock']), ({}, {}), 'an older plugin made them in the game: nothing is counted twice')
        W.start_trip(st, m['id'], 27, 2, at=T0, seed=2)
        plan = W.delivery_plan(m, m['trip'], 2.0, at=T0)
        new = W.apply_delivery(st, m['id'], plan, dict(created={}, prospect_outputs={'14:2': 5, '14:3': 2}, routed=True), at=T0)
        self.assertEqual(new['stock'], {'14:2': 5, '14:3': 2}); self.assertEqual(st['camp']['stock'], {'14:2': 5, '14:3': 2})


class FakePlugin:
    """`worker recipes` and `worker deliver` answered like the plugin does."""
    def __init__(self, data):
        self.data, self.commands = Path(data), []

    def __call__(self, *_):
        return self

    def send(self, line, timeout=30):
        self.commands.append(line)
        words = line.split()
        if words[1:3] == ['worker', 'recipes']:
            afk.write_json(self.data / 'models' / f'jewel-recipes-{words[3]}.json', dict(ok=True, recipes=GAME, at='now'))
        elif words[1:3] == ['worker', 'deliver']:
            plan = json.loads(Path(' '.join(words[3:])).read_text(encoding='utf-8'))
            crafted = {'15:82': sum(c['count'] for c in plan['crafts'])}
            afk.write_json(self.data / 'sessions' / f"{plan['delivery_id']}.result.json", dict(delivery_id=plan['delivery_id'], state='done', crafted=crafted, routed=False))
            (self.data / 'spool' / f"{plan['delivery_id']}.ndjson").write_text('', encoding='utf-8')
        return ['ok']


class PanelJewelerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name) / 'afk'
        for sub in ('sessions', 'spool', 'plans', 'models'): (self.d / sub).mkdir(parents=True)
        afk.write_json(self.d / 'config.json', dict(game_bin=str(self.d))); (self.d / 'Hero_Siege.exe').write_bytes(b'x')
        afk.write_json(self.d / 'build.json', dict(game_build='B'))
        self.app = panel.Panel(self.d); self.app.job = dict(output='')
        live = dict(character=HERO, room='Town_01_rm', replay_running=False, online=False)
        for p in [patch.object(self.app, 'fresh', return_value=live), patch.object(self.app, 'cli'), patch.object(self.app, 'sync_notification'),
                  patch.object(panel.notify, 'sync_all', side_effect=AssertionError('Task Scheduler')),
                  patch.object(panel.worker_loot, 'pools', return_value=dict(build='B', chests={}, goblins={}))]:
            p.start(); self.addCleanup(p.stop)

    def test_a_session_reads_the_recipes_from_the_game_and_its_jewels_are_made_there(self):
        st, w = jeweler_state(); W.save(self.d, st)
        plugin = FakePlugin(self.d)
        with patch.object(afk, 'Ipc', plugin):
            self.app.action('worker_start', dict(worker=w['id'], recipe=40, hours=1))
        self.assertTrue(any(c.startswith('afk worker recipes ') for c in plugin.commands))
        self.assertEqual([r['index'] for r in J.load_recipes(self.d)['recipes']], [40, 41, 58])
        self.assertEqual(list((self.d / 'models').glob('jewel-recipes-*.json')), [], 'the plugin answer file is removed after reading')
        st = W.load(self.d); st['workers'][0]['trip']['started_at'] = '2026-01-01T00:00:00Z'; W.save(self.d, st)
        with patch.object(afk, 'Ipc', plugin):
            self.app.action('worker_collect', dict(worker=w['id']))
        st = W.load(self.d); opal = st['workers'][0]
        self.assertIsNone(opal['trip']); self.assertEqual(opal['stats']['crafts'], 6)
        self.assertEqual(self.app.cli.call_args.args[0], 'ingest', 'the jewels go to the Vault')
        self.assertIn('jewels made', self.app.job['output'])
        overview = self.app.workers_overview()
        self.assertEqual([r['name'] for r in overview['recipes']], ['Exan Jewel', 'Wilrden Jewel', 'Angelic Gem'])
        self.assertEqual(overview['material_names'][44], 'Enchanted Sigil')


if __name__ == '__main__':
    unittest.main()
