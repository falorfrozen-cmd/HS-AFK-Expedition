"""The Camp and worker traits (0.8): buildings, resources, building sites, hot spots, the
Walls' Siege gate, traits and what they change. Nothing contacts the game or Windows.
"""
import json, random, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import afk, camp, siege, traits, workers as W
from test_siege import profile as siege_profile, Folder as SiegeFolder

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def rich(state=None, **levels):
    """A camp with plenty of resources and the given building levels."""
    state = state or W.empty(T0)
    state['camp']['resources'] = dict(stone=40000, spoils=40000, dust=40000)
    state['camp']['buildings'].update(levels)
    return state


class BuildingTests(unittest.TestCase):
    def test_a_new_camp_is_small_and_honest_about_what_it_needs(self):
        c = camp.new(T0)
        self.assertEqual({k: v for k, v in c['buildings'].items() if v}, dict(hq=1, barracks=1, storehouse=1))
        eff = camp.effects(c)
        self.assertEqual((eff['max_workers'], eff['team_size'], eff['sites'], eff['candidates']), (3, 2, 1, 1))
        self.assertEqual(eff['types'], ['miner']); self.assertEqual(eff['gate_hp'], 100.0)
        self.assertEqual(camp.next_build(c, 'forge')['blockers'][0], 'needs Headquarters level 2')
        self.assertIn('needs Headquarters level 2', camp.next_build(c, 'barracks')['blockers'])
        self.assertTrue(any(b.startswith('missing 150 stone') for b in camp.next_build(c, 'tavern')['blockers']))
        with self.assertRaisesRegex(ValueError, 'Unknown building'):
            camp.next_build(c, 'castle')

    def test_building_takes_real_time_and_one_site_until_headquarters_3(self):
        c = rich()['camp']
        plan = camp.next_build(c, 'tavern')
        self.assertEqual((plan['to'], plan['cost']['gold'], plan['hours'], plan['blockers']), (1, 300000, 0.5, []))
        camp.take(c, plan['cost']); site = camp.start(c, 'tavern', 'req', T0)
        self.assertEqual(c['resources']['stone'], 40000 - 150)
        self.assertIn('every building site is busy', camp.next_build(c, 'walls')['blockers'])
        self.assertIn('already being built', camp.next_build(c, 'tavern')['blockers'])
        self.assertEqual(camp.settle(c, T0 + timedelta(minutes=29)), []); self.assertEqual(camp.level(c, 'tavern'), 0)
        self.assertEqual([d['building'] for d in camp.settle(c, T0 + timedelta(minutes=30))], ['tavern'])
        self.assertEqual(camp.level(c, 'tavern'), 1); self.assertEqual(camp.effects(c)['types'], ['miner', 'adventurer', 'goblin_hunter'])
        c['buildings']['hq'] = 3
        self.assertEqual(camp.effects(c)['sites'], 2)
        camp.start(c, 'walls', 'a', T0); self.assertNotIn('every building site is busy', camp.next_build(c, 'forge')['blockers'])
        self.assertEqual(camp.next_build(c, 'walls')['to'], 2, 'the next level counts the one being built')

    def test_resources_stop_at_the_storehouse_cap_and_refunds_return_them(self):
        c = camp.new(T0)
        self.assertEqual(camp.add(c, dict(stone=1500, spoils=10)), dict(stone=1000, spoils=10, dust=0))
        c['buildings']['storehouse'] = 3
        self.assertEqual(camp.add(c, dict(stone=9000))['stone'], 7000)
        with self.assertRaisesRegex(ValueError, 'Not enough camp resources: 490 spoils'):
            camp.take(c, dict(stone=10, spoils=500))
        taken = camp.take(c, dict(stone=500)); camp.give_back(c, taken)
        self.assertEqual(c['resources']['stone'], 8000)
        self.assertEqual(camp.add_keys(c, {0: 60})['0'], 50, 'the key rack holds 50 at Storehouse 3')

    def test_a_stored_camp_is_repaired_not_trusted(self):
        c = camp.normalize(dict(buildings=dict(hq=9, tavern='x', walls=-2), resources=dict(stone=-5, spoils='a'), queue=[dict(building='castle')]))
        self.assertEqual((c['buildings']['hq'], c['buildings']['tavern'], c['buildings']['walls']), (5, 0, 0))
        self.assertEqual(c['resources'], dict(stone=0, spoils=0, dust=0)); self.assertEqual(c['queue'], [])

    def test_hot_spots_are_the_same_all_day_and_grow_with_the_watchtower(self):
        c = camp.new(T0)
        targets = {'miner': ['copper', 'iron', 'gold', 'ruby']}
        self.assertEqual(camp.hotspots(c, targets, T0), [])
        c['buildings']['watchtower'] = 4
        spots = camp.hotspots(c, targets, T0)
        self.assertEqual(len(spots), 3); self.assertEqual(spots, camp.hotspots(c, targets, T0 + timedelta(hours=11)))
        self.assertTrue(all(s['bonus'] == 0.3 for s in spots))
        self.assertEqual(camp.hotspot_bonus(c, targets, 'miner', spots[0]['target'], T0), 0.3)


