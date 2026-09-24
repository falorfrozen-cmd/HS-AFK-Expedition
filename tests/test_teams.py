"""Team trips (0.8): size, synergies, auras and Lone Wolves, all-or-nothing starts,
and the panel's team_start. Synthetic packets; nothing contacts the game or Windows.
"""
import sys, unittest
from pathlib import Path
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import afk, camp, panel, teams, workers as W
from test_worker_loot import Folder, ROOM, HERO, T0


class TeamTests(Folder):
    def crew(self, *kinds, **camp_levels):
        st = W.empty(T0); st['camp']['buildings'].update(camp_levels)
        out = []
        for i, kind in enumerate(kinds):
            w = W.new_worker(st, f'Mate {i}', worker_type=kind, at=T0)
            w['level'] = 10
            out.append(w)
        return st, out

    def test_synergies_follow_the_crew(self):
        names = lambda types: [s['key'] for s in teams.active(types)]
        self.assertEqual(names(['miner', 'goblin_hunter']), ['goblin_patrol'])
        self.assertEqual(names(['adventurer', 'miner', 'goblin_hunter']), ['goblin_patrol', 'treasure_trail', 'deep_vein'])
        self.assertIn('full_caravan', names(['miner', 'adventurer', 'goblin_hunter', 'jeweler']))
        self.assertEqual(names(['miner', 'miner']), [])

    def test_a_team_shares_its_hours_synergy_and_experience(self):
        st, (miner, hunter) = self.crew('miner', 'goblin_hunter')
        team = teams.start(st, [dict(worker=miner['id'], target=27), dict(worker=hunter['id'], target=ROOM)], 2, at=T0)
        self.assertEqual(team['synergies'], ['goblin_patrol'])
        self.assertEqual(miner['trip']['team'], team['id']); self.assertEqual(hunter['trip']['team'], team['id'])
        self.assertAlmostEqual(miner['trip']['mods']['amount'], 1.10); self.assertAlmostEqual(hunter['trip']['mods']['speed'], 1.25)
        self.assertAlmostEqual(miner['trip']['mods']['xp'], 1.10, msg='the team experience bonus')
        self.assertEqual([t['id'] for t in teams.view(st)], [team['id']])
        W.cancel_trip(st, miner['id']); W.cancel_trip(st, hunter['id']); teams.tidy(st)
        self.assertEqual(st['teams'], {}, 'a team is forgotten once everyone is back')

    def test_team_players_help_and_lone_wolves_prefer_to_work_alone(self):
        st, (a, b) = self.crew('miner', 'miner')
        a['traits'] = [dict(id='team_player')]; b['traits'] = [dict(id='lone_wolf')]
        teams.start(st, [dict(worker=a['id'], target=27), dict(worker=b['id'], target=27)], 1, at=T0)
        self.assertAlmostEqual(b['trip']['mods']['speed'], 1 + 0.10 - 0.10, msg="the Team Player's aura, the Lone Wolf's team penalty")
        self.assertAlmostEqual(a['trip']['mods']['speed'], 1.0, msg="a Team Player's own aura is for its teammates")
        W.cancel_trip(st, b['id'])
        solo = W.start_trip(st, b['id'], 27, 1, at=T0)
        self.assertAlmostEqual(solo['mods']['speed'], 1.15, msg='alone, the Lone Wolf works 15% faster')

    def test_a_team_starts_whole_or_not_at_all(self):
        st, (adv, miner, hunter) = self.crew('adventurer', 'miner', 'goblin_hunter')
        st['camp']['keys'] = {'0': 3}
        with self.assertRaisesRegex(ValueError, 'A team is 2 to 2 workers'):
            teams.start(st, [dict(worker=adv['id'], target=ROOM), dict(worker=miner['id'], target=27), dict(worker=hunter['id'], target=ROOM)], 1, at=T0)
        with self.assertRaisesRegex(ValueError, 'Choose an ore'):
            teams.start(st, [dict(worker=adv['id'], target=ROOM), dict(worker=miner['id'], target='iron')], 1, at=T0)
        self.assertIsNone(adv['trip']); self.assertEqual(st['camp']['keys'], {'0': 3}, "the adventurer's keys came back")
        with self.assertRaisesRegex(ValueError, 'only join a team once'):
            teams.start(st, [dict(worker=adv['id'], target=ROOM), dict(worker=adv['id'], target=ROOM)], 1, at=T0)
        st['camp']['buildings']['barracks'] = 5
        team = teams.start(st, [dict(worker=adv['id'], target=ROOM), dict(worker=miner['id'], target=27), dict(worker=hunter['id'], target=ROOM)], 1, at=T0)
        self.assertEqual(team['synergies'], ['goblin_patrol', 'treasure_trail', 'deep_vein'])
        with self.assertRaisesRegex(ValueError, 'already on a trip'):
            teams.start(st, [dict(worker=adv['id'], target=ROOM), dict(worker=miner['id'], target=27)], 1, at=T0)


class PanelTeamTests(Folder):
    def test_the_panel_sends_a_team(self):
        (self.d / 'models').mkdir(exist_ok=True)
        afk.write_json(self.d / 'config.json', dict(game_bin=str(self.d))); (self.d / 'Hero_Siege.exe').write_bytes(b'x')
        app = panel.Panel(self.d); app.job = dict(output='')
        for p in [patch.object(app, 'sync_notification'), patch.object(panel.notify, 'sync_all', side_effect=AssertionError('Task Scheduler'))]:
            p.start(); self.addCleanup(p.stop)
        st = W.empty(T0); m = W.new_worker(st, 'Brom', at=T0); h = W.new_worker(st, 'Hunt', worker_type='goblin_hunter', at=T0); W.save(self.d, st)
        app.action('team_start', dict(members=[dict(worker=m['id'], ore=27), dict(worker=h['id'], region=ROOM)], hours=1))
        st = W.load(self.d)
        self.assertEqual(len(st['teams']), 1); self.assertIn('synergy: Goblin Patrol', app.job['output'])
        overview = W.overview(st)
        self.assertEqual((overview['team_size'], overview['teams'][0]['synergy_names']), (2, ['Goblin Patrol']))


if __name__ == '__main__':
    unittest.main()
