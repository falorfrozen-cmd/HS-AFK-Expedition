"""Town defense (0.9): waves drawn from a frozen roster, the siege wave by wave, repairs and the
retreat, what the player may see, who is paid for the kills, records and forecasts.
Rosters are plain dicts in the bestiary's shape; nothing reads the player's data or contacts the game.
"""
import sys, tempfile, unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, battle, defense, fortifications as F

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
ROOM = 'Act_01_04'
WAVE = timedelta(minutes=defense.WAVE_MINUTES)


def h(c):
    return c * 64


def entry(monster_key, rank, packets, origin='ordinary', name=None, affix_sets=None, weights=None, speed=2.8, ranged=False, immune=()):
    """One bestiary entry, shaped like bestiary.index() makes them."""
    weights = weights or [1.0] * len(packets)
    return dict(monster_key=monster_key, rank=rank, origin=origin, name=name or monster_key, speed=speed, ranged=ranged,
                immune=list(immune), packets=list(packets), weights=weights, affix_sets=affix_sets or [[] for _ in packets],
                weight=float(sum(weights)))


ENTRIES = {
    'e_orc_1#1': entry('e_orc_1', 1, [h('1')], name='Orc'),
    'e_orc_2#2': entry('e_orc_2', 2, [h('2'), h('3')], name='Orc Champion', affix_sets=[[0], [9]], weights=[1.0, 3.0]),
    'e_orc_3#3': entry('e_orc_3', 3, [h('4')], name='Orc Ancient', affix_sets=[[2, 8]]),
    'e_orc_4#4': entry('e_orc_4', 4, [h('5')], name='Orc Legion', affix_sets=[[3, 10]]),
    'e_sand_wasp_1#1': entry('e_sand_wasp_1', 1, [h('6')], name='Sand Wasp'),
    'e_orc_hunter_1#1': entry('e_orc_hunter_1', 1, [h('7')], name='Orc Hunter', ranged=True),
    'e_orc_1#1@abyss': entry('e_orc_1', 1, [h('8')], origin='abyss', name='Orc'),
    'e_imp_2#2@unholy': entry('e_imp_2', 2, [h('9')], origin='unholy', name='Imp'),
}
GOBLINS = {'treasure': [h('a'), h('b')], 'rune': [h('c')]}
ONE_BALLISTA = [dict(id='t1', kind='ballista', level=1, place='keep')]
SIX_TOWERS = [dict(id=f't{i}', kind=kind, level=8, place=place) for i, (kind, place) in enumerate(
    [('ballista', 'north'), ('storm', 'east'), ('brazier', 'south'), ('plague', 'west'), ('obelisk', 'keep'), ('frost', 'keep')], 1)]


def siege(level=8, hours=1.0, towers=ONE_BALLISTA, walls=3, hq=3, stone=500, seed=7, heroes=(), goblins=None, entries=ENTRIES):
    snap = defense.snapshot(dict(towers={t['id']: t for t in towers}), dict(walls=walls, hq=hq))
    whole = {s: snap['walls'][s]['max'] for s in battle.SIDES}
    return defense.new_record('defense_test', ROOM, level, hours, town_snapshot=snap, walls_now=whole, keep_now=snap['keep']['max'],
                              heroes=list(heroes), entries=entries, goblins=goblins, stone_budget=stone, build='B', at=T0, seed=seed)


def clock(at):
    """defense's own clock (is_over, outcome and the claim read it) set to ``at``."""
    return patch.object(defense, 'now_utc', return_value=at)