class TraitTests(unittest.TestCase):
    def test_rolls_follow_the_tavern_odds(self):
        rng = random.Random(7)
        base = [traits.roll(rng, traits.rarity_odds())[0]['id'] for _ in range(4000)]
        rarity = lambda i: traits.BY_ID[i]['rarity']
        self.assertAlmostEqual(sum(rarity(i) == 'rare' for i in base) / 4000, 0.30, delta=0.03)
        self.assertEqual(sum(rarity(i) == 'legendary' for i in base), 0, 'legendary only at Tavern 5')
        top = [traits.roll(rng, traits.rarity_odds(1.5, 2.0, 0.02)) for _ in range(4000)]
        self.assertAlmostEqual(sum(rarity(t[0]['id']) == 'epic' for t in top) / 4000, 0.20, delta=0.03)
        self.assertAlmostEqual(sum(len(t) == 2 for t in top) / 4000, traits.QUIRK_CHANCE, delta=0.03)

    def test_effects_depend_on_the_trip(self):
        owl, wolf = [dict(id='night_owl')], [dict(id='lone_wolf')]
        self.assertAlmostEqual(traits.effects(owl, hours=8)['speed'], 0.20)
        self.assertAlmostEqual(traits.effects(owl, hours=1)['speed'], -0.10)
        self.assertAlmostEqual(traits.effects(owl, hours=4)['speed'], 0.0)
        self.assertAlmostEqual(traits.effects(wolf, team=False)['speed'], 0.15)
        self.assertAlmostEqual(traits.effects(wolf, team=True)['speed'], -0.10)
        spec = [dict(id='specialist', target='iron')]
        self.assertAlmostEqual(traits.effects(spec, target='iron')['speed'], 0.25)
        self.assertEqual(traits.effects(spec, target='copper')['speed'], 0.0)
        self.assertEqual(traits.clean([dict(id='nope'), 'x', dict(id='lazy', quirk=True, extra=1)]), [dict(id='lazy', quirk=True)])

    def test_traits_change_a_workers_numbers(self):
        st = W.empty(T0)
        prodigy = W.new_worker(st, 'P', worker_traits=[dict(id='prodigy')], at=T0)
        self.assertEqual(W.points_for(30, prodigy['traits']), 29 + 3)
        master = W.new_worker(st, 'M', worker_traits=[dict(id='legendary_master')], at=T0)
        self.assertEqual(W.rank_cap(master, 'swift_pick'), 6)
        tireless = W.new_worker(st, 'T', worker_traits=[dict(id='tireless'), dict(id='hasty', quirk=True)], at=T0)
        self.assertEqual(W.max_trip_hours(tireless), 12.0); self.assertAlmostEqual(W.time_factor(tireless), 0.8)
        greedy = dict(traits=[dict(id='greedy', quirk=True)])
        self.assertEqual(W.retrain_price(greedy), int(W.RETRAIN_PRICE * 1.25))
        with self.assertRaisesRegex(ValueError, 'crew is full'):
            W.new_worker(st, 'X', at=T0)

    def test_a_trip_freezes_its_traits_tools_and_hot_spot(self):
        st = rich(forge=2, training=3)
        w = W.new_worker(st, 'Brom', worker_traits=[dict(id='diligent'), dict(id='lazy', quirk=True)], at=T0); w['tool'] = 2
        trip = W.start_trip(st, w['id'], 27, 2, at=T0, seed=5)
        m = trip['mods']
        self.assertAlmostEqual(m['speed'], 1 + 0.08 - 0.10 + 0.10)        # diligent, lazy, tier 2 pickaxe
        self.assertAlmostEqual(m['xp'], (1 + 0.20) * 1.30)                 # lazy, Training Grounds 3
        st['camp']['buildings']['forge'] = 5
        self.assertEqual(W.trip_view(w, T0)['mods']['speed'], m['speed'], 'a building finished later never changes a running trip')

    def test_hauls_bring_stone_and_traits_scale_them(self):
        plain = dict(id='w_00000001', type='miner', level=1, skills={}, traits=[])
        strong = dict(plain, traits=[dict(id='strong_back')])
        trip = dict(ore=27, work_hours=4.0, real_hours=4.0, started_at='2026-09-24T12:00:00Z', seed=11, mods=dict(W.NEUTRAL_MODS))
        a = W.haul(plain, trip, 4.0)
        b = W.haul(strong, dict(trip, mods=dict(W.NEUTRAL_MODS, amount=1.10)), 4.0)
        self.assertEqual(a['resources'], dict(stone=a['ore_total']))
        self.assertGreater(b['ore_total'], a['ore_total'] * 1.05)
        old = dict(trip); old.pop('mods')
        self.assertEqual(W.haul(plain, old, 4.0)['ore_total'], a['ore_total'], 'a trip from 0.7.0 has neutral multipliers')

    def test_idle_crewmates_learn_at_training_grounds_3_and_the_camp_gets_stone(self):
        st = rich(training=3, storehouse=5)
        a = W.new_worker(st, 'A', at=T0); b = W.new_worker(st, 'B', at=T0)
        W.start_trip(st, a['id'], 27, 1, at=T0, seed=1)
        plan = W.delivery_plan(a, a['trip'], 1.0, at=T0)
        out = W.apply_delivery(st, a['id'], plan, dict(created={}), at=T0)
        self.assertEqual(out['taught'], {b['id']: int(plan['xp'] * 0.10)})
        self.assertEqual(out['camp']['stone'], plan['resources']['stone'])
        self.assertEqual(st['camp']['resources']['stone'], 40000 + plan['resources']['stone'])
        self.assertEqual(a['stats']['stone'], plan['resources']['stone'])


