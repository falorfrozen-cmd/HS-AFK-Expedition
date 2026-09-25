"""Fortifications (0.9): towers as shooters, walls and keep, and the town's building rules in
town.py (slots, sites, the Siege Workshop, materials, building, arranging, healing, repairs).
Plain dicts; nothing reads the player's data or contacts the game.
"""
import sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import battle, camp, defense, fortifications as F, goods, town

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def a_camp(stone=100_000, spoils=100_000, dust=100_000, stock=None, **levels):
    c = camp.new(T0)
    c['buildings'].update(levels)
    c['resources'] = dict(stone=stone, spoils=spoils, dust=dust)
    c['stock'] = dict(stock or {})
    return c


def every_material(n=10_000):
    keys = {k for bands in town.TOWER_MATERIAL.values() for band in bands for k, _ in band} | set(town.PLATING_MATERIAL)
    return {k: n for k in keys}


def a_tower(tid='t1', kind='ballista', level=1, place='north', **extra):
    return dict(dict(id=tid, kind=kind, level=level, place=place, priority=None, perk=None, built_at=None), **extra)


class TowerTests(unittest.TestCase):
    def test_a_towers_damage_grows_by_the_tower_growth_with_each_level(self):
        for kind, spec in F.TOWER_BY_KEY.items():
            for level in (1, 2, 5, 10):
                self.assertAlmostEqual(F.tower_stats(a_tower(kind=kind, level=level))['dps'], spec['dps'] * F.TOWER_GROWTH ** (level - 1))
        self.assertEqual([F.tower_stats(a_tower(level=n))['level'] for n in (0, 99)], [1, F.MAX_TOWER_LEVEL])
        stats = F.tower_stats(a_tower(kind='mortar', level=3, place='west'))
        self.assertEqual((stats['side'], stats['dtype'], stats['reach'], stats['min_reach'], stats['targets'], stats['air'], stats['ground']),
                         ('west', 'physical', 75.0, 15.0, 6, False, True))

    def test_a_specialisation_counts_only_from_level_5(self):
        four, five = F.tower_stats(a_tower(level=4, perk='twin')), F.tower_stats(a_tower(level=5, perk='twin'))
        self.assertEqual((four['targets'], 'perk' in four), (1, False))
        self.assertEqual((five['targets'], five['perk']), (2, 'twin'))
        self.assertEqual(F.tower_stats(a_tower(level=5, perk='piercing'))['pierce'], 0.5)
        self.assertEqual(F.tower_stats(a_tower(kind='frost', level=5, perk='deep_freeze'))['slow'], 0.5)
        self.assertEqual(F.tower_stats(a_tower(kind='harpoon', level=5, perk='net'))['slow'], 0.4)
        self.assertEqual(F.tower_stats(a_tower(kind='mortar', level=5, perk='long_barrel'))['reach'], 75.0 + 20)
        plain = F.tower_stats(a_tower(kind='storm', level=6))
        self.assertAlmostEqual(F.tower_stats(a_tower(kind='storm', level=6, perk='overcharge'))['dps'], plain['dps'] * 1.3)
        self.assertNotIn('perk', F.tower_stats(a_tower(level=5, perk='inferno')), "another tower's specialisation does nothing")

    def test_keep_towers_stand_back_and_aim_where_they_are_told(self):
        wall, keep = F.tower_stats(a_tower(kind='storm', place='east')), F.tower_stats(a_tower(kind='storm', place='keep'))
        self.assertEqual((keep['side'], keep['reach']), ('keep', wall['reach'] + battle.KEEP_DISTANCE), 'as far past the wall as a wall tower')
        self.assertEqual(F.tower_stats(a_tower(priority='weakest'))['priority'], 'weakest')
        self.assertEqual(F.tower_stats(a_tower(priority='nearest'))['priority'], F.TOWER_BY_KEY['ballista']['priority'], 'an unknown aim falls back')


