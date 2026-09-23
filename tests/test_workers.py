"""Workers (0.7.0): levels, the skill tree, trips, hauls, hire payments and deliveries.

The game is a fake IPC that writes the receipts and results the plugin would;
nothing contacts the real game, Windows or the Item Editor.
"""
import json, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, panel, workers as W

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
HERO = dict(identity_version=2, slot=1, name='Suh', **{'class': 8})


def crew(level=1, **skills):
    st = W.empty(); w = W.new_worker(st, 'Brom', at=T0)
    w['xp'] = sum(W.xp_to_next(l) for l in range(1, level)); w['level'] = level; w['skills'] = dict(skills)
    return st, w


class LevelTests(unittest.TestCase):
    def test_levels_grow_steeper_and_give_a_point_each(self):
        costs = [W.xp_to_next(l) for l in range(1, W.MAX_LEVEL)]
        self.assertEqual(costs, sorted(costs)); self.assertEqual(costs[0], 12)
        self.assertEqual(W.level_for(0), (1, 0, 12))
        self.assertEqual(W.level_for(12)[0], 2)
        self.assertEqual(W.level_for(10 ** 9)[0], W.MAX_LEVEL)
        self.assertEqual(W.points_for(1), 0); self.assertEqual(W.points_for(50), 49)
        self.assertEqual(sum(n['max'] for n in W.TREE), 58, 'more ranks than points: choices matter')

    def test_ores_unlock_by_level(self):
        _, w = crew(12)
        self.assertEqual([o['name'] for o in W.unlocked(w)], ['Copper Ore', 'Iron Ore', 'Gold Ore'])
        _, top = crew(36)
        self.assertEqual(len(W.unlocked(top)), 6)


class TreeTests(unittest.TestCase):
    def test_points_prerequisites_and_ranks(self):
        st, w = crew(1)
        with self.assertRaisesRegex(ValueError, 'No skill points'): W.learn(st, w['id'], 'swift_pick')
        st, w = crew(10)
        with self.assertRaisesRegex(ValueError, 'needs Swift Pick rank 2'): W.learn(st, w['id'], 'deep_delver')
        W.learn(st, w['id'], 'swift_pick'); W.learn(st, w['id'], 'swift_pick'); W.learn(st, w['id'], 'deep_delver')
        self.assertEqual(W.view(w)['points'], 9 - 3)
        for _ in range(3): W.learn(st, w['id'], 'foreman')
        with self.assertRaisesRegex(ValueError, 'highest rank'): W.learn(st, w['id'], 'foreman')
        with self.assertRaisesRegex(ValueError, 'Unknown skill'): W.learn(st, w['id'], 'teleport')
        W.respec(w); self.assertEqual(W.spent(w), 0); self.assertEqual(W.view(w)['points'], 9)

    def test_skill_effects(self):
        _, base = crew(40)
        _, fast = crew(40, swift_pick=5, deep_delver=3, night_crew=2, long_shift=4, quick_hands=5)
        ruby = W.ORE_BY_ID[30]
        self.assertAlmostEqual(W.digs_per_hour(base, ruby, 8), 20 * 0.7)
        self.assertAlmostEqual(W.digs_per_hour(fast, ruby, 8), 20 * 0.7 * 1.25 * 1.3 * 1.2)
        self.assertAlmostEqual(W.digs_per_hour(fast, ruby, 5), 20 * 0.7 * 1.25 * 1.3, msg='Night Crew needs a 6 hour trip')
        self.assertEqual(W.max_trip_hours(fast), 12); self.assertAlmostEqual(W.time_factor(fast), 0.8)


