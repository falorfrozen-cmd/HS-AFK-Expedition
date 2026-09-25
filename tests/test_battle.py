"""One wave against the town (0.9): battle.fight's walls, keep, towers, heroes and monster
behaviours. Plain dicts only; nothing reads the player's data or contacts the game.
"""
import math, sys, unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import battle

STRONG = 1_000_000.0             # walls and a keep no wave in these tests can break
RUSH = battle.SPAWN_DISTANCE     # a speed that reaches the wall in the first second


def town(towers=(), heroes=(), keep=STRONG, keep_dps=0.0, wall_hp=STRONG, armor=0.0, **sides):
    walls = {s: dict(hp=wall_hp, max=wall_hp, armor=armor) for s in battle.SIDES}
    walls.update(sides)
    return dict(walls=walls, keep=dict(hp=keep, max=keep, dps=keep_dps), towers=list(towers), heroes=list(heroes))


def group(ident='g', **spec):
    """A melee group on the north side, walking in from the spawn line."""
    return dict(dict(id=ident, side='north', count=1, hp=10.0, speed=6.0, reach=0.0, wall_dps=1.0, keep_dps=1.0, delay=0.0), **spec)


def tower(ident='t1', **spec):
    return dict(dict(id=ident, side='north', dps=10.0, dtype='physical', reach=60.0, min_reach=0.0, targets=1, air=True, ground=True), **spec)


def hero(ident='hero:1', **spec):
    return dict(dict(id=ident, side='north', dps=10.0, dtype='physical', reach=30.0, min_reach=0.0, targets=1, air=True, ground=True), **spec)


def fight(groups, **town_spec):
    return battle.fight(dict(groups=list(groups)), town(**town_spec))


def row(result, ident='g'):
    return next(k for k in result['kills'] if k['group'] == ident)


def lost(result, side='north'):
    """Wall health a side lost in the wave (every wall starts at STRONG)."""
    return STRONG - result['walls'][side]['hp']


def texts(result):
    return [h['text'] for h in result['highlights']]


def one_second():
    """Only the first second of a wave: shows whom the defenders shot first."""
    return patch.object(battle, 'MAX_SECONDS', 1)


def busy_wave():
    return dict(groups=[
        group('a', count=12, hp=4.0, speed=7.0, flags=['regenerating', 'shielded'], shield=1.6, resist=dict(physical=0.3)),
        group('b', side='east', count=6, hp=9.0, speed=RUSH, flags=['summoner', 'enraged'], delay=5.0),
        group('c', side='south', count=5, hp=3.0, speed=9.0, reach=25.0, flags=['splitting', 'exploding']),
        group('d', side='west', count=8, hp=2.0, speed=11.0, flags=['flying', 'frozen']),
        group('e', count=10, hp=5.0, speed=RUSH, flags=['teleporting', 'vampiric'], delay=20.0),
        group('f', side='east', count=2, hp=4.0, speed=16.0, flags=['thief'], wall_dps=0.0, keep_dps=0.0, delay=30.0),
        group('h', side='south', count=4, hp=6.0, speed=RUSH, flags=['burrowing', 'juggernaut'], delay=12.0)])


def busy_town():
    return town(towers=[tower('t1', dps=6.0, targets=3), tower('t2', side='keep', dtype='fire', dps=4.0, reach=85.0, targets=2),
                        tower('t3', side='east', dtype='cold', dps=2.0, reach=40.0, slow=0.3, targets=3),
                        tower('t4', side='south', dtype='holy', dps=3.0, reach=40.0, dispel=True, priority='elite', targets=2)],
                heroes=[hero('hero:1', roam=True, hold=0.2, dps=8.0, targets=3), hero('hero:2', side='west', dps=5.0, reach=55.0)],
                keep_dps=3.0, wall_hp=400.0, armor=0.1)