class WallAndKeepTests(unittest.TestCase):
    def test_wall_health_and_armor_follow_the_walls_level_and_plating(self):
        self.assertEqual([F.wall_max(level) for level in range(6)], list(F.WALL_HP))
        self.assertAlmostEqual(F.wall_max(3, 2), F.WALL_HP[3] * (1 + 2 * F.PLATING_HP))
        self.assertAlmostEqual(F.wall_armor(3, 2), F.WALL_ARMOR[3] + 2 * F.PLATING_ARMOR)
        self.assertEqual((F.wall_max(9, 9), F.wall_armor(9, 9)), (F.wall_max(5, F.MAX_PLATING), F.wall_armor(5, F.MAX_PLATING)), 'levels are capped')
        self.assertEqual(F.wall_max(0, 3), 0.0, 'no Walls, no wall to plate')
        self.assertEqual((F.keep(0), F.keep(3), F.keep(9)), (F.keep(1), dict(max=F.KEEP_HP[2], dps=F.KEEP_DPS[2]), F.keep(5)))
        self.assertEqual([F.hero_posts(hq) for hq in range(1, 6)], list(F.HERO_POSTS))
        self.assertEqual([F.tower_slots(w) for w in range(6)], list(F.TOWER_SLOTS))
        snap = defense.snapshot(dict(plating=dict(north=2), towers={'t1': a_tower(), 't2': a_tower('t2', level=0)}), dict(walls=3, hq=2))
        self.assertEqual(snap['walls']['north'], dict(max=F.wall_max(3, 2), armor=F.wall_armor(3, 2)))
        self.assertEqual(snap['walls']['east'], dict(max=F.wall_max(3), armor=F.wall_armor(3)))
        self.assertEqual((snap['keep'], snap['masons'], [t['id'] for t in snap['towers']]), (F.keep(2), F.MASONS[3], ['t1']),
                         'a tower still at level 0 does not fight')

    def test_a_wall_heals_by_the_hour_out_of_a_siege(self):
        c, t = a_camp(walls=3, hq=2), town.new()
        top, keep_top = F.wall_max(3), F.keep(2)['max']
        self.assertEqual((town.wall_now(t, c, 'north', T0), town.keep_now(t, c, T0)), (top, keep_top), 'never hurt, whole')
        town.set_health(t, dict(north=1000.0, east=top, south=top, west=top), 1500.0, T0)
        self.assertAlmostEqual(town.wall_now(t, c, 'north', T0 + timedelta(hours=2)), 1000.0 + 2 * top * F.HEAL_PER_HOUR)
        self.assertEqual(town.wall_now(t, c, 'north', T0 + timedelta(days=3)), top)
        self.assertEqual(town.wall_now(t, c, 'north', T0 - timedelta(hours=1)), 1000.0, 'no healing backwards in time')
        self.assertAlmostEqual(town.keep_now(t, c, T0 + timedelta(hours=1)), 1500.0 + keep_top * F.HEAL_PER_HOUR)
        self.assertEqual((F.mend(-5.0, 100.0, 0), F.mend(10.0, 0.0, 5)), (0.0, 0.0))

    def test_stone_mends_the_most_hurt_wall_first(self):
        c, t = a_camp(stone=1000, walls=3), town.new()
        top = F.wall_max(3)
        town.set_health(t, dict(north=top * 0.5, east=top * 0.8, south=top, west=top), F.keep(1)['max'], T0)
        per_stone = F.STONE_PER_REPAIR
        # the north wall (at half) needs 300 stone: all 250 go there
        self.assertEqual(town.repair_now(t, c, 250, T0), dict(stone=250, walls=dict(north=250 * per_stone)))
        self.assertEqual((c['resources']['stone'], t['walls']['north']['hp'], t['walls']['east']['hp']), (750, top * 0.5 + 250 * per_stone, top * 0.8))
        # now the east wall (80%) is worse off than the north (about 92%): it comes first, then the north; no stone is wasted
        done = town.repair_now(t, c, 500, T0)
        self.assertEqual(list(done['walls']), ['east', 'north'])
        self.assertEqual(done['stone'], F.repair_stone(top * 0.2) + F.repair_stone(top * 0.5 - 250 * per_stone))
        self.assertEqual([t['walls'][s]['hp'] for s in battle.SIDES], [top] * 4)
        with self.assertRaisesRegex(ValueError, 'whole'):
            town.repair_now(t, c, 10, T0)
        town.set_health(t, {s: top for s in battle.SIDES}, 500.0, T0)
        self.assertEqual(town.repair_now(t, c, 10, T0), dict(stone=10, walls=dict(keep=10 * per_stone)), 'with the walls whole, the keep')
        self.assertEqual((F.repair_stone(0), F.repair_stone(10), F.repair_stone(10.5), F.repair_stone(-3)), (0, 1, 2, 0))

    def test_stone_is_refused_under_siege_or_when_the_camp_lacks_it(self):
        c, t = a_camp(stone=40, walls=3), town.new()
        town.set_health(t, dict(north=100.0, east=100.0, south=100.0, west=100.0), 100.0, T0)
        for stone in (0, -5, 2.5):
            with self.assertRaisesRegex(ValueError, 'Choose how much stone'):
                town.repair_now(t, c, stone, T0)
        with self.assertRaisesRegex(ValueError, 'The camp has only 40 stone'):
            town.repair_now(t, c, 41, T0)
        t['siege'] = dict(id='defense_1')
        with self.assertRaisesRegex(ValueError, 'under siege'):
            town.repair_now(t, c, 10, T0)
        self.assertEqual(c['resources']['stone'], 40, 'nothing was taken')


