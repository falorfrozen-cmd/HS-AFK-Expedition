"""Adventurers and goblin hunters (0.8): packet pools by region, keys, chests and goblins,
the replay plan, collecting through the panel and the worker-replay command.
Packets are synthetic files shaped like the plugin's; nothing contacts the game.
"""
import json, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, camp, panel, worker_loot as L, workers as W

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
HERO = dict(identity_version=2, slot=1, name='Suh', **{'class': 8})
ROOM = 'Act_01_04'


def packet(h, obj, room=ROOM, first=None, build='B', protected=True, key=''):
    return dict(packet_hash=h * 64, self_object=obj, room=room, game_build_id=build, monster_key=key, rank=None if not key else 3,
                args=[first, 0, 1.0] if first is not None else [5, 0], protected=dict(dSlots=3) if protected else None)


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)
        paths = {k: self.d / v for k, v in dict(DATA='', PACKETS='packets', SESSIONS='sessions', SPOOL='spool', PROFILES='profiles', PLANS='plans',
                                                  STATE='state.json', CONFIG='config.json').items()}
        for p in list(paths.values())[1:6]: p.mkdir(parents=True, exist_ok=True)
        self.patch = patch.dict(afk.__dict__, paths); self.patch.start(); self.addCleanup(self.patch.stop)
        L._POOL_CACHE.clear()
        afk.write_json(self.d / 'build.json', dict(game_build='B'))
        rows = [packet('a', 'Chest_Drop_obj', first=2), packet('b', 'Chest_Drop_obj', first=2), packet('c', 'Chest_Drop_obj', first=3),
                packet('e', 'Chest_Drop_obj', first=4), packet('f', 'Chest_Drop_obj', first=3, build='OLD'),
                packet('g', 'Chest_Drop_obj', first=2, protected=False), packet('h', 'Chest_Drop_obj', room='Act_03_03', first=2),
                packet('t', 'Goblin_Treasure_obj', key='treasure_goblin'), packet('r', 'Goblin_Rune_obj', key='goblinRune'),
                packet('s', 'Goblin_Shadow_obj', room='Act_03_03', key='goblinShadow'), packet('x', 'Enemy_Parent_obj', key='e_orc')]
        for r in rows:
            afk.write_json(afk.PACKETS / (r['packet_hash'] + '.json'), r)

    def worker(self, st, kind, level=1, **skills):
        w = W.new_worker(st, kind.replace('_', ' ').title()[:20], worker_type=kind, at=T0)
        w['xp'] = sum(W.xp_to_next(l) for l in range(1, level)); w['level'] = level; w['skills'] = dict(skills)
        return w


class PoolTests(Folder):
    def test_pools_sort_the_running_builds_chests_by_tier_and_goblins_by_kind(self):
        pool = L.pools()
        self.assertEqual(pool['chests'][ROOM], dict(wooden=['a' * 64, 'b' * 64], golden=['c' * 64], crystal=['e' * 64]),
                         'an old-build chest and one without protected values are left out')
        self.assertEqual(pool['goblins'][ROOM], dict(treasure=['t' * 64], rune=['r' * 64]))
        self.assertEqual(L.regions('adventurer', pool), {ROOM: dict(wooden=2, golden=1, crystal=1), 'Act_03_03': dict(wooden=1)})
        self.assertEqual(L.regions('goblin_hunter', pool), {ROOM: dict(treasure=1, rune=1), 'Act_03_03': dict(shadow=1)})
        self.assertIs(L.pools(), pool, 'cached until the packets change')