class FightTests(unittest.TestCase):
    def test_the_same_wave_and_town_give_the_same_result_and_neither_is_changed(self):
        wave, defenders = busy_wave(), busy_town()
        before = deepcopy((wave, defenders))
        first = battle.fight(wave, defenders)
        self.assertEqual(battle.fight(wave, defenders), first)
        self.assertEqual((wave, defenders), before, 'the wave and the town are read, never changed')
        self.assertGreater(sum(k['killed'] for k in first['kills']), 0)
        self.assertGreater(len({who for k in first['kills'] for who in k['by']}), 3, 'several towers and a hero shared the kills')

    def test_every_kill_is_credited_to_exactly_one_defender(self):
        result = battle.fight(busy_wave(), busy_town())
        for k in result['kills']:
            self.assertEqual(sum(k['by'].values()), k['killed'], k['group'])
        # every monster of the wave either died or leaked (a teleporter's share keeps counting as its group)
        for spec in busy_wave()['groups']:
            mine = [k for k in result['kills'] if k['group'] == spec['id'] or k['group'].startswith(spec['id'] + '.t')]
            self.assertEqual(sum(k['killed'] + k['leaked'] for k in mine), spec['count'], spec['id'])


class WallAndKeepTests(unittest.TestCase):
    def test_a_lone_melee_group_dies_to_a_tower_before_it_reaches_the_wall(self):
        result = fight([group(count=3)], towers=[tower(dps=100.0)])
        g = row(result)
        self.assertEqual((g['killed'], g['leaked'], g['by']), (3, 0, {'t1': 3}))
        self.assertEqual(result['walls']['north'], dict(hp=STRONG, max=STRONG, breached=False), 'it never touched the wall')
        self.assertEqual(result['keep'], dict(hp=STRONG, max=STRONG, fallen=False))
        self.assertLess(result['seconds'], 30, 'the wave ends once nothing is left')

    def test_monsters_at_the_wall_hit_it_and_armor_softens_the_blows(self):
        pack = group(count=2, wall_dps=5.0, speed=RUSH)
        plain = fight([pack])
        self.assertAlmostEqual(lost(plain), 2 * 5.0 * battle.MAX_SECONDS, msg='every second of the wave, at the wall')
        self.assertAlmostEqual(lost(fight([pack], armor=0.5)), lost(plain) * 0.5)
        self.assertAlmostEqual(lost(fight([pack], armor=0.99)), lost(plain) * (1 - 0.9), msg='armor stops at 90%')
        self.assertEqual(plain['keep']['hp'], STRONG, 'an unbroken wall keeps them out')
        self.assertEqual((row(plain)['killed'], row(plain)['leaked']), (0, 2))

    def test_a_breached_wall_lets_the_monsters_in_to_hit_the_keep(self):
        result = fight([group(count=5, wall_dps=10.0, keep_dps=2.0, speed=RUSH)], wall_hp=50.0)
        self.assertEqual(result['walls']['north'], dict(hp=0.0, max=50.0, breached=True))
        self.assertFalse(result['walls']['east']['breached'])
        self.assertIn('The north wall was breached', texts(result))
        self.assertAlmostEqual(STRONG - result['keep']['hp'], 5 * 2.0 * (battle.MAX_SECONDS - 1),
                               msg='the breach took the first second; from the next they are inside at the keep')

    def test_the_keep_falling_ends_the_wave(self):
        # no wall stands on the north side: the pack walks straight in
        result = fight([group(count=5, keep_dps=10.0, speed=RUSH)], keep=200.0, north=dict(hp=0.0, max=0.0, armor=0.0))
        self.assertTrue(result['walls']['north']['breached'], 'a side without a wall is open from the start')
        self.assertEqual(result['keep'], dict(hp=0.0, max=200.0, fallen=True))
        self.assertEqual(texts(result)[-1], 'The keep fell')
        self.assertEqual(result['seconds'], 4.0, '50 a second against 200')
        self.assertEqual((row(result)['killed'], row(result)['leaked']), (0, 5))

    def test_a_ranged_group_stops_at_its_reach_and_hits_the_wall_from_there(self):
        archers = group(count=1, hp=50.0, reach=25.0, wall_dps=2.0, speed=RUSH)     # at 25 from the first second
        short = fight([archers], towers=[tower(reach=20.0, dps=100.0)])
        self.assertEqual(row(short)['killed'], 0, 'a tower reaching 20 never touches archers standing at 25')
        self.assertAlmostEqual(lost(short), 2.0 * battle.MAX_SECONDS, msg='they shoot the wall all wave long')
        self.assertEqual(short['keep']['hp'], STRONG)
        stepped_out = fight([archers], heroes=[hero(reach=30.0, dps=100.0, air=False)])
        self.assertEqual(row(stepped_out)['by'], {'hero:1': 1}, 'a melee hero steps out 30 and reaches them')
        self.assertEqual(lost(stepped_out), 0)

    def test_what_is_still_alive_when_time_runs_out_withdraws_as_leaked(self):
        result = fight([group('here', count=3, speed=RUSH), group('late', count=2, delay=battle.MAX_SECONDS + 10)])
        self.assertEqual(result['seconds'], battle.MAX_SECONDS)
        self.assertEqual([(k['group'], k['killed'], k['leaked']) for k in result['kills']], [('here', 0, 3), ('late', 0, 2)],
                         'a group that never came is counted as leaked too')


