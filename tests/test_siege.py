"""Siege (0.7.0): waves, gate, special waves, claims and records.

Profiles are synthetic but shaped like calibrations; nothing contacts the game.
"""
import json, math, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, panel, reward_modifiers, siege

HERO = dict(identity_version=2, slot=1, name='Suh', **{'class': 8})


def packet(h, rank, count, key='e_orc_warrior_3', kind='kill', exp=100.0):
    return dict(hash=h * 64 if len(h) == 1 else h, kind=kind, rank=rank, count=count, weight=count, monster_key=key, exp=exp, native_exp=exp)


def profile(pace=60.0, windows=None, packets=None, room='Act_01_01'):
    packets = packets or [packet('a', 3, 400), packet('b', 4, 100), packet('c', 2, 50, key='e_treasure_goblin_3'),
                          packet('d', 0, 30, key='', kind='break', exp=0.0), packet('e', 9, 1, key='e_boss_act1')]
    return dict(room=room, character=HERO, farm_context=dict(schema=1, hash='a' * 64), rate_basis='farm-clock', forgepact={},
                coverage=1, basis_seconds=600, kills=551, breaks=30, kills_per_min=pace, breaks_per_min=3.0, game_build='B',
                reward_baseline=dict(schema=1, complete=True, magic_find=1000), packets=packets,
                quality=dict(window_rates=windows if windows is not None else [55, 60, 70, 50, 65, 60, 58, 62, 64, 56]))


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)
        paths = {k: self.d / v for k, v in dict(DATA='', PACKETS='packets', SESSIONS='sessions', SPOOL='spool', PROFILES='profiles', PLANS='plans',
                                                  STATE='state.json', CONFIG='config.json').items()}
        for p in list(paths.values())[1:6]: p.mkdir(parents=True, exist_ok=True)
        self.patch = patch.dict(afk.__dict__, paths); self.patch.start(); self.addCleanup(self.patch.stop)
        self.mods = reward_modifiers.normalize(dict(magic_find=2.0))


class WaveTests(Folder):
    def test_demand_grows_by_level_and_wave(self):
        self.assertAlmostEqual(siege.demand(1, 1), 10.0)
        self.assertAlmostEqual(siege.demand(2, 1), 11.5)
        self.assertAlmostEqual(siege.demand(1, 11), 10 * 1.03 ** 10)

    def test_pace_factors_come_from_the_calibration_windows(self):
        factors = siege.pace_factors(profile(windows=[50, 100, 150]))
        self.assertEqual(factors, [0.5, 1.0, 1.5])
        self.assertIsNone(siege.pace_factors(profile(windows=[10, 20])), 'too few windows use the fixed spread')
        self.assertIsNone(siege.pace_factors(profile(windows=[0, 0, 0])))

    def test_the_same_seed_gives_the_same_siege_and_a_stronger_hero_lasts_longer(self):
        a = siege.timeline(60, 3, [1.0], 5, 96, 42, {})
        self.assertEqual(a, siege.timeline(60, 3, [1.0], 5, 96, 42, {}))
        weak, _ = siege.timeline(30, 0, None, 5, 96, 1, {}); strong, _ = siege.timeline(90, 0, None, 5, 96, 1, {})
        self.assertGreater(len(strong), len(weak))
        waves, fell = siege.timeline(20, 0, None, 1, 96, 3, {})
        self.assertEqual(fell, len(waves)); self.assertLessEqual(waves[-1]['hp'], 0 + 1e-9)
        self.assertTrue(all(abs(w['kills'] - w['rate'] * 5) < 1 for w in waves), 'a wave pays the kills made at the measured pace')

    def test_a_comfortable_wave_repairs_the_gate(self):
        waves, _ = siege.timeline(12, 0, None, 1, 3, 5, {})     # pace near the demand: some damage
        hp = waves[-1]['hp']
        more, _ = siege.timeline(1000, 0, None, 1, 3, 5, {})
        self.assertEqual(more[-1]['hp'], 100.0)
        self.assertLessEqual(hp, 100.0)

    def test_special_waves_follow_the_schedule_and_what_the_calibration_met(self):
        full = dict(elite=True, goblin=True, boss=True)
        self.assertEqual([siege.wave_kind(k, full) for k in (4, 5, 10, 25, 50)], ['normal', 'elite', 'treasure', 'boss', 'boss'])
        self.assertEqual(siege.wave_kind(25, dict(elite=True)), 'elite', 'no verified boss: the 25th wave is an elite wave')
        self.assertEqual(siege.wave_kind(10, dict(elite=True)), 'elite')
        self.assertEqual(siege.wave_kind(5, {}), 'normal')

    def test_chests_never_join_a_siege_and_every_loot_goblin_counts(self):
        p = profile(packets=[packet('a', 3, 400), packet('d', 0, 30, key='', kind='break', exp=0.0),
                             dict(packet('f', 0, 10, key='', kind='break', exp=0.0), object='Abyss_Chest_obj'),
                             packet('g', 2, 5, key='e_goblinOrb_1'), dict(packet('h', 2, 5, key=''), object='Goblin_Ore_obj')])
        g = siege.groups(p)
        self.assertEqual(g['breaks'], ['d' * 64], 'a chest opening is not a breakable')
        self.assertEqual(sorted(g['goblin']), ['g' * 64, 'h' * 64], 'orb and ore goblins are loot goblins too')
        self.assertAlmostEqual(siege.ordinary_breaks_per_min(p), 3.0 * 30 / 40)
        plan = siege.build_plan(p, 5, 1.0, 's', self.mods, seed=3)
        self.assertNotIn('f' * 64, {q['hash'] for q in plan['packets']})

    def test_groups_keep_unverified_bosses_out(self):
        g = siege.groups(profile())
        self.assertEqual(g['boss'], []); self.assertNotIn('e' * 64, g['normal'])
        self.assertEqual(g['elite'], ['b' * 64]); self.assertEqual(g['goblin'], ['c' * 64]); self.assertEqual(g['breaks'], ['d' * 64])
        self.assertEqual(siege.groups(profile(), specials=['e' * 64])['boss'], ['e' * 64])


