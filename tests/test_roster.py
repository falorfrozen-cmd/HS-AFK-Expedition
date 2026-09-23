"""Hero roster (0.7.0): one armed expedition per hero, several heroes at once.

State files and profiles live in a temporary folder; the game, IPC and Task
Scheduler are never contacted.
"""
import json, sys, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, panel, recovery

A = dict(identity_version=2, slot=1, name='Suh', **{'class': 8})
B = dict(identity_version=2, slot=2, name='Sgham', **{'class': 13})


def profile(hero, room='Act_01_01'):
    return dict(room=room, character=hero, farm_context=dict(schema=1, hash='a' * 64), rate_basis='farm-clock', forgepact={},
                coverage=1, basis_seconds=240, kills=50, breaks=0, kills_per_min=12.5, breaks_per_min=0, game_build='B',
                reward_baseline=dict(schema=1, complete=True, magic_find=1000),
                packets=[dict(kind='kill', rank=1, count=50, weight=1, hash='f' * 64, monster_key='test', exp=10, native_exp=10)])


class RosterStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)
        for name in ('plans',):
            (self.d / name).mkdir()

    def plan(self, ident, hero):
        path = self.d / 'plans' / f'{ident}.json'
        afk.write_json(path, dict(expedition_id=ident, character=hero, zones=[dict(room='Act_01_01')], hours=1.0))
        return path

    def test_a_0_6_state_reads_as_a_one_hero_roster_and_is_saved_as_schema_2(self):
        path = self.plan('old', A)
        afk.write_json(self.d / 'state.json', dict(armed=dict(expedition_id='old', plan=str(path), started_at='2026-09-24T00:00:00Z', hours=1.0),
                                                 last_claim=dict(expedition_id='x_claim')))
        before = (self.d / 'state.json').read_bytes()
        st = afk.load_state(self.d / 'state.json')
        self.assertEqual(list(st['expeditions']), ['old'])
        self.assertEqual(afk.armed_hero(st['expeditions']['old']), '1:8:Suh', 'a 0.6 record finds its hero through the plan')
        self.assertEqual((self.d / 'state.json').read_bytes(), before, 'reading never rewrites the file')
        afk.save_state(st, self.d / 'state.json')
        saved = afk.read_json(self.d / 'state.json')
        self.assertEqual(saved['schema'], 2); self.assertNotIn('armed', saved); self.assertIn('old', saved['expeditions'])

    def test_selection_by_id_by_hero_or_the_only_one(self):
        st = afk.load_state(self.d / 'missing.json')
        with self.assertRaises(SystemExit): afk.select_armed(st)
        st['expeditions']['a'] = dict(expedition_id='a', plan=str(self.plan('a', A)), started_at='2026-09-24T00:00:00Z', hero='1:8:Suh')
        self.assertEqual(afk.select_armed(st)['expedition_id'], 'a')
        st['expeditions']['b'] = dict(expedition_id='b', plan=str(self.plan('b', B)), started_at='2026-09-24T00:01:00Z', hero='2:13:Sgham')
        with self.assertRaisesRegex(SystemExit, 'several expeditions'): afk.select_armed(st)
        self.assertEqual(afk.select_armed(st, 'b')['expedition_id'], 'b')
        self.assertEqual(afk.select_armed(st, stamp=A)['expedition_id'], 'a')
        self.assertEqual([x['expedition_id'] for x in afk.armed_list(st)], ['a', 'b'], 'oldest first')

    def test_settling_frees_only_that_hero_and_remembers_its_claim(self):
        st = afk.load_state(self.d / 'missing.json')
        st['expeditions']['a'] = dict(expedition_id='a', hero='1:8:Suh'); st['expeditions']['b'] = dict(expedition_id='b', hero='2:13:Sgham')
        afk.settle_in_state(st, 'a_claim', dict(expedition_id='a_claim'))
        self.assertEqual(list(st['expeditions']), ['b'])
        self.assertEqual(st['last_claims']['1:8:Suh']['expedition_id'], 'a_claim'); self.assertEqual(st['last_claim']['expedition_id'], 'a_claim')