class StateTests(unittest.TestCase):
    def test_a_070_file_gains_a_camp_and_keeps_its_crew(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = dict(schema=1, workers=[dict(id='w_ee38768d', type='miner', name='Miner 1', xp=179, level=3, skills=dict(swift_pick=2),
                                                trip=None, stats={}, history=[])], payments={'r': dict(state='paid')})
            (Path(tmp) / 'workers.json').write_text(json.dumps(old), encoding='utf-8')
            st = W.load(tmp)
            self.assertEqual(st['schema'], 2); self.assertEqual(st['workers'][0]['skills'], dict(swift_pick=2))
            self.assertEqual((st['workers'][0]['traits'], st['workers'][0]['tool'], st['workers'][0]['route']), ([], 0, 'vault'))
            self.assertEqual(st['camp']['buildings']['hq'], 1); self.assertEqual(st['payments'], {'r': dict(state='paid')})

    def test_candidates_stay_until_a_hire_and_follow_the_tavern(self):
        st = W.empty(T0)
        first = W.candidates(st, 'miner', T0)
        self.assertEqual(len(first), 1); self.assertEqual(W.candidates(st, 'miner', T0), first)
        with self.assertRaisesRegex(ValueError, 'needs a better camp'):
            W.candidates(st, 'adventurer', T0)
        st['camp']['buildings']['tavern'] = 2
        self.assertEqual(len(W.candidates(st, 'adventurer', T0)), 3)
        self.assertEqual(len(W.candidates(st, 'miner', T0)), 3, 'more candidates once the Tavern grows')


class WallsTests(SiegeFolder):
    def test_walls_hold_the_gate_longer(self):
        weak = siege_profile(pace=15.0)
        plain = siege.build_plan(weak, 3, 2.0, 'a', self.mods, seed=4)
        walled = siege.build_plan(weak, 3, 2.0, 'b', self.mods, seed=4, gate=camp.effects(dict(camp.new(), buildings=dict(camp.new()['buildings'], walls=5))))
        self.assertEqual(walled['siege']['gate_hp'], 150.0); self.assertEqual(walled['siege']['gate']['max_damage'], 45.0)
        self.assertGreater(walled['siege']['fell_at'] or 99, plain['siege']['fell_at'])
        self.assertGreaterEqual(siege.suggest_level(siege_profile(pace=300.0), 1.0, gate=dict(gate_hp=150.0, gate_repair=4.0, gate_max_damage=45.0)),
                                siege.suggest_level(siege_profile(pace=300.0), 1.0))


if __name__ == '__main__':
    unittest.main()