class TripTests(unittest.TestCase):
    def test_trip_rules_and_progress(self):
        st, w = crew(5, long_shift=1, quick_hands=2)
        with self.assertRaisesRegex(ValueError, 'level 12'): W.start_trip(st, w['id'], 29, 1, at=T0)
        with self.assertRaisesRegex(ValueError, 'Choose an ore'): W.start_trip(st, w['id'], 99, 1, at=T0)
        with self.assertRaisesRegex(ValueError, '9 hours'): W.start_trip(st, w['id'], 28, 9.5, at=T0)
        trip = W.start_trip(st, w['id'], 28, 9, at=T0, seed=1)
        self.assertAlmostEqual(trip['real_hours'], 9 * 0.92)
        with self.assertRaisesRegex(ValueError, 'already on a trip'): W.start_trip(st, w['id'], 27, 1, at=T0)
        half = W.trip_view(w, T0 + timedelta(hours=9 * 0.92 / 2))
        self.assertFalse(half['ready']); self.assertAlmostEqual(half['credited_work_hours'], 4.5)
        self.assertTrue(W.trip_view(w, T0 + timedelta(hours=9))['ready'])
        W.cancel_trip(st, w['id']); self.assertIsNone(w['trip'])

    def test_a_haul_is_decided_by_the_trip_seed(self):
        st, w = crew(30, full_cart=5, keen_eye=5, gem_sense=4, lucky_find=3, crystal_heart=3, goblin_bait=3, motherlode=5, double_strike=5, swift_pick=5)
        trip = W.start_trip(st, w['id'], 30, 8, at=T0, seed=99)
        a, b = W.haul(w, trip, 8), W.haul(w, trip, 8)
        self.assertEqual(a, b, 'the same trip never re-rolls')
        ore = next(i for i in a['items'] if i['id'] == 30)
        self.assertEqual(ore['amount'] + a['prospect']['14:30'], a['ore_total'], 'prospected ore is used up')
        self.assertAlmostEqual(a['prospect']['14:30'] / a['ore_total'], 0.20, places=2)
        self.assertTrue(all(i['type'] == 14 and (i['id'] in (30, 58, 60, 66)) for i in a['items']))
        _, plain = crew(30)
        plain_trip = dict(trip); p = W.haul(plain, plain_trip, 8)
        self.assertGreater(a['ore_total'], p['ore_total'] * 1.3, 'Haul and Pickwork skills pay')
        self.assertEqual(p['prospect'], {}); self.assertEqual(p['finds'], dict(fragments=0, shards=0, crystals=0))
        self.assertLess(W.haul(w, trip, 2)['digs'], a['digs'], 'an early collection mines less')

    def test_a_delivery_brings_experience_levels_and_history(self):
        st, w = crew(1)
        trip = W.start_trip(st, w['id'], 27, 8, at=T0, seed=5)
        plan = W.delivery_plan(w, trip, 8, at=T0)
        self.assertTrue(plan['delivery_id'].startswith('worker_'))
        out = W.apply_delivery(st, w['id'], plan, dict(created={'14:27': plan['ore_total']}, prospect_outputs={}), at=T0)
        self.assertGreater(out['levels'], 0); self.assertIsNone(w['trip'])
        self.assertEqual(w['stats']['ore']['27'], plan['ore_total']); self.assertEqual(w['history'][0]['delivery_id'], plan['delivery_id'])
        self.assertEqual(w['stats']['finds'], {}, 'the trip ore is not a find')

    def test_hiring_rules(self):
        st = W.empty()
        self.assertEqual(W.hire_price(st), 250_000)
        for _ in range(3): W.new_worker(st)
        self.assertIsNone(W.hire_price(st))
        with self.assertRaisesRegex(ValueError, 'crew is full'): W.new_worker(st)
        with self.assertRaisesRegex(ValueError, 'Names use'): W.rename(st, st['workers'][0]['id'], '<script>')
        self.assertEqual([w['name'] for w in st['workers']], ['Miner 1', 'Miner 2', 'Miner 3'])


class FakeGame:
    """The plugin's side of `afk worker pay` / `afk worker deliver`."""
    def __init__(self, data, gold=1_000_000, pay_ok=True, deliver_ok=True):
        self.data, self.gold, self.pay_ok, self.deliver_ok, self.commands = Path(data), gold, pay_ok, deliver_ok, []

    def __call__(self, *_):
        return self

    def send(self, line, timeout=30):
        self.commands.append(line)
        words = line.split()
        if words[1:3] == ['worker', 'pay']:
            request, amount = words[3], int(words[4])
            ok = self.pay_ok and self.gold >= amount
            before = self.gold
            if ok: self.gold -= amount
            afk.write_json(self.data / 'models' / f'worker-pay-{request}.json',
                           dict(request_id=request, ok=ok, amount=amount, gold_before=before, gold_after=self.gold,
                                saved='saved (character and account save performed)' if ok else '', error='' if ok else 'not enough gold'))
        elif words[1:3] == ['worker', 'deliver']:
            plan = json.loads(Path(' '.join(words[3:])).read_text(encoding='utf-8'))
            created = {f"{i['type']}:{i['id']}": i['amount'] for i in plan['items']}
            state = 'done' if self.deliver_ok else 'error'
            afk.write_json(self.data / 'sessions' / f"{plan['delivery_id']}.result.json",
                           dict(delivery_id=plan['delivery_id'], state=state, error='' if self.deliver_ok else 'a stack could not be created',
                                created=created, prospect_outputs={'14:1': 3} if plan['prospect'] else {}, stacks=len(created)))
            (self.data / 'spool' / f"{plan['delivery_id']}.ndjson").write_text('', encoding='utf-8')
        return ['ok']


class PanelWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name) / 'afk'
        for sub in ('sessions', 'spool', 'plans', 'models'): (self.d / sub).mkdir(parents=True)
        afk.write_json(self.d / 'config.json', dict(game_bin=str(self.d)))
        (self.d / 'Hero_Siege.exe').write_bytes(b'x')
        self.app = panel.Panel(self.d); self.app.job = dict(output='')
        self.live = dict(character=HERO, room='Town_01_rm', replay_running=False, online=False)
        # Never reach Task Scheduler or the game from a test (sync_notification
        # would schedule real "haul ready" tasks on this computer).
        patchers = [patch.object(self.app, 'fresh', return_value=self.live), patch.object(self.app, 'cli'),
                    patch.object(self.app, 'sync_notification'), patch.object(panel.notify, 'sync_all', side_effect=AssertionError('Task Scheduler')),
                    patch.object(panel.notify, 'toast', side_effect=AssertionError('Windows notification'))]
        for p in patchers: p.start(); self.addCleanup(p.stop)

    def run_action(self, name, args, game):
        with patch.object(afk, 'Ipc', game):
            self.app.action(name, args)

    def test_hiring_takes_the_gold_through_the_game_and_never_twice(self):
        game = FakeGame(self.d)
        self.run_action('worker_hire', dict(name='Brom'), game)
        st = W.load(self.d)
        self.assertEqual([w['name'] for w in st['workers']], ['Brom']); self.assertEqual(game.gold, 750_000)
        self.assertEqual(st['workers'][0]['payment']['amount'], 250_000)
        self.assertEqual([p['state'] for p in st['payments'].values()], ['paid'])
        poor = FakeGame(self.d, gold=10)
        with self.assertRaisesRegex(ValueError, 'did not take the gold: not enough gold'):
            self.run_action('worker_hire', {}, poor)
        st = W.load(self.d)
        self.assertEqual(len(st['workers']), 1, 'a refused payment hires nobody')
        self.assertEqual(sorted(p['state'] for p in st['payments'].values()), ['paid', 'refused'])
        self.assertEqual(self.app.workers_view()['hire_price'], 1_000_000)

    def test_a_trip_is_collected_through_the_game_and_sent_to_the_vault(self):
        st = W.empty(); w = W.new_worker(st, 'Brom'); w['xp'] = 10 ** 6; w['level'] = W.level_for(10 ** 6)[0]
        w['skills'] = dict(keen_eye=1, gem_sense=4); W.save(self.d, st)
        self.app.action('worker_start', dict(worker=w['id'], ore=27, hours=1))
        with self.assertRaisesRegex(ValueError, 'No haul is ready'):
            self.run_action('worker_collect', {}, FakeGame(self.d))
        st = W.load(self.d); st['workers'][0]['trip']['started_at'] = '2026-01-01T00:00:00Z'; W.save(self.d, st)
        self.assertEqual(self.app.workers_view()['crew'][0]['trip']['ready'], True)
        game = FakeGame(self.d)
        self.run_action('worker_collect', {}, game)
        st = W.load(self.d); worker = st['workers'][0]
        self.assertIsNone(worker['trip']); self.assertEqual(worker['stats']['trips'], 1); self.assertEqual(worker['stats']['jewels'], 3)
        delivery = worker['history'][0]['delivery_id']
        self.assertTrue(any(c.startswith('afk worker deliver ') for c in game.commands))
        self.assertEqual(self.app.cli.call_args.args[0], 'ingest')
        self.assertTrue(self.app.cli.call_args.args[3].startswith('AFK · Workers · Brom · '))
        self.assertEqual(afk.read_json(self.d / 'sessions' / f'{delivery}.result.json')['ingest'], 'done')

    def test_a_failed_delivery_keeps_the_haul_for_another_try(self):
        st = W.empty(); w = W.new_worker(st, 'Brom'); W.save(self.d, st)
        self.app.action('worker_start', dict(worker=w['id'], ore=27, hours=2))
        st = W.load(self.d); st['workers'][0]['trip']['started_at'] = '2026-01-01T00:00:00Z'; W.save(self.d, st)
        with self.assertRaisesRegex(ValueError, 'stopped part way'):
            self.run_action('worker_collect', dict(worker=w['id']), FakeGame(self.d, deliver_ok=False))
        trip = W.load(self.d)['workers'][0]['trip']
        self.assertTrue(trip['delivery'], 'the planned haul is remembered, never re-rolled')
        with self.assertRaisesRegex(ValueError, 'Close the haul as partial'):
            self.run_action('worker_collect', dict(worker=w['id']), FakeGame(self.d))   # never made twice
        self.assertEqual(W.load(self.d)['workers'][0]['trip']['delivery'], trip['delivery'])
        plan = json.loads(Path(trip['delivery']['plan']).read_text(encoding='utf-8'))
        ore = plan['items'][0]['amount']
        record = dict(expedition_id=plan['delivery_id'], seq=1, kind='item', item=dict(itemType=14, itemDefinitionStruct=dict(b=27, o=ore // 2)))
        (self.d / 'spool' / f"{plan['delivery_id']}.ndjson").write_text(json.dumps(record) + '\n', encoding='utf-8')
        self.app.action('worker_settle_partial', dict(worker=w['id']))
        worker = W.load(self.d)['workers'][0]
        self.assertIsNone(worker['trip']); self.assertEqual(worker['history'][0]['xp'], int(plan['xp'] * (ore // 2) / ore))
        result = afk.read_json(self.d / 'sessions' / f"{plan['delivery_id']}.result.json")
        self.assertEqual((result['state'], result['created']), ('partial', {'14:27': ore // 2}))

    def test_a_missing_result_is_safe_to_retry(self):
        st = W.empty(); w = W.new_worker(st, 'Brom'); W.save(self.d, st)
        self.app.action('worker_start', dict(worker=w['id'], ore=27, hours=2))
        st = W.load(self.d); st['workers'][0]['trip']['started_at'] = '2026-01-01T00:00:00Z'; W.save(self.d, st)
        class Silent(FakeGame):
            def send(self, line, timeout=30): self.commands.append(line); return None
        with self.assertRaisesRegex(ValueError, 'collect again; nothing was made'):
            self.run_action('worker_collect', dict(worker=w['id']), Silent(self.d))
        self.run_action('worker_collect', dict(worker=w['id']), FakeGame(self.d))
        self.assertIsNone(W.load(self.d)['workers'][0]['trip'])

    def test_skills_names_and_notifications_need_no_game(self):
        st = W.empty(); w = W.new_worker(st); w['xp'] = 500; w['level'] = W.level_for(500)[0]; W.save(self.d, st)
        with patch.object(afk, 'Ipc', side_effect=AssertionError('local actions never talk to the game')):
            self.app.action('worker_learn', dict(worker=w['id'], skill='swift_pick'))
            self.app.action('worker_rename', dict(worker=w['id'], name="Dulga's Pick"))
            self.app.action('worker_start', dict(worker=w['id'], ore=27, hours=2))
        view = self.app.workers_overview()
        self.assertEqual(view['workers'][0]['name'], "Dulga's Pick"); self.assertEqual(view['workers'][0]['skills'], dict(swift_pick=1))
        self.assertEqual(len(view['tree']), len(W.TREE))
        entries = self.app.worker_entries()
        self.assertEqual(entries[0]['key'], 'worker-' + w['id']); self.assertIn('haul ready', entries[0]['title'])

    def test_a_claim_collects_finished_trips_while_the_game_is_open(self):
        st = W.empty(); w = W.new_worker(st, 'Brom'); W.save(self.d, st)
        self.app.action('worker_start', dict(worker=w['id'], ore=27, hours=1))
        st = W.load(self.d); st['workers'][0]['trip']['started_at'] = '2026-01-01T00:00:00Z'; W.save(self.d, st)
        armed = dict(expedition_id='farm_x', plan=str(self.d / 'plans' / 'farm_x.json'))
        afk.write_json(Path(armed['plan']), dict(character=HERO))
        with patch.object(afk, 'Ipc', FakeGame(self.d)), patch.object(panel, 'characters', return_value=[]):
            self.app.claim(armed, 'normal')
        self.assertIsNone(W.load(self.d)['workers'][0]['trip'])


if __name__ == '__main__':
    unittest.main()