class RosterCommandTests(unittest.TestCase):
    """The CLI start/cancel commands against a temporary data folder."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)
        paths = {k: self.d / v for k, v in dict(DATA='', PACKETS='packets', SESSIONS='sessions', SPOOL='spool', PROFILES='profiles', PLANS='plans',
                                                  STATE='state.json', CONFIG='config.json').items()}
        for p in list(paths.values())[1:6]: p.mkdir(parents=True, exist_ok=True)
        self.patch = patch.dict(afk.__dict__, paths); self.patch.start(); self.addCleanup(self.patch.stop)

    def arm(self, ident, hero):
        path = self.d / 'plans' / f'{ident}.json'
        afk.write_json(path, dict(expedition_id=ident, character=hero, zones=[dict(room='Act_01_01', minutes=60, kills=1, breaks=0)], hours=1.0,
                                  preview=dict(kills=1, breaks=0, calls=1, exp=0), packets=[]))
        afk.cmd_start(SimpleNamespace(plan=str(path)))

    def test_two_heroes_run_at_once_but_one_hero_never_twice(self):
        self.arm('a', A); self.arm('b', B)
        st = afk.load_state()
        self.assertEqual(sorted(st['expeditions']), ['a', 'b'])
        self.assertEqual({x['hero'] for x in st['expeditions'].values()}, {'1:8:Suh', '2:13:Sgham'})
        before = afk.STATE.read_bytes()
        with self.assertRaisesRegex(SystemExit, "hero's expedition is already active"): self.arm('c', A)
        self.assertEqual(afk.STATE.read_bytes(), before)

    def test_cancel_needs_a_choice_when_several_heroes_run(self):
        self.arm('a', A); self.arm('b', B)
        with self.assertRaisesRegex(SystemExit, 'several expeditions'): afk.cmd_cancel(SimpleNamespace(expedition=None))
        afk.cmd_cancel(SimpleNamespace(expedition='a'))
        st = afk.load_state()
        self.assertEqual(list(st['expeditions']), ['b']); self.assertEqual(st['cancelled']['expedition_id'], 'a')

    def test_recovery_settles_the_claim_of_its_own_expedition(self):
        self.arm('a', A); self.arm('b', B)
        ok = dict(recoverable=True, status='saved', reasons=[])
        afk.write_json(self.d / 'sessions' / 'a_claim.progress.json', dict(state='done'))
        afk.write_json(self.d / 'plans' / 'a_claim.json', dict(hours=1.0, scale=0.5))
        with patch.object(recovery, 'inspect', return_value=ok):
            recovery.settle(self.d, 'a_claim')
            with self.assertRaisesRegex(ValueError, 'not the claim of an armed expedition'):
                recovery.settle(self.d, 'nobody_claim')
        st = afk.load_state()
        self.assertEqual(list(st['expeditions']), ['b'])
        self.assertAlmostEqual(st['last_claims']['1:8:Suh']['credited_hours'], 0.5)


class RosterPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name) / 'afk'; self.d.mkdir()
        paths = {k: self.d / v for k, v in dict(DATA='', PACKETS='packets', SESSIONS='sessions', SPOOL='spool', PROFILES='profiles', PLANS='plans',
                                                  STATE='state.json', CONFIG='config.json').items()}
        self.patch = patch.dict(afk.__dict__, paths); self.patch.start(); self.addCleanup(self.patch.stop)
        afk.write_json(self.d / 'profiles' / 'a.json', profile(A)); afk.write_json(self.d / 'profiles' / 'b.json', profile(B))
        chars = [dict(A, class_name='Samurai', level=90), dict(B, class_name='White Mage', level=80)]
        self.chars = patch.object(panel, 'characters', return_value=chars); self.chars.start(); self.addCleanup(self.chars.stop)
        self.app = panel.Panel(self.d); self.app.job = dict(output='')

    def start(self, slot, profile_id):
        def cli(*args):
            self.assertEqual(args[0], 'start'); afk.cmd_start(SimpleNamespace(plan=args[1]))
        with patch.object(self.app, 'cli', side_effect=cli):
            self.app.action('start', dict(slot=slot, profile=profile_id, hours=.25))

    def test_every_hero_starts_its_own_expedition_from_the_panel(self):
        self.start(1, 'a'); self.start(2, 'b')
        with self.assertRaisesRegex(ValueError, 'already active'): self.start(1, 'a')
        snap = self.app.snapshot()
        self.assertEqual(len(snap['expeditions']), 2)
        self.assertEqual({r['character']['name'] for r in snap['expeditions']}, {'Suh', 'Sgham'})
        self.assertTrue(all(r['progress']['state'] is None and r['recovery']['status'] == 'not_started' for r in snap['expeditions']))

    def test_the_selected_hero_decides_the_focus(self):
        self.start(1, 'a')
        focused = self.app.snapshot(dict(slot=1))
        self.assertEqual(focused['plan']['character']['name'], 'Suh')
        free = self.app.snapshot(dict(slot=2))
        self.assertIsNone(free['armed'], 'a hero without an expedition sees the planner')
        self.assertEqual(len(free['expeditions']), 1, 'the roster still lists every active expedition')
        self.assertEqual(self.app.snapshot()['armed']['expedition_id'], focused['armed']['expedition_id'],
                         'without a selection the newest expedition is shown (0.6 panels)')
        self.assertEqual(panel.focus_query('slot=2&expedition=farm_1'), dict(slot=2, expedition='farm_1'))
        self.assertEqual(panel.focus_query('slot=x&expedition=../x'), {})

    def test_actions_find_the_right_expedition(self):
        self.start(1, 'a'); self.start(2, 'b')
        st = afk.load_state()
        by_hero = {afk.armed_hero(a): a['expedition_id'] for a in afk.armed_list(st)}
        self.assertEqual(self.app.target_armed(dict(slot=2))['expedition_id'], by_hero['2:13:Sgham'])
        self.assertEqual(self.app.target_armed(dict(expedition=by_hero['1:8:Suh']))['expedition_id'], by_hero['1:8:Suh'])
        live = dict(character=A)
        self.assertEqual(self.app.target_armed({}, live)['expedition_id'], by_hero['1:8:Suh'], 'the loaded hero claims its own expedition')
        with self.assertRaisesRegex(ValueError, 'Several heroes'): self.app.target_armed({})
        with self.assertRaisesRegex(ValueError, 'not active'): self.app.target_armed(dict(expedition='nope'))
        with patch.object(self.app, 'cli') as cli:
            self.app.action('cancel', dict(slot=2))
        self.assertEqual(cli.call_args.args, ('cancel', '--expedition', by_hero['2:13:Sgham']))

    def test_farm_again_is_offered_per_hero(self):
        self.start(1, 'a')
        st = afk.load_state(); ident = afk.armed_list(st)[0]['expedition_id']
        afk.settle_in_state(st, ident + '_claim', dict(expedition_id=ident + '_claim')); afk.save_state(st)
        self.app.refresh_profiles()
        repeat = self.app.snapshot(dict(slot=1))['repeat']
        self.assertEqual((repeat['name'], repeat['mode']), ('Suh', 'farm'))
        self.assertIsNone(self.app.snapshot(dict(slot=2))['repeat'], "Sgham has no settled claim to repeat")


if __name__ == '__main__':
    unittest.main()