class ShooterTests(unittest.TestCase):
    def test_a_mortar_cannot_hit_monsters_standing_at_the_wall(self):
        mortar = tower('m', min_reach=15.0, reach=75.0, dps=100.0, air=False)
        self.assertEqual(row(fight([group(speed=RUSH)], towers=[mortar]))['killed'], 0)
        self.assertEqual(row(fight([group()], towers=[mortar]))['by'], {'m': 1}, 'it shells the pack out in the field')

    def test_flyers_need_shooters_that_hit_the_air_and_pass_the_wall_to_hit_the_keep(self):
        flyers = group(count=2, flags=['flying'], wall_dps=5.0, keep_dps=5.0)
        free = fight([flyers])
        self.assertEqual(free['walls']['north']['hp'], STRONG, 'they fly over the wall')
        self.assertLess(free['keep']['hp'], STRONG, 'and hit the keep')
        grounded = fight([flyers], towers=[tower(air=False, reach=20.0, dps=100.0)])
        self.assertEqual(row(grounded)['killed'], 0, 'a tower that cannot hit the air watches them pass')
        self.assertEqual(row(fight([flyers], towers=[tower(air=True, dps=100.0)]))['by'], {'t1': 2})
        landed = fight([flyers], towers=[tower('k', side='keep', air=False, reach=5.0, dps=100.0)])
        self.assertEqual(row(landed)['by'], {}, 'at the keep they still fly: a ground-only tower cannot hit them')
        self.assertLess(landed['keep']['hp'], STRONG)
        sky = fight([flyers], towers=[tower('s', side='keep', air=True, ground=False, reach=5.0, dps=100.0)])
        self.assertEqual(row(sky)['by'], {'s': 2}, 'a flyers-only tower at the keep takes them down')
        harpoon = tower('h', air=True, ground=False, dps=100.0)
        self.assertEqual(row(fight([group(speed=RUSH)], towers=[harpoon]))['killed'], 0, 'a flyers-only tower ignores the ground')

    def test_each_priority_picks_its_own_target(self):
        groups = [group('a_near', hp=5.0, speed=RUSH), group('b_tough', hp=50.0, rank=2, speed=30.0),
                  group('c_elite', hp=20.0, rank=4, speed=30.0), group('d_flyer', hp=8.0, speed=30.0, flags=['flying']),
                  group('e_weak', hp=2.0, speed=30.0)]
        expected = dict(first='a_near', strongest='b_tough', weakest='e_weak', flying='d_flyer', elite='c_elite')
        self.assertEqual(set(expected), set(battle.PRIORITIES))
        with one_second():
            for priority, target in expected.items():
                result = fight(groups, towers=[tower(dps=1000.0, priority=priority)])
                self.assertEqual([k['group'] for k in result['kills'] if k['killed']], [target], priority)

    def test_a_shooter_with_several_targets_kills_several_monsters_at_once(self):
        with one_second():
            killed = {n: row(fight([group(count=10, speed=RUSH)], towers=[tower(dps=10.0, targets=n)]))['killed'] for n in (1, 5, 50)}
        self.assertEqual(killed, {1: 1, 5: 5, 50: 10})

    def test_frost_slows_a_group_so_towers_get_more_time(self):
        field_gun = tower('m', min_reach=15.0, reach=60.0, dps=10.0)     # only while they cross the field
        pack = group(count=50, speed=10.0)
        alone = row(fight([pack], towers=[field_gun]))['by']
        chilled = row(fight([pack], towers=[field_gun, tower('f', dps=0.0, slow=0.5)]))['by']
        self.assertGreater(chilled['m'], alone['m'])
        self.assertNotIn('f', chilled, 'the frost itself killed nothing')
        capped = row(fight([pack], towers=[field_gun, tower('f', dps=0.0, slow=0.95)]))['by']
        at_cap = row(fight([pack], towers=[field_gun, tower('f', dps=0.0, slow=battle.MAX_SLOW)]))['by']
        self.assertEqual(capped, at_cap, 'no slow goes past MAX_SLOW')

    def test_shields_are_absorbed_first_and_holy_fire_strips_them_three_times_as_fast(self):
        with one_second():
            def killed(shield, **shooter):
                return row(fight([group(speed=RUSH, shield=shield)], towers=[tower(dps=25.0, **shooter)]))['killed']
            self.assertEqual(killed(0.0), 1)
            self.assertEqual(killed(20.0), 0, '20 of the 25 went into the shield')
            self.assertEqual(killed(20.0, dtype='holy'), 1, 'holy fire used only a third of that on the shield')
            self.assertEqual(killed(20.0, dispel=True), 1, 'a dispelling tower strips shields like holy fire')

    def test_resistance_cuts_damage_and_pierce_ignores_part_of_it(self):
        with one_second():
            def killed(resist, pierce=0.0):
                return row(fight([group(count=10, hp=1.0, speed=RUSH, resist=dict(physical=resist))],
                                 towers=[tower(dps=4.0, pierce=pierce)]))['killed']
            self.assertEqual([killed(0.0), killed(0.5), killed(0.5, 0.5), killed(1.0), killed(1.0, 1.0), killed(-0.5)],
                             [4, 2, 3, 0, 4, 6])

    def test_a_frozen_aura_weakens_the_towers_of_its_side_but_not_the_heroes(self):
        frozen = group(count=100, hp=1.0, speed=RUSH, flags=['frozen'])
        with one_second():
            self.assertEqual(row(fight([dict(frozen, flags=[])], towers=[tower()]))['killed'], 10)
            self.assertEqual(row(fight([frozen], towers=[tower()]))['killed'], 8, '15% less damage while it stands close')
            self.assertEqual(row(fight([frozen], heroes=[hero()]))['killed'], 10, 'heroes shrug the aura off')
            self.assertEqual(row(fight([frozen], towers=[tower(side='keep', reach=90.0)]))['killed'], 10, 'keep towers stand back')
            far = dict(frozen, speed=6.0)
            self.assertEqual(row(fight([far], towers=[tower(reach=80.0)]))['killed'], 10, 'out of FROZEN_RANGE it chills nothing')

    def test_a_roaming_hero_moves_to_the_side_under_pressure(self):
        raid = group(side='east', count=3, speed=RUSH)
        self.assertEqual(row(fight([raid], heroes=[hero(side='north', roam=True, dps=100.0)]))['by'], {'hero:1': 3})
        self.assertEqual(row(fight([raid], heroes=[hero(side='north', dps=100.0)]))['killed'], 0, 'a hero told to hold the north stays')

    def test_melee_heroes_hold_part_of_the_wall_damage_up_to_a_cap(self):
        pack = group(wall_dps=10.0, speed=RUSH)
        bare = lost(fight([pack]))
        guard = lambda *holds, side='north': [hero(f'hero:{i}', side=side, dps=0.0, hold=h) for i, h in enumerate(holds)]
        self.assertAlmostEqual(lost(fight([pack], heroes=guard(0.3))), bare * 0.7)
        self.assertAlmostEqual(lost(fight([pack], heroes=guard(0.5, 0.5))), bare * (1 - battle.MAX_HOLD))
        self.assertAlmostEqual(lost(fight([pack], heroes=guard(0.5, side='east'))), bare, msg='only the wall a hero stands at')