class AdventurerTests(Folder):
    def test_keys_go_along_and_come_back_when_a_trip_is_cancelled(self):
        st = W.empty(T0); st['camp']['keys'] = {'0': 4, '1': 1, '14': 2}
        w = self.worker(st, 'adventurer')
        with self.assertRaisesRegex(ValueError, 'Choose a region where chests were recorded'):
            W.start_trip(st, w['id'], 'Act_09_05', 2, at=T0)
        trip = W.start_trip(st, w['id'], ROOM, 2, at=T0, seed=3)
        self.assertEqual(trip['keys'], {'0': 4, '1': 1}); self.assertEqual(st['camp']['keys'], {'14': 2}, 'dungeon keys stay on the rack')
        self.assertEqual(W.trip_view(w, T0)['target_name'], ROOM)
        W.cancel_trip(st, w['id'])
        self.assertEqual(st['camp']['keys'], {'14': 2, '0': 4, '1': 1})

    def test_locked_chests_need_keys_and_levels_open_better_chests(self):
        st = W.empty(T0)
        low = self.worker(st, 'adventurer', level=1)
        trip = dict(region=ROOM, work_hours=40.0, real_hours=40.0, started_at='2026-09-24T12:00:00Z', seed=9, keys={}, build='B', mods=dict(W.NEUTRAL_MODS))
        h = L.haul(low, trip, 40.0)
        self.assertEqual((h['opened']['golden'], h['opened']['crystal']), (0, 0), 'level 1 opens wooden chests only')
        self.assertEqual(set(h['replays']), {'a' * 64, 'b' * 64})
        high = self.worker(st, 'adventurer', level=10)
        locked = L.haul(high, trip, 40.0)
        self.assertGreater(locked['locked'], 0); self.assertEqual(locked['opened']['golden'] + locked['opened']['crystal'], 0)
        keyed = L.haul(high, dict(trip, keys={'0': 50, '1': 50}), 40.0)
        self.assertGreater(keyed['opened']['golden'], 0); self.assertGreater(keyed['opened']['crystal'], 0)
        self.assertEqual(keyed['keys_used'], {'0': keyed['opened']['golden'], '1': keyed['opened']['crystal']})
        self.assertEqual(sum(int(v) for v in keyed['keys_left'].values()) + sum(keyed['keys_used'].values()), 100)
        self.assertEqual(L.haul(high, dict(trip, keys={'0': 50, '1': 50}), 40.0), keyed, 'the trip seed decides the haul')
        self.assertEqual(keyed['resources']['spoils'], 25 * sum(keyed['opened'].values()))
        picker = self.worker(st, 'adventurer', level=10, lockpicking=5)
        self.assertGreater(L.haul(picker, trip, 40.0)['picked'], 0, 'Lockpicking opens some locked chests without a key')

    def test_a_haul_becomes_a_replay_plan_without_experience(self):
        st = W.empty(T0); st['camp']['keys'] = {'0': 3}
        w = self.worker(st, 'adventurer', level=5)
        trip = W.start_trip(st, w['id'], ROOM, 3, at=T0, seed=21)
        plan = L.delivery_plan(w, trip, 3.0, HERO, 'AFK · Workers · Kara · 2026-09-24', 'convert', 'fast', at=T0)
        self.assertTrue(plan['expedition_id'].startswith('worker_')); self.assertIs(plan['exp'], False); self.assertEqual(plan['gold'], 'pickup')
        self.assertEqual((plan['zones'][0]['room'], plan['game_build'], plan['character'], plan['filtered_items']), (ROOM, 'B', HERO, 'convert'))
        self.assertTrue(all(p['kind'] == 'break' and p['exp'] == 0.0 and p['room'] == ROOM for p in plan['packets']))
        self.assertEqual(sum(p['count'] for p in plan['packets']), plan['preview']['calls'])
        out = L.apply(st, w, plan, dict(calls_done=plan['preview']['calls'], items=12, gold=300), at=T0)
        self.assertIsNone(w['trip']); self.assertEqual(st['camp']['resources']['spoils'], min(plan['haul']['resources']['spoils'], 1000))
        back = sum(int(v) for v in plan['haul']['keys_left'].values()) + sum(int(v) for v in plan['haul']['keys_found'].values())
        self.assertEqual(sum(st['camp']['keys'].values()), back)
        self.assertEqual(out['entry']['region'], ROOM)