class PlanTests(Folder):
    def plan(self, level=5, hours=2.0, pace=60.0, seed=7, **kw):
        return siege.build_plan(profile(pace=pace), level, hours, 'siege_x', self.mods, seed=seed, **kw)

    def test_a_siege_plan_replays_only_calibrated_packets_with_native_xp(self):
        plan = self.plan()
        self.assertEqual(plan['mode'], 'siege'); self.assertEqual(plan['siege']['level'], 5)
        hashes = {p['hash'] for p in plan['packets']}
        self.assertNotIn('e' * 64, hashes, 'an unverified boss never replays')
        self.assertTrue(all(p['exp'] == p['native_exp'] for p in plan['packets']))
        waves = plan['siege']['waves']
        self.assertEqual(plan['preview']['kills'], sum(w['kills'] for w in waves))
        self.assertEqual(plan['preview']['breaks'], sum(w['breaks'] for w in waves))
        self.assertAlmostEqual(plan['reward_modifiers']['magic_find'], 2.0 * 1.10)
        self.assertAlmostEqual(plan['effective_magic_find'], 1000 * 2.2)
        capped = siege.build_plan(profile(), 50, 1.0, 'y', reward_modifiers.normalize(dict(magic_find=90)), seed=1)
        self.assertEqual(capped['reward_modifiers']['magic_find'], 100.0, 'the bonus never passes the x100 limit')

    def test_levels_and_durations_are_checked(self):
        for level, hours in ((0, 1), (51, 1), ('5', 1), (5, 0.1), (5, 9), (5, float('nan'))):
            with self.subTest(level=level, hours=hours), self.assertRaises(ValueError):
                siege.build_plan(profile(), level, hours, 'z', self.mods, seed=1)

    def test_a_claim_delivers_complete_waves_only_and_never_rerolls(self):
        plan = self.plan(pace=500.0)          # holds the whole time
        early = siege.claim_plan(plan, 0.40, 'siege_x_claim')     # 24 min: 4 complete waves
        self.assertEqual(early['siege_claim']['waves_fought'], 4)
        self.assertEqual(early['preview']['kills'], sum(w['kills'] for w in plan['siege']['waves'][:4]))
        self.assertAlmostEqual(early['scale'], (4 * 5 / 60) / 2.0)
        self.assertEqual(siege.claim_plan(plan, 0.40, 'again')['packets'], early['packets'], 'the same time delivers the same waves')
        full = siege.claim_plan(plan, 99, 'siege_x_claim')
        self.assertEqual(full['siege_claim']['waves_fought'], 24); self.assertFalse(full['siege_claim']['fell'])
        self.assertEqual((full['siege_claim']['elite_waves'], full['siege_claim']['treasure_waves']), (2, 2), 'waves 10 and 20 are treasure waves')
        weak = self.plan(pace=15.0, level=3)
        fallen = siege.claim_plan(weak, 8, 'w')
        self.assertTrue(fallen['siege_claim']['fell']); self.assertEqual(fallen['siege_claim']['waves_fought'], weak['siege']['fell_at'])
        self.assertAlmostEqual(siege.report_hours(weak), weak['siege']['fell_at'] * 5 / 60)
        self.assertEqual(siege.claim_plan(plan, 0.05, 'n')['packets'], [], 'no complete wave yet: nothing to credit')

    def test_treasure_waves_bring_goblins_and_elite_waves_elites(self):
        plan = self.plan(pace=500.0, level=10)
        s = plan['siege']
        treasure = [w for w in s['waves'] if w['kind'] == 'treasure']
        self.assertTrue(treasure and all(w['goblins'] == 3 for w in treasure))
        counts = {p['hash']: p['count'] for p in plan['packets']}
        self.assertGreaterEqual(counts['c' * 64], sum(w['goblins'] for w in treasure), 'goblins also come at their measured rate in other waves')
        elites = sum(w['kills'] for w in s['waves'] if w['kind'] == 'elite')
        self.assertGreaterEqual(counts['b' * 64], elites)

    def test_live_view_shows_the_past_only(self):
        plan = self.plan(pace=15.0, level=3)
        fell = plan['siege']['fell_at']
        before = siege.live_view(plan, (fell - 1) * 5 / 60)
        self.assertFalse(before['fell']); self.assertIsNone(before['fell_at']); self.assertEqual(before['wave'], fell)
        self.assertNotIn('waves', before); self.assertLessEqual(len(before['last']), 5)
        after = siege.live_view(plan, 99)
        self.assertTrue(after['fell'] and after['over']); self.assertEqual(after['fell_at'], fell); self.assertIsNone(after['wave'])
        start = siege.live_view(self.plan(pace=500.0), 0)
        self.assertEqual((start['waves_done'], start['wave'], start['hp']), (0, 1, 100.0))
        self.assertEqual(start['next_special'], dict(wave=5, kind='elite'))

    def test_forecast_and_suggestion_follow_the_pace(self):
        easy, hard = siege.forecast(profile(), 1, 4), siege.forecast(profile(), 20, 4)
        self.assertGreater(easy['waves_median'], hard['waves_median'])
        self.assertLessEqual(hard['waves_low'], hard['waves_median']); self.assertLessEqual(hard['waves_median'], hard['waves_high'])
        self.assertGreater(siege.suggest_level(profile(pace=300.0)), siege.suggest_level(profile(pace=40.0)))
        self.assertTrue(0 <= hard['fall_chance'] <= 1)

    def test_the_suggested_level_keeps_the_gate_standing(self):
        # MEASURED 2026-09-24 (Suh, Act 2-5, 778.68 kills/min, a 90 s calibration,
        # 30 minutes): the next level up still "lasted" 6 of 6 waves, but its gate
        # broke on the last wave in every run - it must not be the suggestion.
        p = profile(pace=778.68, windows=[])
        level = siege.suggest_level(p, 0.5)
        chosen, above = siege.forecast(p, level, 0.5, runs=40), siege.forecast(p, level + 1, 0.5, runs=40)
        self.assertLessEqual(chosen['fall_chance'], siege.SUGGEST_MAX_FALL); self.assertEqual(chosen['waves_median'], 6)
        self.assertEqual(above['waves_median'], 6, 'the level above lasts too...')
        self.assertGreater(above['fall_chance'], siege.SUGGEST_MAX_FALL, '...but its gate falls')

    def test_a_siege_lasts_whole_waves_and_always_reaches_its_end(self):
        odd = self.plan(pace=500.0, hours=0.3)                     # 18 minutes: 3 whole waves
        self.assertEqual(len(odd['siege']['waves']), 3); self.assertAlmostEqual(odd['hours'], 0.25)
        self.assertEqual(siege.claim_plan(odd, odd['hours'], 'c')['siege_claim']['waves_fought'], 3)
        self.assertTrue(siege.live_view(odd, odd['hours'])['over'], 'the last planned wave is reached')
        long = self.plan(pace=500.0, hours=49 / 12)                 # 245 minutes; 4.0833 h is below 49/12 in binary
        self.assertEqual(len(long['siege']['waves']), 49)
        self.assertEqual(siege.claim_plan(long, long['hours'], 'c')['siege_claim']['waves_fought'], 49)
        self.assertTrue(siege.live_view(long, long['hours'])['over'])
        self.assertEqual([siege.wave_count(h) for h in (0.25, 0.3, 0.5, 49 / 12, 8)], [3, 3, 6, 49, 96])

    def test_records_keep_the_best_wave_per_hero_region_and_level(self):
        self.assertTrue(siege.record_claim(self.d, 'h', 'Act_01_01', dict(level=5, waves_fought=12)))
        self.assertFalse(siege.record_claim(self.d, 'h', 'Act_01_01', dict(level=5, waves_fought=9)))
        self.assertEqual(siege.best(self.d, 'h', 'Act_01_01', 5), 12); self.assertEqual(siege.best(self.d, 'h', 'Act_01_01', 6), 0)