class WaveTests(unittest.TestCase):
    def setUp(self):
        self.ros = defense.roster(ENTRIES)

    def test_the_schedule_puts_the_final_warlords_goblins_specials_and_elites_in_their_places(self):
        def kind(wave, total=90, goblins=True, special=('abyss', 'unholy')):
            return defense.wave_kind(wave, total, goblins, list(special))
        self.assertEqual(kind(12, total=12), 'final')
        self.assertEqual(kind(5, total=5), 'elite', 'a siege of fewer than six waves has no final wave')
        self.assertEqual([kind(25), kind(50)], ['warlord', 'warlord'])
        self.assertEqual([kind(10), kind(10, goblins=False)], ['goblin', 'elite'])
        self.assertEqual([kind(w) for w in (7, 14, 21, 28)], ['special:abyss', 'special:unholy', 'special:abyss', 'special:unholy'])
        self.assertEqual([kind(7, special=()), kind(35, special=())], ['normal', 'elite'], 'no special wave without special monsters')
        self.assertEqual([kind(w) for w in (1, 2, 3, 5, 15)], ['normal', 'normal', 'normal', 'elite', 'elite'])
        self.assertEqual([kind(50), kind(70), kind(35)], ['warlord', 'goblin', 'special:abyss'],
                         'a warlord before goblins, goblins before a special wave, a special wave before an elite one')
        self.assertEqual(defense.specials(self.ros), ['abyss', 'unholy'])

    def test_a_wave_is_drawn_the_same_from_the_same_seed_and_waves_differ(self):
        before = deepcopy(self.ros)
        wave = defense.make_wave(self.ros, 10, 3, 12, 42)
        self.assertEqual(defense.make_wave(self.ros, 10, 3, 12, 42), wave)
        self.assertEqual(defense.make_wave(self.ros, 10, 3, 24, 42), wave, 'a wave does not depend on how long the siege is')
        self.assertNotEqual(defense.make_wave(self.ros, 10, 4, 12, 42)['groups'], wave['groups'])
        self.assertNotEqual(defense.make_wave(self.ros, 10, 3, 12, 43)['groups'], wave['groups'])
        self.assertEqual(self.ros, before, 'the roster is never changed')

    def test_every_group_replays_a_packet_of_its_own_entry(self):
        for wave in range(1, 31):
            for g in defense.make_wave(self.ros, 12, wave, 30, 5, goblins=GOBLINS)['groups']:
                if g['arch'] == 'goblin':
                    self.assertIn(g['packet'], GOBLINS[g['entry'].split(':')[1]])
                    continue
                e = self.ros[g['entry']]
                self.assertIn(g['packet'], e['packets'])
                recorded = set(e['affix_sets'][e['packets'].index(g['packet'])])
                self.assertLessEqual(recorded, set(g['affixes']), "the packet's own affixes come with it")
                if g['tier'] == 'normal':
                    self.assertEqual(set(g['affixes']), recorded)

    def test_a_special_wave_draws_only_its_origins_monsters_and_the_others_only_ordinary_ones(self):
        special = defense.make_wave(self.ros, 10, 7, 12, 42)
        self.assertEqual((special['kind'], special['title']), ('special:abyss', 'Abyssal Incursion'))
        self.assertEqual({self.ros[g['entry']]['origin'] for g in special['groups']}, {'abyss'})
        self.assertTrue(all(g['name'].startswith('Abyssal ') for g in special['groups']))
        for wave in range(1, 7):
            self.assertEqual({self.ros[g['entry']]['origin'] for g in defense.make_wave(self.ros, 10, wave, 12, 42)['groups']}, {'ordinary'})
        only_special = defense.roster({k: e for k, e in ENTRIES.items() if '@' in k})
        drawn = {g['entry'] for g in defense.make_wave(only_special, 10, 1, 12, 42)['groups']}
        self.assertTrue(drawn and drawn <= set(only_special), 'a region of special monsters only still sends ordinary waves')

    def test_past_max_bodies_a_wave_brings_fewer_tougher_monsters(self):
        free = defense.make_wave(self.ros, 10, 3, 12, 42)
        with patch.object(defense, 'MAX_BODIES', 30):
            capped = defense.make_wave(self.ros, 10, 3, 12, 42)
        bodies = lambda wave: sum(g['count'] for g in wave['groups'])
        self.assertGreater(bodies(free), 30)
        self.assertLessEqual(bodies(capped), 30 + len(capped['groups']), 'each group rounds to whole monsters')
        factor = bodies(free) / 30
        for a, b in zip(free['groups'], capped['groups']):
            self.assertEqual((a['id'], a['entry'], a['packet'], a['side'], a['delay']), (b['id'], b['entry'], b['packet'], b['side'], b['delay']))
            self.assertLessEqual(b['count'], a['count'])
            self.assertAlmostEqual(b['hp'], a['hp'] * factor)
            self.assertAlmostEqual(b['wall_dps'], a['wall_dps'] * factor ** 0.5)
            self.assertAlmostEqual(b['count'] * b['hp'], a['count'] * a['hp'], delta=b['hp'], msg='the same health in fewer bodies')
        self.assertEqual(capped['event'], free['event'])

    def test_ascended_and_primordial_legions_appear_only_from_their_levels(self):
        ascended, primordial = defense.TIER_BY_KEY['ascended'], defense.TIER_BY_KEY['primordial']

        def groups(level):
            return [g for seed in range(40) for wave in (5, 15, 20) for g in defense.make_wave(self.ros, level, wave, 30, seed)['groups']]
        self.assertEqual({g['tier'] for g in groups(ascended['level'] - 1)}, {'normal'})
        below = groups(primordial['level'] - 1)
        self.assertEqual({g['tier'] for g in below}, {'normal', 'ascended'})
        above = groups(primordial['level'] + 10)
        self.assertIn('primordial', {g['tier'] for g in above})
        for g in below + above:
            tier = defense.TIER_BY_KEY[g['tier']]
            recorded = self.ros[g['entry']]['affix_sets'][0]
            self.assertEqual(g['drops'], tier['drops'])
            if g['tier'] != 'normal':
                self.assertEqual(g['rank'], 4, 'only Legions rise above their rank')
                self.assertTrue(g['name'].startswith(tier['name'] + ' '))
                self.assertEqual(len(g['affixes']), len(recorded) + tier['extra_affixes'])

    def test_the_rarer_ranks_march_more_often_at_higher_levels(self):
        every = [1, 2, 3, 4]
        low, high = defense.rank_weights(1, 'normal', every), defense.rank_weights(40, 'normal', every)
        share = lambda weights, rank: weights[rank] / sum(weights.values())
        self.assertLess(share(high, 1), share(low, 1))
        self.assertGreater(share(high, 3), share(low, 3))
        self.assertGreater(share(high, 4), share(low, 4))
        elite = defense.rank_weights(10, 'elite', every)
        self.assertNotIn(1, elite, 'no Commons in an elite wave')
        self.assertAlmostEqual(elite[2], defense.rank_weights(10, 'normal', every)[2] * 0.3)
        self.assertEqual(set(defense.rank_weights(10, 'normal', [1, 3])), {1, 3}, 'only the ranks the region has')
        self.assertEqual(defense.rank_weights(10, 'warlord', [1]), {1: 1.0}, 'a warlord wave of a Commons-only region still marches')

    def test_affixes_change_a_groups_health_speed_resistances_and_behaviour(self):
        # Champion, Extra Fast, Stoneskin, Cold Enchanted, Arcana's Curse, Fallen Angel and one not yet identified
        cursed = {'e_orc_2#2': entry('e_orc_2', 2, [h('2')], name='Orc Champion', affix_sets=[[0, 8, 10, 11, 18, 21, 22]], immune=('fire', 'cold'))}
        g = defense.make_wave(defense.roster(cursed), 1, 1, 12, 1)['groups'][0]
        self.assertAlmostEqual(g['hp'], defense.RANK_HP[2] * 1.2 * 1.3 * 1.1)
        self.assertAlmostEqual(g['speed'], 2.8 * defense.SPEED_SCALE * 1.5)
        self.assertAlmostEqual(g['wall_dps'], defense.WALL_DAMAGE * defense.RANK_DAMAGE[2])
        self.assertEqual(g['resist'], dict(fire=1.0, cold=1.0, physical=0.5, holy=0.5), 'an immunity stays whole')
        self.assertEqual(g['flags'], ['frozen', 'regenerating', 'shielded'])
        self.assertAlmostEqual(g['shield'], g['hp'] * 0.4)
        self.assertEqual(defense.affix(22)['name'], 'Unnamed affix 22')
        plain = defense.make_wave(self.ros, 1, 1, 12, 1)['groups']
        for g in plain:
            e = self.ros[g['entry']]
            self.assertEqual('flying' in g['flags'], g['entry'] == 'e_sand_wasp_1#1')
            self.assertEqual(g['reach'], defense.RANGED_REACH if e['ranged'] else 0.0)

    def test_a_blood_moon_speeds_the_monsters_and_hardens_their_blows(self):
        orcs = defense.roster({'e_orc_1#1': entry('e_orc_1', 1, [h('1')], name='Orc')})
        waves = [defense.make_wave(orcs, 1, 3, 12, seed) for seed in range(300)]
        moon = next(w for w in waves if w['event'] == 'blood_moon')
        calm = next(w for w in waves if w['event'] is None)
        for g in moon['groups']:
            self.assertAlmostEqual(g['speed'], 2.8 * defense.SPEED_SCALE * 1.25)
            self.assertAlmostEqual((g['wall_dps'], g['keep_dps']), (defense.WALL_DAMAGE * 1.15,) * 2)
        for g in calm['groups']:
            self.assertAlmostEqual((g['speed'], g['wall_dps']), (2.8 * defense.SPEED_SCALE, defense.WALL_DAMAGE))
        self.assertGreater(sum(w['event'] is not None for w in waves), 0)
        for wave in (1, 2, 5):   # before EVENT_FROM, and never on an elite wave
            self.assertTrue(all(defense.make_wave(orcs, 1, wave, 12, seed)['event'] is None for seed in range(300)), wave)

    def test_a_goblin_wave_sends_thieves_and_a_warlord_wave_a_boss_from_the_top_rank(self):
        raid = defense.make_wave(self.ros, 12, 10, 30, 3, goblins=GOBLINS)
        thieves = [g for g in raid['groups'] if g['arch'] == 'goblin']
        self.assertEqual(raid['kind'], 'goblin')
        self.assertEqual(sorted(g['entry'] for g in thieves), ['goblin:rune', 'goblin:treasure'])
        for g in thieves:
            self.assertEqual((g['flags'], g['count'], g['wall_dps'], g['keep_dps'], g['speed']), (['thief'], 1 + 12 // 10, 0.0, 0.0, defense.THIEF_SPEED))
            self.assertIn(g['packet'], GOBLINS[g['entry'].split(':')[1]])
        war = defense.make_wave(self.ros, 21, 25, 30, 3)
        bosses = [g for g in war['groups'] if g['boss']]
        self.assertEqual((war['kind'], war['sides'], len(bosses)), ('warlord', list(battle.SIDES), 1))
        boss = bosses[0]
        self.assertEqual((boss['tier'], boss['rank'], boss['count'], boss['drops']), ('warlord', 4, 1 + 21 // 20, defense.TIER_BY_KEY['warlord']['drops']))
        self.assertEqual(boss['name'], 'Warlord Orc Legion')
        self.assertGreater(boss['hp'], max(g['hp'] for g in war['groups'] if not g['boss']))
        later = next(g for g in defense.make_wave(self.ros, 21, 50, 60, 3)['groups'] if g['boss'])
        self.assertGreater(later['hp'], boss['hp'], 'a warlord grows with the wave')


class DefenderTests(unittest.TestCase):
    def test_a_heros_damage_comes_from_its_pace_and_the_ranks_it_killed(self):
        profile = dict(kills_per_min=60, packets=[dict(kind='kill', rank=1, count=30, hash=h('1')), dict(kind='kill', rank=4, count=10, hash=h('5')),
                                                  dict(kind='break', count=99, hash=h('b'))])
        viking = defense.hero_shooter(1, dict(class_name='Viking', name='Olaf'), profile, 'east')
        mean = (30 * 1.0 + 10 * defense.RANK_HP[4]) / 40
        self.assertAlmostEqual(viking['dps'], 60 * mean / 60)
        self.assertEqual((viking['id'], viking['name'], viking['side'], viking['roam'], viking['priority']), ('hero:1', 'Olaf', 'east', False, 'elite'))
        self.assertEqual((viking['dtype'], viking['reach'], viking['targets'], viking['air'], viking['hold']), ('physical', 30.0, 3, False, 0.20))
        faster = defense.hero_shooter(1, dict(class_name='Viking'), dict(profile, kills_per_min=120), 'east')
        self.assertAlmostEqual(faster['dps'], 2 * viking['dps'])
        stranger = defense.hero_shooter(2, dict(class_name='Nobody Knows'), dict(kills_per_min=30), 'roam')
        self.assertEqual((stranger['side'], stranger['roam'], stranger['name'], stranger['dps']), ('north', True, 'Hero 2', 0.5))
        self.assertEqual((stranger['dtype'], stranger['reach'], stranger['targets'], stranger['air'], stranger['hold']), defense.DEFAULT_ROLE)
        for cls, role in defense.CLASS_ROLES.items():
            self.assertGreaterEqual(role[1], defense.RANGED_REACH, f'a {cls} reaches monsters shooting at its wall')
        for pace in (0, None, float('nan')):
            with self.assertRaisesRegex(ValueError, 'no kill pace'):
                defense.hero_shooter(1, {}, dict(kills_per_min=pace))


class SiegeTests(unittest.TestCase):
    def test_the_walls_carry_their_damage_and_masons_and_stone_mend_them_between_waves(self):
        rec = siege(level=12)
        top = {s: rec['town']['walls'][s]['max'] for s in battle.SIDES}
        share, keep_top = rec['town']['masons'], rec['town']['keep']['max']
        self.assertTrue(any(r['walls'][s] < top[s] for r in rec['timeline'] for s in battle.SIDES), 'this siege hurts the walls')
        carried, stone = rec['start_state'], rec['stone_budget']
        for r in rec['timeline']:
            attacked = {g['side'] for g in r['groups']}
            for s in battle.SIDES:
                if s not in attacked:
                    self.assertAlmostEqual(r['walls'][s], carried['walls'][s], delta=1e-3, msg=f"wave {r['wave']}: {s} came in as it was left")
                masons = min(top[s], r['walls'][s] + top[s] * share)
                self.assertGreaterEqual(r['after']['walls'][s], masons - 1e-3, 'the masons mend every wall')
                self.assertLessEqual(r['after']['walls'][s], top[s])
            self.assertGreaterEqual(r['after']['keep'], min(keep_top, r['keep'] + keep_top * share) - 1e-3)
            self.assertEqual(r['after']['stone'], stone - r['stone_used'])
            if r['stone_used'] == 0 and stone > 0:
                self.assertEqual(r['after']['walls'], {s: top[s] for s in battle.SIDES}, 'stone is left only when every wall is whole')
            carried, stone = r['after'], r['after']['stone']
        self.assertEqual(sum(r['stone_used'] for r in rec['timeline']), rec['stone_budget'], 'the budget is spent to the last stone')

    def test_a_siege_stops_at_the_wave_the_keep_falls(self):
        rec = siege(level=18)
        fell = [r['wave'] for r in rec['timeline'] if r['fell']]
        self.assertEqual(fell, [rec['timeline'][-1]['wave']], 'the timeline ends with the wave the keep fell in')
        self.assertTrue(1 < fell[0] < rec['waves_total'], 'it held a while, then fell before the end')
        last = rec['timeline'][-1]
        self.assertEqual((last['after']['keep'], last['mended'], last['stone_used']), (0.0, {}, 0), 'nobody mends a fallen town')
        self.assertEqual(defense.end_wave(rec), fell[0])
        self.assertEqual(defense.ends_at(rec), T0 + fell[0] * WAVE)
        self.assertIsNone(defense.outcome(rec, T0 + (fell[0] - 1) * WAVE), 'not told before the wave is over')
        self.assertEqual(defense.outcome(rec, T0 + fell[0] * WAVE), 'fell')

    def test_redrawing_from_a_later_wave_keeps_the_earlier_rows(self):
        rec = siege(level=14)
        original = deepcopy(rec['timeline'])
        for k in (2, 5, len(original)):
            again = deepcopy(rec)
            defense.simulate(again, start=k)
            self.assertEqual(again['timeline'], original, f'from wave {k}: with nothing changed, the waves come out the same')
        marked = deepcopy(rec)
        marked['timeline'][0]['kills'] = -1
        defense.simulate(marked, start=3)
        self.assertEqual(marked['timeline'][0]['kills'], -1, 'rows before the start are kept, not drawn again')
        self.assertEqual(marked['timeline'][2:], original[2:])

    def test_a_repair_redraws_only_the_waves_after_it(self):
        rec = siege(level=14)
        original = deepcopy(rec['timeline'])
        at = T0 + 3 * WAVE + timedelta(seconds=30)
        hurt = {s: rec['town']['walls'][s]['max'] - v for s, v in original[2]['after']['walls'].items() if v < rec['town']['walls'][s]['max']}
        self.assertTrue(hurt, 'a wall is still hurt after wave 3')
        done = defense.repair(rec, 10_000, at)
        self.assertEqual(done['after_wave'], 3)
        self.assertEqual(done['walls'], {s: F.repair_stone(missing) * F.STONE_PER_REPAIR for s, missing in hurt.items()})
        self.assertEqual(done['stone'], sum(F.repair_stone(missing) for missing in hurt.values()), 'only what the walls need')
        self.assertEqual(rec['timeline'][:3], original[:3])
        fourth, before = rec['timeline'][3], original[3]
        self.assertEqual((fourth['kind'], fourth['sides'], fourth['event']), (before['kind'], before['sides'], before['event']), 'the same wave comes')
        for s in hurt:
            self.assertGreaterEqual(fourth['walls'][s], before['walls'][s], 'and meets a mended wall')
        self.assertNotEqual(fourth, before)
        self.assertEqual(rec['interventions'], [dict(kind='repair', after_wave=3, walls=done['walls'], stone=done['stone'], at='2026-09-25T12:15:30Z')])
        with self.assertRaisesRegex(ValueError, 'Choose how much stone'):
            defense.repair(rec, 0, at)
        with self.assertRaisesRegex(ValueError, 'Every wall is whole'):
            defense.repair(siege(level=14), 100, T0)
        with self.assertRaisesRegex(ValueError, 'The siege is over'):
            defense.repair(rec, 100, T0 + timedelta(hours=2))

    def test_a_retreat_ends_the_siege_with_the_waves_already_fought(self):
        rec = siege(level=14)
        at = T0 + 2 * WAVE
        self.assertEqual(defense.retreat(rec, at), 2)
        self.assertEqual([r['wave'] for r in rec['timeline']], [1, 2])
        self.assertEqual((defense.end_wave(rec), defense.is_over(rec, at), defense.outcome(rec, at)), (2, True, 'retreated'))
        with self.assertRaisesRegex(ValueError, 'The siege is over'):
            defense.retreat(rec, at)
        early = siege(level=14)
        self.assertEqual(defense.retreat(early, T0), 0)
        self.assertEqual((early['timeline'], defense.outcome(early, T0)), ([], 'retreated'), 'sounded before the first wave')

    def test_the_view_never_shows_a_wave_the_clock_has_not_reached(self):
        rec = siege(level=12)
        rows = rec['timeline']
        v = defense.view(rec, T0 + 2 * WAVE + timedelta(minutes=1))
        self.assertEqual((v['waves_done'], v['wave'], v['over'], v['outcome']), (2, 3, False, None))
        self.assertEqual([r['wave'] for r in v['last']], [1, 2])
        self.assertEqual(v['totals']['kills'], rows[0]['kills'] + rows[1]['kills'])
        self.assertEqual(v['walls']['south']['hp'], round(rows[1]['after']['walls']['south'], 1))
        self.assertEqual((v['stone_left'], v['next_wave_at']), (rows[1]['after']['stone'], '2026-09-25T12:15:00Z'))
        self.assertIsNone(v['next'], 'without a Watchtower the next wave stays hidden')
        self.assertEqual(sum(d['drops'] for d in v['by_defender']), sum(sum(per.values()) for r in rows[:2] for per in r['drops'].values()))
        before = defense.view(rec, T0 - timedelta(minutes=1))
        self.assertEqual((before['waves_done'], before['last'], before['totals']['kills'], before['stone_left']), (0, [], 0, rec['stone_budget']))
        self.assertEqual({s: w['hp'] for s, w in before['walls'].items()}, rec['start_state']['walls'])
        after = defense.view(rec, T0 + timedelta(hours=5))
        self.assertEqual((after['waves_done'], after['wave'], after['over'], after['outcome']), (rec['waves_total'], None, True, 'held'))

    def test_the_watchtower_scouts_more_of_the_next_wave_with_each_level(self):
        rec = siege(level=14)
        nxt = rec['timeline'][2]
        self.assertIsNone(defense.scout(rec, 2, 0))
        self.assertEqual(defense.scout(rec, 2, 1), dict(wave=3, kind=nxt['kind'], sides=nxt['sides']))
        self.assertEqual(defense.scout(rec, 2, 2), dict(wave=3, kind=nxt['kind'], sides=nxt['sides'], event=nxt['event']))
        seen = defense.scout(rec, 2, 3)['groups']
        self.assertEqual(len(seen), len(nxt['groups']))
        self.assertTrue(all(set(g) == {'name', 'tier', 'rank', 'side', 'count', 'affixes', 'flags', 'boss'} for g in seen),
                        'the monsters, never how their fight ends')
        self.assertIsNone(defense.scout(rec, defense.end_wave(rec), 3), 'nothing to scout after the last wave')
        self.assertEqual(defense.view(rec, T0 + 2 * WAVE, watchtower=3)['next'], defense.scout(rec, 2, 3))

    def test_a_siege_is_refused_without_a_level_a_length_a_bestiary_or_a_defender(self):
        for level in (0, defense.MAX_LEVEL + 1, 5.0):
            with self.assertRaisesRegex(ValueError, 'Choose a siege level'):
                siege(level=level)
        for hours in (0.2, defense.MAX_HOURS + 0.5, float('nan')):
            with self.assertRaisesRegex(ValueError, 'between 15 minutes and 8 hours'):
                siege(hours=hours)
        with self.assertRaisesRegex(ValueError, 'knows no monster'):
            siege(entries={})
        for stone in (-1, 1.5):
            with self.assertRaisesRegex(ValueError, 'stone budget'):
                siege(stone=stone)
        with self.assertRaisesRegex(ValueError, 'Nobody would defend'):
            siege(towers=[])
        rec = siege(level=10, hours=1.1)
        self.assertEqual((rec['waves_total'], rec['hours'], len(rec['timeline'])), (13, 13 * defense.WAVE_MINUTES / 60, 13))
        self.assertAlmostEqual(rec['mf_bonus'], 1 + 10 * defense.MF_BONUS_PER_LEVEL)


class RewardTests(unittest.TestCase):
    def paid(self, retreat_after=None):
        """A finished siege by hand: a hero who recorded orc packets 1 and 2 (3:1), and the drops of three waves."""
        roster = defense.roster({'e_orc_1#1': entry('e_orc_1', 1, [h('1'), h('2'), h('3')]), 'e_orc_2#2': entry('e_orc_2', 2, [h('4')])})
        heroes = [dict(slot=1, shooter=dict(id='hero:1', name='Olaf'), own={h('1'): 3.0, h('2'): 1.0}),
                  dict(slot=2, shooter=dict(id='hero:2', name='Idle'), own={h('4'): 1.0})]
        timeline = [dict(wave=1, fell=False, drops={'hero:1': {h('1'): 5, h('3'): 8, h('4'): 2}, 't1': {h('1'): 4}}),
                    dict(wave=2, fell=False, drops={'hero:1': {h('2'): 1}, 'keep': {h('4'): 3}}),
                    dict(wave=3, fell=False, drops={'hero:1': {h('1'): 100}})]
        interventions = [dict(kind='retreat', after_wave=retreat_after)] if retreat_after is not None else []
        return dict(id='defense_paid', room=ROOM, level=7, waves_total=3, wave_minutes=defense.WAVE_MINUTES, started_at=defense.iso(T0),
                    roster=roster, heroes=heroes, interventions=interventions, timeline=timeline)

    def test_a_heros_own_packets_go_to_the_hero_and_everything_else_to_the_town(self):
        out = defense.shares(self.paid(retreat_after=2))
        # packet 3 is an orc the hero never recorded: its 8 kills follow the hero's own orc packets, 3 to 1
        self.assertEqual(out['heroes'], {1: {h('1'): 5 + 6, h('2'): 2 + 1}, 2: {}})
        self.assertEqual(out['town'], {h('1'): 4, h('4'): 2 + 3}, "the towers' and keep's kills, and a monster the hero never met")
        paid = sum(n for per in out['heroes'].values() for n in per.values()) + sum(out['town'].values())
        self.assertEqual(paid, 5 + 8 + 2 + 4 + 1 + 3, 'every drop once; the wave after the retreat pays nothing')
        whole = defense.shares(self.paid())
        self.assertEqual(whole['heroes'][1][h('1')], 5 + 6 + 100)

    def test_a_heros_claim_waits_for_the_end_and_lists_only_its_candidates(self):
        record = self.paid()
        candidate = dict(hash=h('1'), monster_key='e_orc_1', room=ROOM, kind='kill', exp=12.0, weight=3.0)
        plan = dict(expedition_id='siege_hero', hours=1.0, extras=[], packets=[], estimate_rates=dict(items_per_call={}, gold_per_call=None),
                    zones=[dict(room=ROOM, weight=1, minutes=60, kills=0, breaks=0)],
                    defense=dict(id=record['id'], slot=1, record='', candidates=[candidate]))
        with tempfile.TemporaryDirectory() as tmp, patch.object(afk, 'RATE_FILE', Path(tmp) / 'delivery-rate.json'):
            with clock(T0 + 2 * WAVE):
                with self.assertRaises(SystemExit):
                    defense.hero_claim_plan(plan, record, 'claim_1')
            with clock(T0 + 3 * WAVE):
                out = defense.hero_claim_plan(plan, record, 'claim_1')
                path = Path(tmp) / 'record.json'
                afk.write_json(path, record)
                stored = defense.claim_from_plan(dict(plan, defense=dict(plan['defense'], record=str(path))), 'claim_1')
                self.assertEqual((stored['packets'], stored['defense_claim']), (out['packets'], out['defense_claim']), 'the same from the stored record')
                with self.assertRaisesRegex(SystemExit, 'record is missing'):
                    defense.claim_from_plan(dict(plan, defense=dict(plan['defense'], record=str(path), id='defense_other')), 'claim_1')
        self.assertEqual(out['packets'], [dict(candidate, count=5 + 6 + 100)], "packet 2 was the hero's, but not a candidate to replay")
        self.assertEqual((out['expedition_id'], out['scaled_from']), ('claim_1', 'siege_hero'))
        self.assertEqual((out['zones'][0]['minutes'], out['zones'][0]['kills']), (3 * defense.WAVE_MINUTES, 111))
        self.assertAlmostEqual(out['scale'], 3 * defense.WAVE_MINUTES / 60)
        self.assertEqual(out['defense_claim'], dict(id='defense_paid', level=7, waves_fought=3, outcome='held', replays=111))
        self.assertEqual(out['preview']['exp'], 111 * 12)
        self.assertEqual(plan['packets'], [], 'the plan itself is not changed')

    def test_the_best_level_held_and_the_most_waves_are_remembered(self):
        with tempfile.TemporaryDirectory() as tmp, clock(T0 + timedelta(hours=2)), \
                patch.object(afk, 'write_json', wraps=afk.write_json) as writes:
            held5, held3 = siege(level=5, hours=0.5), siege(level=3, hours=0.5)
            fallen = siege(level=20, walls=1, hq=1)
            self.assertEqual(defense.outcome(fallen), 'fell')
            self.assertEqual(defense.record_result(tmp, held5), dict(held=True, waves=True))
            self.assertEqual(defense.record_result(tmp, held5), dict(held=False, waves=False))
            self.assertEqual(writes.call_count, 1, 'nothing new, nothing written')
            self.assertEqual(defense.record_result(tmp, held3), dict(held=False, waves=True), 'a lower level is no new best')
            self.assertEqual(defense.record_result(tmp, fallen), dict(held=False, waves=True), 'a fall is no level held')
            saved = afk.read_json(Path(tmp) / defense.RECORDS)
        self.assertEqual(saved, dict(schema=1, regions={ROOM: dict(held=5, waves={'5': 6, '3': 6, '20': defense.end_wave(fallen)})}))


class ForecastTests(unittest.TestCase):
    def test_a_stronger_town_is_forecast_to_hold_a_level_at_least_as_high(self):
        weak, strong = siege(level=5, towers=ONE_BALLISTA), siege(level=5, towers=SIX_TOWERS)
        before = deepcopy(weak)
        f = defense.forecast(weak, 12, waves=4, runs=3)
        self.assertEqual((f['level'], f['waves']), (12, 4))
        self.assertTrue(0.0 <= f['fall_chance'] <= 1.0 and f['waves_low'] <= f['waves_median'] <= 4)
        self.assertEqual(defense.forecast(weak, 12, waves=4, runs=3), f, 'the same town and level forecast the same')
        self.assertEqual(weak, before, 'forecasting never changes the siege it looks at')
        self.assertGreater(defense.forecast(weak, defense.MAX_LEVEL, waves=4, runs=2)['fall_chance'], 0, 'one Ballista falls at the top')
        low, high = defense.suggest_level(weak, waves=4, runs=2), defense.suggest_level(strong, waves=4, runs=2)
        self.assertTrue(1 <= low <= high <= defense.MAX_LEVEL, (low, high))
        self.assertLessEqual(defense.forecast(weak, low, waves=4, runs=2)['fall_chance'], 0.25, 'the level it suggests usually holds')


if __name__ == '__main__':
    unittest.main()