class GoblinTests(Folder):
    def test_hunters_catch_the_goblins_their_level_allows(self):
        st = W.empty(T0)
        young, old = self.worker(st, 'goblin_hunter', level=1), self.worker(st, 'goblin_hunter', level=10, net_master=5)
        trip = dict(region=ROOM, work_hours=30.0, real_hours=30.0, started_at='2026-09-24T12:00:00Z', seed=4, build='B', mods=dict(W.NEUTRAL_MODS))
        a = L.haul(young, trip, 30.0)
        self.assertEqual(set(a['caught']), {'treasure'}, 'rune goblins open at level 6'); self.assertGreater(a['fled'], 0)
        b = L.haul(old, trip, 30.0)
        self.assertIn('rune', b['caught'])
        self.assertGreater(sum(b['caught'].values()) / b['met'], sum(a['caught'].values()) / a['met'], 'Net Master catches more')
        self.assertEqual(b['xp'], 25 * b['caught'].get('treasure', 0) + 35 * b['caught'].get('rune', 0))
        plan = L.delivery_plan(old, dict(trip, delivery=None), 30.0, HERO, 'label', 'keep', at=T0)
        self.assertTrue(all(p['kind'] == 'kill' and p['exp'] == 0.0 for p in plan['packets']), "the hero gets no experience for the hunter's goblins")
        with self.assertRaisesRegex(ValueError, 'cannot open or catch anything recorded there yet'):
            W.start_trip(st, young['id'], 'Act_03_03', 1, at=T0)


class PanelLootTests(Folder):
    def setUp(self):
        super().setUp()
        for sub in ('models',): (self.d / sub).mkdir(parents=True, exist_ok=True)
        afk.write_json(self.d / 'config.json', dict(game_bin=str(self.d))); (self.d / 'Hero_Siege.exe').write_bytes(b'x')
        self.app = panel.Panel(self.d); self.app.job = dict(output='')
        self.live = dict(character=HERO, room='Town_01_rm', replay_running=False, online=False)
        for p in [patch.object(self.app, 'fresh', side_effect=lambda: dict(self.live)), patch.object(self.app, 'sync_notification'),
                  patch.object(panel.notify, 'sync_all', side_effect=AssertionError('Task Scheduler'))]:
            p.start(); self.addCleanup(p.stop)

    def test_a_haul_is_collected_in_its_region_and_never_twice(self):
        st = W.empty(T0); st['camp']['keys'] = {'0': 5}
        w = self.worker(st, 'adventurer', level=5); W.save(self.d, st)
        self.app.action('worker_start', dict(worker=w['id'], region=ROOM, hours=2))
        st = W.load(self.d); st['workers'][0]['trip']['started_at'] = '2026-01-01T00:00:00Z'; W.save(self.d, st)
        replays = []

        def fake_cli(*args):
            replays.append(args)
            plan = afk.read_json(Path(args[1]))
            afk.write_json(self.d / 'sessions' / f"{plan['expedition_id']}.result.json", dict(state='done', rewards_saved=True,
                           calls_done=plan['preview']['calls'], items=9, gold=120))
        with patch.object(self.app, 'cli', side_effect=fake_cli):
            with self.assertRaisesRegex(ValueError, f'Load any offline hero in .*{ROOM}'):
                self.app.action('worker_collect', dict(worker=w['id']))
            self.assertEqual(replays, [], 'nothing replays outside the region')
            self.live['room'] = ROOM
            self.app.action('worker_collect', dict(worker=w['id']))
        st = W.load(self.d); kara = st['workers'][0]
        self.assertIsNone(kara['trip']); self.assertEqual(kara['stats']['trips'], 1)
        self.assertEqual([a[0] for a in replays], ['worker-replay'])
        self.assertGreater(st['camp']['resources']['spoils'], 0)
        self.assertIn('spoils for the camp', self.app.job['output'])

    def test_the_command_refuses_other_plans_and_never_replays_a_delivered_haul(self):
        bad = self.d / 'plans' / 'x.json'; afk.write_json(bad, dict(expedition_id='farm_1', exp=True))
        with self.assertRaisesRegex(SystemExit, 'not a worker haul plan'):
            afk.cmd_worker_replay(SimpleNamespace(plan=str(bad), no_ingest=False))
        good = self.d / 'plans' / 'worker_1.json'; afk.write_json(good, dict(expedition_id='worker_1', exp=False, packets=[]))
        afk.write_json(self.d / 'sessions' / 'worker_1.result.json', dict(rewards_saved=True))
        with patch.object(afk, 'run_plan', side_effect=AssertionError('replayed twice')):
            afk.cmd_worker_replay(SimpleNamespace(plan=str(good), no_ingest=False))


if __name__ == '__main__':
    unittest.main()