class MonsterTests(unittest.TestCase):
    def test_fire_and_poison_stop_regeneration_and_vampires_heal_only_while_they_attack(self):
        troll = group(hp=100.0, speed=RUSH, flags=['regenerating'])
        heals = {dtype: row(fight([troll], towers=[tower(dps=1.5, dtype=dtype)]))['killed'] for dtype in ('physical', 'fire', 'poison')}
        self.assertEqual(heals, dict(physical=0, fire=1, poison=1), '2% a second outheals 1.5 damage unless it burns')
        bat = group(hp=100.0, speed=RUSH, flags=['vampiric'])
        self.assertEqual(row(fight([bat], towers=[tower(dps=1.2)]))['killed'], 0, 'it drinks while it hits the wall')
        self.assertEqual(row(fight([dict(bat, flags=[])], towers=[tower(dps=1.2)]))['killed'], 1)
        walking = dict(bat, speed=0.5)                    # still far out in the field when it dies
        self.assertEqual(row(fight([walking], towers=[tower(dps=1.2, reach=80.0)]))['killed'], 1, 'no drinking on the way in')

    def test_enraged_monsters_hit_harder_when_hurt_and_juggernauts_always(self):
        # one monster at the wall, 1000 health, 10 damage a second: it hits the wall 99 times before it dies,
        # the last 49 of them below half its health
        def wall_loss(flags):
            return lost(fight([group(hp=1000.0, speed=RUSH, flags=flags)], towers=[tower(dps=10.0)]))
        self.assertAlmostEqual(wall_loss([]), 99.0)
        self.assertAlmostEqual(wall_loss(['enraged']), 50 + 49 * battle.ENRAGE_MULT)
        self.assertAlmostEqual(wall_loss(['juggernaut']), 99 * battle.JUGGERNAUT_MULT)

    def test_teleporters_send_a_share_past_the_wall_once(self):
        result = fight([group(count=10, speed=RUSH, name='Stalker', flags=['teleporting'])])
        jump = int(10 * battle.TELEPORT_SHARE)
        self.assertEqual([(k['group'], k['leaked']) for k in result['kills']], [('g', 10 - jump), ('g.t1', jump)])
        self.assertEqual(texts(result), [f'{jump} Stalker blinked past the north wall'], 'only once')
        self.assertAlmostEqual(STRONG - result['keep']['hp'], jump * battle.MAX_SECONDS)
        self.assertAlmostEqual(lost(result), (10 - jump) * battle.MAX_SECONDS)

    def test_summoned_minions_carry_no_loot(self):
        result = fight([group(count=2, speed=RUSH, name='Necro', drops=2, flags=['summoner'])], towers=[tower(dps=1000.0, targets=10)])
        parent, minions = row(result), row(result, 'g.s1')
        self.assertEqual((parent['killed'], parent['loot'], parent['drops']), (2, True, 2))
        self.assertEqual((minions['name'], minions['killed'], minions['loot'], minions['drops']),
                         ('Summoned minion', 2 * battle.SUMMON_PER_MONSTER, False, 0))
        self.assertEqual(texts(result), [f'Necro called {2 * battle.SUMMON_PER_MONSTER} minions'])
        self.assertEqual(len(result['kills']), 2, 'a summoner calls once')

    def test_a_splitter_leaves_two_weaker_children_for_each_death(self):
        result = fight([group(count=3, speed=RUSH, name='Slime', drops=2, flags=['splitting'])], towers=[tower(dps=1000.0, targets=10)])
        children = [k for k in result['kills'] if k['group'] != 'g']
        self.assertEqual(row(result)['killed'], 3)
        self.assertEqual(sum(k['killed'] for k in children), 6)
        self.assertTrue(all(k['name'] == 'Slime spawn' and not k['loot'] and k['drops'] == 0 for k in children))
        self.assertTrue(all(k['group'].count('.') == 1 for k in children), 'children never split again')

    def test_an_exploding_death_near_the_wall_damages_it(self):
        bomber = group(count=2, hp=100.0, wall_dps=0.0, flags=['exploding'])
        at_wall = fight([dict(bomber, speed=RUSH)], towers=[tower(dps=1000.0, targets=2)])
        self.assertEqual(row(at_wall)['killed'], 2)
        self.assertAlmostEqual(lost(at_wall), 2 * 100.0 * battle.EXPLODE_WALL)
        far_out = fight([bomber], towers=[tower(dps=1000.0, targets=2, reach=80.0)])
        self.assertEqual((row(far_out)['killed'], lost(far_out)), (2, 0), 'a blast out in the field misses the wall')

    def test_burrowers_dig_under_the_wall_after_a_while(self):
        result = fight([group(speed=RUSH, name='Mole', flags=['burrowing'])])
        digging = int(battle.BURROW_SECONDS) - 1         # seconds at the wall before it is under it
        self.assertAlmostEqual(lost(result), digging)
        self.assertAlmostEqual(STRONG - result['keep']['hp'], battle.MAX_SECONDS - digging)
        self.assertEqual(texts(result), ['1 Mole dug under the north wall'])
        self.assertFalse(result['walls']['north']['breached'], 'nothing was broken on the way')

    def test_a_goblin_thief_escapes_after_reaching_the_keep_and_counts_as_leaked(self):
        goblins = group(count=2, speed=16.0, name='Treasure Goblin', wall_dps=0.0, keep_dps=0.0, flags=['thief'])
        result = fight([goblins])
        self.assertEqual((row(result)['killed'], row(result)['leaked']), (0, 2))
        self.assertEqual(texts(result), ['2 Treasure Goblin escaped with their loot'])
        arrive = math.ceil((battle.SPAWN_DISTANCE + battle.KEEP_DISTANCE) / 16) - 1     # the second it reaches the keep
        self.assertEqual(result['seconds'], arrive + battle.THIEF_ESCAPE)
        self.assertEqual((result['walls']['north']['hp'], result['keep']['hp']), (STRONG, STRONG), 'thieves hit nothing')
        caught = fight([dict(goblins, hp=5.0)], towers=[tower(dps=5.0, reach=10.0)])    # one shot as they pass the wall
        self.assertEqual((row(caught)['killed'], row(caught)['leaked']), (1, 1), 'a thief caught in time is a kill')