class SpecialPacketTests(Folder):
    def test_unverified_specials_are_left_out_of_every_plan(self):
        p = profile(); afk.write_json(self.d / 'profiles' / 'Act_01_01.json', p)
        plan = afk.make_plan(1.0, [('Act_01_01', 1)], 'x', 40, 'pickup', profile_overrides={'Act_01_01': p})
        self.assertNotIn('e' * 64, {q['hash'] for q in plan['packets']})
        self.assertEqual(plan['preview']['kills'], round(550 * 3600 / 600))
        afk.write_json(self.d / 'special-packets.json', dict(schema=1, packets={'e' * 64: dict(room='Act_01_01', build='B')}))
        plan = afk.make_plan(1.0, [('Act_01_01', 1)], 'x', 40, 'pickup', profile_overrides={'Act_01_01': p})
        self.assertIn('e' * 64, {q['hash'] for q in plan['packets']}, 'a verified boss replays at its measured rate')

    def test_a_calibration_with_a_boss_kill_stays_usable(self):
        p = dict(profile(), coverage=1, basis_seconds=240, kills=50)
        self.assertEqual(panel.profile_problems(p, 'B'), [])


class PanelSiegeTests(Folder):
    def setUp(self):
        super().setUp()
        afk.write_json(self.d / 'profiles' / 'a.json', profile())
        self.chars = patch.object(panel, 'characters', return_value=[dict(HERO, class_name='Samurai', level=90)])
        self.chars.start(); self.addCleanup(self.chars.stop)
        self.app = panel.Panel(self.d); self.app.job = dict(output='')

    def start(self, **extra):
        def cli(*args):
            afk.cmd_start(SimpleNamespace(plan=args[1]))
        with patch.object(self.app, 'cli', side_effect=cli):
            self.app.action('start', dict(slot=1, profile='a', hours=2, mode='siege', **extra))

    def test_starting_a_siege_from_the_panel(self):
        with self.assertRaisesRegex(ValueError, 'Siege level'): self.start(siege_level=99)
        self.start(siege_level=4)
        st = afk.load_state(); armed = afk.armed_list(st)[0]
        self.assertEqual(armed['mode'], 'siege')
        plan = afk.read_json(Path(armed['plan']))
        self.assertTrue(plan['label'].startswith('Siege L4 · Suh · '))
        row = self.app.snapshot(dict(slot=1))['expeditions'][0]
        self.assertEqual(row['mode'], 'siege'); self.assertEqual(row['siege']['level'], 4); self.assertEqual(row['siege']['waves_done'], 0)
        entry = self.app.ready_entry(armed, plan)
        self.assertEqual(entry['at'], afk.parse_iso(armed['started_at']) + timedelta(hours=siege.report_hours(plan)))
        self.assertIn('siege', entry['title'])
        with self.assertRaisesRegex(ValueError, 'already active'): self.start(siege_level=4)

    def test_forecast_and_specials_views(self):
        result = self.app.siege_forecast('profile=a&level=5&hours=2')
        self.assertEqual(result['level'], 5); self.assertIn('suggested_level', result); self.assertEqual(result['best_waves'], 0)
        with self.assertRaisesRegex(ValueError, 'usable'): self.app.siege_forecast('profile=missing&level=5&hours=2')
        with self.assertRaisesRegex(ValueError, 'Invalid'): self.app.siege_forecast('profile=a&level=0&hours=2')
        specials = self.app.specials_view()['specials']
        self.assertEqual([(s['monster_key'], s['verified'], s['kills']) for s in specials], [('e_boss_act1', False, 1)])
        self.assertEqual(self.app.profiles[0]['special_kills'], 1)


if __name__ == '__main__':
    unittest.main()