class BuildTests(unittest.TestCase):
    def test_a_stored_town_is_repaired_not_trusted(self):
        t = town.normalize(dict(
            towers={'t3': dict(kind='ballista', level=14, place='moat', priority='nearest', perk='inferno'),
                    't7': dict(kind='frost', level='x', place='keep', priority='weakest', perk='deep_freeze'),
                    'bad': dict(kind='catapult'), 'x': 'junk'},
            next_tower=2, plating=dict(north=9, east=-1, south='x'), keep='junk', siege=dict(no_id=True), sending=dict(),
            walls=dict(north=dict(hp=-4, at=5), east=dict(hp=True), west=dict(hp=300.0, at='2026-09-25T12:00:00Z')),
            queue=[dict(target='tower:t3'), dict(), 'x'], history=[1, dict(id='a')], slain=dict(a=3, b=-1, c='x', d=True)))
        self.assertEqual(t['towers'], {'t3': dict(id='t3', kind='ballista', level=10, place='keep', priority=None, perk=None, built_at=None),
                                       't7': dict(id='t7', kind='frost', level=0, place='keep', priority='weakest', perk='deep_freeze', built_at=None)})
        self.assertEqual(t['next_tower'], 8, 'a new tower never takes an id in use')
        self.assertEqual(t['plating'], dict(north=5, east=0, south=0, west=0))
        self.assertEqual(t['walls'], dict(north=dict(hp=None, at=None), east=dict(hp=None, at=None), south=dict(hp=None, at=None),
                                          west=dict(hp=300.0, at='2026-09-25T12:00:00Z')))
        self.assertEqual((t['keep'], t['queue'], t['siege'], t['sending']), (dict(hp=None, at=None), [dict(target='tower:t3')], None, None))
        self.assertEqual((t['history'], t['slain']), ([dict(id='a')], dict(a=3)))
        self.assertEqual(town.normalize(None), town.new())

    def test_a_new_tower_needs_a_siege_workshop_and_a_free_slot(self):
        t, c = town.new(), a_camp(stock=every_material())
        self.assertEqual(town.next_tower(t, c, 'ballista', 'keep')['blockers'], ['needs a Siege Workshop'])
        c['buildings']['workshop'] = 4                          # two sites, so the queue only takes a slot
        plan = town.next_tower(t, c, 'ballista', 'keep')
        self.assertEqual((plan['target'], plan['to'], plan['hours'], plan['materials'], plan['blockers']),
                         ('new:ballista:keep', 1, F.tower_cost(1)['hours'], town.recipe('ballista', 1), []))
        self.assertEqual(plan['cost'], {k: v for k, v in F.tower_cost(1).items() if k != 'hours'})
        t['towers']['t1'] = a_tower(place='keep')
        self.assertIn('every tower slot is taken (1 at Walls level 0)', town.next_tower(t, c, 'mortar', 'keep')['blockers'],
                      'before any wall stands, one keep tower')
        c['buildings']['walls'] = 1
        self.assertEqual(town.next_tower(t, c, 'mortar', 'north')['blockers'], [])
        town.start(t, town.next_tower(t, c, 'mortar', 'north'), at=T0)
        self.assertIn('every tower slot is taken (2 at Walls level 1)', town.next_tower(t, c, 'frost', 'east')['blockers'],
                      'a tower being built takes its slot')
        with self.assertRaisesRegex(ValueError, 'Unknown tower'):
            town.next_tower(t, c, 'catapult', 'north')
        with self.assertRaisesRegex(ValueError, 'Choose a wall'):
            town.next_tower(t, c, 'ballista', 'moat')

    def test_a_tower_rises_only_as_high_as_the_siege_workshop_allows(self):
        t, c = town.new(), a_camp(stock=every_material(), workshop=1, walls=5)
        t['towers']['t1'] = a_tower(level=2)
        plan = town.next_upgrade(t, c, 't1')
        self.assertEqual((plan['to'], plan['blockers']), (3, ['needs Siege Workshop level 2']))
        self.assertEqual((plan['cost'], plan['materials']), ({k: v for k, v in F.tower_cost(3).items() if k != 'hours'}, town.recipe('ballista', 3)))
        c['buildings']['workshop'] = 2
        self.assertEqual(town.next_upgrade(t, c, 't1')['blockers'], [])
        self.assertEqual([town.limits(a_camp(workshop=w))['tower_max'] for w in range(6)], [0, 2, 4, 6, 8, 10])
        t['towers']['t1']['level'] = F.MAX_TOWER_LEVEL
        self.assertEqual(town.next_upgrade(t, c, 't1'), dict(target='tower:t1', to=None, cost=None, hours=None, materials={},
                                                            blockers=['already at its highest level']))
        with self.assertRaisesRegex(ValueError, 'No such tower'):
            town.next_upgrade(t, c, 't9')

    def test_a_busy_site_and_a_siege_hold_new_work_back(self):
        t, c = town.new(), a_camp(stock=every_material(), workshop=3, walls=5)
        t['towers'] = {'t1': a_tower(), 't2': a_tower('t2', place='east')}
        town.start(t, town.next_upgrade(t, c, 't1'), at=T0)
        self.assertEqual(town.next_upgrade(t, c, 't1')['blockers'], ['already being built', 'the Siege Workshop is busy'])
        self.assertEqual(town.next_upgrade(t, c, 't2')['blockers'], ['the Siege Workshop is busy'])
        c['buildings']['workshop'] = 4
        self.assertEqual(town.next_upgrade(t, c, 't2')['blockers'], [], 'a second site at Siege Workshop 4')
        self.assertEqual(town.next_upgrade(t, c, 't1')['blockers'], ['already being built'])
        t['queue'], t['siege'] = [], dict(id='defense_1')
        for plan in (town.next_tower(t, c, 'frost', 'keep'), town.next_upgrade(t, c, 't2'), town.next_plating(t, c, 'north')):
            self.assertEqual(plan['blockers'], ['the town is under siege'], plan['target'])

    def test_missing_resources_and_materials_are_named(self):
        c = a_camp(stone=100, spoils=0, dust=0, stock={'14:28': 10}, workshop=5, walls=5)
        self.assertEqual(town.next_tower(town.new(), c, 'ballista', 'north')['blockers'], ['missing 20 stone, 20 Iron Ore'])
        t = town.new()
        t['towers']['t1'] = a_tower(level=5)
        cost, need = F.tower_cost(6), town.recipe('ballista', 6)
        self.assertEqual(town.next_upgrade(t, c, 't1')['blockers'],
                         [f"missing {cost['stone'] - 100:,} stone, {cost['spoils']:,} spoils, {cost['dust']:,} dust, "
                          f"{need['14:6']:,} {goods.name('14:6')}"])
        with self.assertRaisesRegex(ValueError, 'Not enough in the camp stock: 20 Iron Ore missing'):
            town.take_materials(c, {'14:28': 30})
        self.assertEqual(c['stock'], {'14:28': 10}, 'nothing was taken')
        self.assertEqual(town.take_materials(c, {'14:28': 10}), {'14:28': 10})
        self.assertEqual(c['stock'], {}, 'an emptied material leaves the stock')
        town.give_back_materials(c, {'14:28': 10, '14:6': 0})
        self.assertEqual(c['stock'], {'14:28': 10})

    def test_plating_needs_walls_as_high_as_the_plating(self):
        t, c = town.new(), a_camp(stock=every_material(), workshop=1)
        self.assertEqual(town.next_plating(t, c, 'north')['blockers'], ['needs Walls', 'needs Walls level 1'])
        c['buildings']['walls'] = 2
        t['plating']['north'] = 2
        plan = town.next_plating(t, c, 'north')
        self.assertEqual((plan['to'], plan['blockers'], plan['materials']), (3, ['needs Walls level 3'], town.plating_recipe(3)))
        self.assertEqual(plan['cost'], {k: v for k, v in F.plating_cost(3).items() if k != 'hours'})
        t['plating']['north'] = F.MAX_PLATING
        self.assertEqual(town.next_plating(t, c, 'north')['blockers'], ['already fully plated'])
        with self.assertRaisesRegex(ValueError, 'Choose a wall'):
            town.next_plating(t, c, 'keep')

    def test_finished_work_stands_when_its_time_is_up(self):
        t, c = town.new(), a_camp(stock=every_material(), workshop=4, walls=3)
        t['next_tower'] = 4
        tower_plan, plating_plan = town.next_tower(t, c, 'frost', 'east'), town.next_plating(t, c, 'north')
        queued = town.start(t, tower_plan, request='r1', at=T0)
        self.assertEqual(queued, dict(target='new:frost:east', to=1, started_at='2026-09-25T12:00:00Z',
                                      ready_at=camp.iso(T0 + timedelta(hours=tower_plan['hours'])), request='r1'))
        t['walls']['north'] = dict(hp=100.0, at=camp.iso(T0))
        town.start(t, plating_plan, at=T0)
        self.assertEqual(town.settle(t, c, T0 + timedelta(hours=tower_plan['hours']) - timedelta(seconds=1)), [])
        done = town.settle(t, c, T0 + timedelta(hours=max(tower_plan['hours'], plating_plan['hours'])))
        self.assertEqual([(q['target'], q.get('tower')) for q in done], [('new:frost:east', 't4'), ('plating:north', None)])
        self.assertEqual(t['towers']['t4'], dict(id='t4', kind='frost', level=1, place='east', priority=None, perk=None, built_at=queued['ready_at']))
        self.assertEqual((t['next_tower'], t['plating']['north'], t['queue']), (5, 1, []))
        self.assertEqual(t['walls']['north'], dict(hp=None, at=None), 'the plated wall stands whole')
        self.assertEqual(town.wall_now(t, c, 'north', T0), F.wall_max(3, 1))
        town.start(t, town.next_upgrade(t, c, 't4'), at=T0)
        town.settle(t, c, T0 + timedelta(days=1))
        self.assertEqual(t['towers']['t4']['level'], 2)

    def test_arranging_a_tower_follows_the_rules(self):
        t = town.new()
        t['towers']['t1'] = a_tower(level=4)
        self.assertEqual(town.arrange(t, 't1', place='keep', priority='weakest'), dict(a_tower(level=4, place='keep', priority='weakest')))
        with self.assertRaisesRegex(ValueError, 'specialisation at level 5'):
            town.arrange(t, 't1', perk='twin')
        t['towers']['t1']['level'] = 5
        with self.assertRaisesRegex(ValueError, "one of this tower's specialisations"):
            town.arrange(t, 't1', perk='inferno')
        self.assertEqual(town.arrange(t, 't1', perk='twin')['perk'], 'twin')
        with self.assertRaisesRegex(ValueError, 'has chosen its specialisation'):
            town.arrange(t, 't1', perk='piercing')
        with self.assertRaisesRegex(ValueError, 'Choose a wall'):
            town.arrange(t, 't1', place='moat')
        with self.assertRaisesRegex(ValueError, 'Choose a target'):
            town.arrange(t, 't1', priority='nearest')
        with self.assertRaisesRegex(ValueError, 'No such tower'):
            town.arrange(t, 't9', priority='first')
        t['siege'] = dict(id='defense_1')
        with self.assertRaisesRegex(ValueError, 'cannot move during a siege'):
            town.arrange(t, 't1', place='north')
        self.assertEqual(town.arrange(t, 't1', place='keep', priority='elite')['priority'], 'elite', 'staying put and a new aim are fine')

    def test_tower_materials_come_in_bands(self):
        self.assertEqual([town.band(level) for level in range(1, 11)], [0, 0, 0, 1, 1, 1, 2, 2, 3, 3])
        self.assertEqual([town.recipe('ballista', level) for level in (1, 3, 4, 6, 7, 8, 9, 10)],
                         [{'14:28': 30}, {'14:28': 90}, {'14:6': 20}, {'14:6': 60}, {'14:14': 12}, {'14:14': 24},
                          {'14:21': 6, '14:58': 2}, {'14:21': 12, '14:58': 4}])
        self.assertEqual((town.recipe('catapult', 3), town.recipe('ballista', 0)), ({}, {}))
        self.assertEqual([town.plating_recipe(level) for level in (1, 5)], [{'14:27': 40}, {'14:32': 200}])
        for kind in F.TOWER_BY_KEY:
            for level in range(1, F.MAX_TOWER_LEVEL + 1):
                self.assertTrue(town.recipe(kind, level) and all(goods.known(k) for k in town.recipe(kind, level)), (kind, level))


if __name__ == '__main__':
    unittest.main()