class RegressionTests(unittest.TestCase):
    """Bugs the unit tests found (2026-09-25), fixed. Each test states the rule."""

    def test_an_enraged_pack_hits_harder_once_it_has_lost_half_its_health(self):
        # Was a bug: battle.py:348: the enrage check compares the group's pool with half of its *living* monsters' full
        # health. Damage finishes the front monster first, so with two or more alive the pool is always above that:
        # only a group's last monster can ever enrage, and Enraged/Berserker packs never hit harder.
        def wall_loss(flags):
            return lost(fight([group(count=4, hp=100.0, speed=RUSH, flags=flags)], towers=[tower(dps=1.0)]))
        self.assertGreater(wall_loss(['enraged']), wall_loss([]))

    def test_the_spawn_of_a_flying_splitter_killed_over_the_town_stays_past_the_wall(self):
        # Was a bug: battle.py:208-215 with 263-265: _child copies no flags, so the children of a splitter shot down while
        # flying over the town (between the wall and the keep) are ground monsters at a negative position; the next
        # move clamps them back outside the wall, where they hit the wall instead of the keep.
        sky = tower('sky', side='keep', reach=20.0, dps=100.0, air=True, ground=False)
        result = fight([group(flags=['flying', 'splitting'])], towers=[sky])
        self.assertEqual(row(result)['by'], {'sky': 1}, 'the splitter died over the town')
        self.assertEqual(result['walls']['north']['hp'], STRONG, 'nothing that crossed the wall hits it from outside')

    def test_a_flyer_exploding_far_inside_the_walls_leaves_the_wall_alone(self):
        # Was a bug: battle.py:332: 'a death within EXPLODE_RANGE of the wall' is checked as pos <= EXPLODE_RANGE with no
        # lower bound, so a flyer shot down 20 inside the wall (pos -20) still blasts it for half its health.
        sky = tower('sky', side='keep', reach=5.0, dps=1000.0, air=True, ground=False)
        result = fight([group(hp=100.0, speed=5.0, wall_dps=0.0, keep_dps=0.0, flags=['flying', 'exploding'])], towers=[sky])
        self.assertEqual(row(result)['by'], {'sky': 1})
        self.assertEqual(result['walls']['north']['hp'], STRONG)

    def test_children_of_a_common_monster_get_their_share_of_its_health(self):
        # Was a bug: battle.py:75: group() floors every group's health at 1.0, which is the region's unit (one Common
        # monster, see bestiary.py). A summoned minion (10%) or a split child (30%) of a Common monster gets a whole
        # Common monster's health, so the same fight measured in a ten times smaller unit ends differently.
        def kills(unit):
            wave = [group('split', hp=unit, speed=RUSH, flags=['splitting']),
                    group('summon', side='east', hp=unit, speed=RUSH, flags=['summoner'])]
            with patch.object(battle, 'MAX_SECONDS', 2):
                result = fight(wave, towers=[tower('n', dps=unit), tower('e', side='east', dps=unit)])
            return {k['group']: k['killed'] for k in result['kills']}
        self.assertEqual(kills(1.0), kills(10.0))


if __name__ == '__main__':
    unittest.main()
