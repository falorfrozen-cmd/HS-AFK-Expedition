"""The town in the panel (0.9): the coffer and its receipts, fortifications, sieges with
stationed heroes and their claims, the town's share, wagons, merchants and shipments
to the Vault. Packets and profiles are synthetic; nothing contacts the game, the Item
Editor, Task Scheduler or the player's data.
"""
import json, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, bestiary, camp, defense, merchants, panel, town, trade, worker_loot, workers as W

HERO = dict(identity_version=2, slot=1, name='Suh', **{'class': 1})
ROOM = 'Act_02_02'


def mpacket(h, key, rank, name, room=ROOM, affixes=(), special=0, ranged=False, build='B'):
    """A monster kill packet shaped like the plugin's, with the protected drop values a replay needs."""
    return dict(schema=2, packet_hash=h * 64, room=room, game_build_id=build, monster_key=key, rank=rank, self_object='Enemy_obj',
                script='gml_Script_DropItem', args=[rank, 0, 1.0, 1.0, 1, 0],
                protected=dict(max_hp=1000.0 * rank, damage=10.0 * rank, killExperience=50.0 * rank, dSlots=rank, dCommonChance=4,
                               dCommonDropMult=11, dSatanicDropMult=1, extraMagicFind=0, lootAmount=0),
                self_snapshot=dict(name=name, moveSpeed=2.8, isRanged=1 if ranged else 0, fireImmune=False, coldImmune=False,
                                   poisonImmune=False, affixList=list(affixes), specialType=special))


PACKETS = [mpacket('a', 'e_orc_warrior_1', 1, 'Orc Warrior'), mpacket('b', 'e_orc_warrior_2', 2, 'Grunt Champion', affixes=(0, 8)),
           mpacket('c', 'e_orc_warrior_3', 3, 'Warchief', affixes=(2, 9)), mpacket('d', 'e_orc_warrior_3', 4, 'Warchief', affixes=(5, 10, 21)),
           mpacket('e', 'e_orc_hunter_1', 1, 'Orc Hunter', ranged=True), mpacket('f', 'e_orc_warrior_1', 1, 'Orc Warrior', special=9),
           mpacket('g', 'e_orc_warrior_1', 1, 'Old build', build='OLD')]


def profile_row(h, key, rank, count):
    return dict(hash=h * 64, kind='kill', rank=rank, count=count, weight=count, monster_key=key, exp=50.0 * rank, native_exp=50.0 * rank)


def hero_profile():
    rows = [profile_row('a', 'e_orc_warrior_1', 1, 300), profile_row('b', 'e_orc_warrior_2', 2, 60), profile_row('c', 'e_orc_warrior_3', 3, 30),
            profile_row('d', 'e_orc_warrior_3', 4, 10)]
    return dict(profile_id='p1', room=ROOM, character=HERO, farm_context=dict(schema=1, hash='a' * 64), rate_basis='farm-clock', forgepact={},
                coverage=1, basis_seconds=600, kills=400, breaks=0, kills_per_min=200.0, breaks_per_min=0.0, game_build='B',
                reward_baseline=dict(schema=1, complete=True, magic_find=1000), packets=rows, built_at='2026-09-25T00:00:00Z',
                quality=dict(window_rates=[190, 200, 210]))


class FakeGame:
    """The plugin's side of `afk worker pay`, `afk worker credit` and `afk worker deliver`."""
    def __init__(self, data, gold=1_000_000, silent=0):
        self.data, self.gold, self.silent, self.late, self.commands = Path(data), gold, silent, [], []

    def __call__(self, *_):
        return self

    def receipt(self, kind, request, amount):
        path = self.data / 'models' / f'worker-{kind}-{request}.json'
        if path.exists():
            return ['already processed']
        ok = (self.gold >= amount) if kind == 'pay' else (self.gold + amount <= 500_000_000)
        before = self.gold
        if ok:
            self.gold += -amount if kind == 'pay' else amount
        afk.write_json(path, dict(request_id=request, ok=ok, amount=amount, gold_before=before, gold_after=self.gold, character=HERO,
                                  saved='saved (character and account save performed)' if ok else '', error='' if ok else 'refused'))
        return ['ok']

    def run_late(self):
        for line in self.late:
            words = line.split()
            self.receipt(words[2], words[3], int(words[4]))
        self.late = []

    def send(self, line, timeout=30):
        self.commands.append(line)
        words = line.split()
        if words[1:3] in (['worker', 'pay'], ['worker', 'credit']):
            if self.silent:
                self.silent -= 1
                self.late.append(line)
                return None
            return self.receipt(words[2], words[3], int(words[4]))
        if words[1:3] == ['worker', 'deliver']:
            plan = json.loads(Path(' '.join(words[3:])).read_text(encoding='utf-8'))
            created = {f"{i['type']}:{i['id']}": i['amount'] for i in plan['items']}
            afk.write_json(self.data / 'sessions' / f"{plan['delivery_id']}.result.json",
                           dict(delivery_id=plan['delivery_id'], state='done', error='', created=created, prospect_outputs={}, stacks=len(created)))
            (self.data / 'spool' / f"{plan['delivery_id']}.ndjson").write_text('', encoding='utf-8')
        return ['ok']


class TownFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name) / 'afk'
        paths = {k: self.d / v for k, v in dict(DATA='', PACKETS='packets', SESSIONS='sessions', SPOOL='spool', PROFILES='profiles', PLANS='plans',
                                                  STATE='state.json', CONFIG='config.json').items()}
        for sub in ('packets', 'sessions', 'spool', 'profiles', 'plans', 'models'):
            (self.d / sub).mkdir(parents=True, exist_ok=True)
        p = patch.dict(afk.__dict__, paths); p.start(); self.addCleanup(p.stop)
        bestiary._CACHE.clear(); worker_loot._POOL_CACHE.clear()
        afk.write_json(self.d / 'build.json', dict(game_build='B'))
        afk.write_json(self.d / 'config.json', dict(game_bin=str(self.d)))
        (self.d / 'Hero_Siege.exe').write_bytes(b'x')
        for pk in PACKETS:
            afk.write_json(self.d / 'packets' / f"{pk['packet_hash']}.json", pk)
        afk.write_json(self.d / 'profiles' / f'{ROOM}--p1.json', hero_profile())
        self.app = panel.Panel(self.d); self.app.job = dict(output='')
        self.live = dict(character=HERO, room=ROOM, replay_running=False, online=False)
        patchers = [patch.object(self.app, 'fresh', return_value=self.live), patch.object(self.app, 'cli'),
                    patch.object(self.app, 'sync_notification'),
                    patch.object(panel.notify, 'sync_all', side_effect=AssertionError('Task Scheduler')),
                    patch.object(panel.notify, 'toast', side_effect=AssertionError('Windows notification')),
                    patch.object(panel.ingest_spool, 'discover_editor', side_effect=AssertionError('a real Item Editor')),
                    patch.object(panel.worker_loot, 'pools', return_value=dict(build='B', chests={}, goblins={}))]
        for p in patchers:
            p.start(); self.addCleanup(p.stop)
        self.game = FakeGame(self.d)

    def act(self, name, **args):
        with patch.object(afk, 'Ipc', self.game):
            return self.app.action(name, args)

    def state(self):
        """The town as the monitor keeps it: what settled by the clock is written."""
        return self.app.town_load(persist=True)

    def edit(self, fn):
        st = self.state(); fn(st); self.app.town_save(st)

    def age(self, minutes):
        """Move the siege's clock back, as if ``minutes`` passed."""
        st = self.state()
        path = Path(st['town']['siege']['path'])
        record = json.loads(path.read_text(encoding='utf-8'))
        started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=minutes)
        record['started_at'] = afk.iso(started)
        afk.write_json(path, record)
        return record

    def fund(self, gold=0, stone=0, **levels):
        def change(st):
            st['trade']['coffer'] = gold
            st['camp']['resources'].update(stone=stone, spoils=5_000, dust=1_000)
            st['camp']['buildings'].update(levels)
        self.edit(change)


class CofferTests(TownFolder):
    def test_gold_goes_into_the_coffer_and_back_to_the_hero_once_each(self):
        self.act('coffer_deposit', amount=300_000)
        self.assertEqual((self.state()['trade']['coffer'], self.game.gold), (300_000, 700_000))
        self.act('coffer_collect', amount=100_000)
        st = self.state()
        self.assertEqual((st['trade']['coffer'], self.game.gold), (200_000, 800_000))
        self.assertEqual([c['state'] for c in st['credits'].values()], ['paid'])
        with self.assertRaisesRegex(ValueError, 'holds only 200,000'):
            self.act('coffer_collect', amount=250_000)
        with self.assertRaisesRegex(ValueError, 'whole number'):
            self.act('coffer_collect', amount=1.5)

    def test_an_unanswered_payout_is_settled_once_and_never_paid_twice(self):
        self.fund(gold=50_000)
        self.game.silent = 1
        with self.assertRaisesRegex(ValueError, 'did not answer in time.*never paid twice'):
            self.act('coffer_collect', amount=20_000)
        self.assertEqual(self.state()['trade']['coffer'], 30_000, 'set aside while the game decides')
        self.game.run_late()
        self.app.settle_credits()
        st = self.state()
        self.assertEqual((st['trade']['coffer'], self.game.gold), (30_000, 1_020_000))
        self.assertEqual([c['state'] for c in st['credits'].values()], ['paid'])
        self.app.settle_credits()
        self.assertEqual(self.game.gold, 1_020_000, 'paid out once')

    def test_a_refused_payout_goes_back_into_the_coffer(self):
        self.fund(gold=50_000)
        self.game.gold = 499_990_000
        with self.assertRaisesRegex(ValueError, 'did not pay the gold.*stays in the coffer'):
            self.act('coffer_collect', amount=20_000)
        self.assertEqual(self.state()['trade']['coffer'], 50_000)


class ReviewTests(TownFolder):
    def test_a_refused_payout_whose_gold_rose_anyway_is_kept_for_review(self):
        self.fund(gold=50_000)
        def odd(request, amount):
            afk.write_json(self.d / 'models' / f'worker-credit-{request}.json',
                           dict(request_id=request, ok=False, amount=amount, gold_before=1_000, gold_after=1_000 + amount,
                                error='the game did not save (failed); taking the gold back failed', character=HERO))
            return ['ok']
        with patch.object(self.game, 'receipt', side_effect=lambda kind, request, amount: odd(request, amount)):
            with self.assertRaisesRegex(ValueError, 'yet the hero.s gold rose.*never paid twice'):
                self.act('coffer_collect', amount=20_000)
        st = self.state()
        self.assertEqual(st['trade']['coffer'], 30_000, 'not put back: the hero may hold it')
        self.assertEqual([c['state'] for c in st['credits'].values()], ['review'])
        self.assertEqual(self.app.town_view()['pending_credits'][0]['state'], 'review')


class FortificationTests(TownFolder):
    def test_towers_are_paid_from_the_coffer_and_stand_when_their_time_is_up(self):
        self.fund(gold=5_000_000, stone=5_000, workshop=1, walls=1)
        self.edit(lambda st: st['camp']['stock'].update({'14:28': 100}))
        self.act('fort_build', kind='ballista', place='north')
        st = self.state()
        self.assertEqual(st['trade']['coffer'], 5_000_000 - 100_000)
        self.assertEqual(st['camp']['stock'], {'14:28': 70}, 'thirty Iron Ore went into it')
        self.assertEqual(len(st['town']['queue']), 1)
        with self.assertRaisesRegex(ValueError, 'Siege Workshop is busy'):
            self.act('fort_build', kind='ballista', place='east')
        self.edit(lambda st: st['town']['queue'][0].update(ready_at='2020-01-01T00:00:00Z'))
        towers = self.state()['town']['towers']
        self.assertEqual([(t['kind'], t['level'], t['place']) for t in towers.values()], [('ballista', 1, 'north')])
        self.act('fort_arrange', tower='t1', priority='flying')
        self.assertEqual(self.state()['town']['towers']['t1']['priority'], 'flying')
        with self.assertRaisesRegex(ValueError, 'specialisation at level 5'):
            self.act('fort_arrange', tower='t1', perk='piercing')

    def test_nothing_is_taken_when_a_build_cannot_start(self):
        self.fund(gold=50_000, stone=5_000, workshop=1, walls=1)
        self.edit(lambda st: st['camp']['stock'].update({'14:28': 100}))
        with self.assertRaisesRegex(ValueError, 'the coffer holds 50,000 of 100,000 gold'):
            self.act('fort_build', kind='ballista', place='north')
        st = self.state()
        self.assertEqual((st['trade']['coffer'], st['camp']['stock'], st['camp']['resources']['stone']), (50_000, {'14:28': 100}, 5_000))

    def test_walls_are_repaired_with_stone_most_hurt_first(self):
        self.fund(stone=500, walls=2)
        self.edit(lambda st: town.set_health(st['town'], dict(north=2_000.0, east=500.0, south=2_500.0, west=2_500.0), 5_000.0,
                                             datetime.now(timezone.utc)))
        self.act('wall_repair', stone=100)
        st = self.state()
        self.assertEqual(st['camp']['resources']['stone'], 400)
        self.assertAlmostEqual(st['town']['walls']['east']['hp'], 1_500.0, delta=5)


class SiegeTests(TownFolder):
    def start(self, heroes=(), level=3, hours=0.5, stone=100, **levels):
        self.fund(gold=0, stone=1_000, **dict(dict(walls=2, hq=2, workshop=1), **levels))
        self.edit(lambda st: st['town']['towers'].update(t1=dict(id='t1', kind='ballista', level=3, place='keep', priority=None, perk=None)))
        return self.act('defense_start', room=ROOM, level=level, hours=hours, heroes=list(heroes), stone=stone)

    def test_a_towers_only_siege_runs_on_the_clock_and_settles_once(self):
        self.start()
        view = self.app.defense_view()['siege']
        self.assertEqual((view['waves_done'], view['wave'], view['over']), (0, 1, False))
        self.assertEqual(view['last'], [], 'the future never shows')
        with self.assertRaisesRegex(ValueError, 'already under siege'):
            self.act('defense_start', room=ROOM, level=1, hours=0.25, heroes=[], stone=0)
        self.age(12)
        view = self.app.defense_view()['siege']
        self.assertEqual((view['waves_done'], len(view['last'])), (2, 2))
        self.age(31)
        st = self.state()
        self.assertTrue(st['town']['siege']['settled'])
        entry = st['town']['history'][0]
        self.assertEqual((entry['waves'], entry['town_collected']), (6, False))
        self.assertIn(entry['outcome'], ('held', 'fell'))
        spoils = st['camp']['resources']['spoils']
        self.assertEqual(self.app.town_view()['history'][0]['id'], entry['id'])
        self.state()
        self.assertEqual(self.state()['camp']['resources']['spoils'], spoils, 'settled once')
        self.assertEqual(json.loads((self.d / 'workers.json').read_text(encoding='utf-8'))['town']['siege']['settled'], True)
        self.assertEqual(len(self.state()['town']['history']), 1)

    def test_a_stationed_hero_is_armed_and_paid_when_the_siege_ends(self):
        self.start(heroes=[dict(slot=1, stance='roam')])
        st = afk.load_state(self.d / 'state.json')
        armed = list(st['expeditions'].values())
        self.assertEqual([(a['mode'], a['hero']) for a in armed], [('defense', afk.hero_key(HERO))])
        plan = json.loads(Path(armed[0]['plan']).read_text(encoding='utf-8'))
        self.assertEqual((plan['mode'], plan['packets'], plan['defense']['slot']), ('defense', [], 1))
        self.assertTrue(plan['label'].startswith('Defense L3 · '))
        with self.assertRaisesRegex(SystemExit, 'still under siege'):
            afk.claim_plan_for(plan, 0.5, plan['expedition_id'] + '_claim')
        with self.assertRaisesRegex(ValueError, 'on the walls'):
            self.act('cancel', expedition=armed[0]['expedition_id'])
        with self.assertRaisesRegex(ValueError, 'already away'):
            self.edit(lambda s: s['town'].update(siege=None))
            self.act('defense_start', room=ROOM, level=1, hours=0.25, heroes=[dict(slot=1)], stone=0)

    def test_the_heroes_share_and_the_towns_share_add_up_to_every_kill(self):
        self.start(heroes=[dict(slot=1, stance='roam')], level=6)
        record = self.age(40)
        self.state()
        plan = json.loads(Path(list(afk.load_state(self.d / 'state.json')['expeditions'].values())[0]['plan']).read_text(encoding='utf-8'))
        claim = afk.claim_plan_for(plan, 0.5, plan['expedition_id'] + '_claim')
        record = json.loads(Path(record['id'] and self.state()['town']['siege']['path']).read_text(encoding='utf-8'))
        shares = defense.shares(record)
        drops = sum(n for row in record['timeline'][:defense.end_wave(record)] for per in row['drops'].values() for n in per.values())
        self.assertEqual(sum(shares['town'].values()) + sum(shares['heroes'][1].values()), drops)
        self.assertEqual(sum(p['count'] for p in claim['packets']), sum(shares['heroes'][1].values()))
        own = {r['hash'] for r in hero_profile()['packets']}
        self.assertTrue(all(p['hash'] in own for p in claim['packets']), "a hero replays only its own calibration's packets")
        self.assertTrue(all(p['exp'] > 0 for p in claim['packets']))
        self.assertEqual(claim['defense_claim']['level'], 6)

    def test_a_hero_with_nothing_to_claim_is_simply_freed(self):
        self.start(heroes=[dict(slot=1, stance='north')], level=2)
        record = self.age(40)
        path = Path(self.state()['town']['siege']['path'])
        record = json.loads(path.read_text(encoding='utf-8'))
        for row in record['timeline']:
            row['drops'] = {k: v for k, v in row['drops'].items() if not k.startswith('hero:')}   # the hero slew nothing
        afk.write_json(path, record)
        armed = list(afk.load_state(self.d / 'state.json')['expeditions'].values())[0]
        args = SimpleNamespace(expedition=armed['expedition_id'], dry_run=False, speed='normal', filtered='keep', no_ingest=True,
                               anywhere=False, forgepact_ignore=False, game_bin=str(self.d))
        with patch.object(afk, 'run_plan', side_effect=AssertionError('nothing to replay')):
            afk.cmd_claim(args)
        st = afk.load_state(self.d / 'state.json')
        self.assertEqual(st['expeditions'], {}, 'the hero is free again')
        self.assertEqual(st['last_claim']['expedition_id'], armed['expedition_id'] + '_claim')

    def test_two_heroes_take_their_posts_in_one_write(self):
        second = dict(HERO, slot=3, name='Kara', **{'class': 3})
        prof = dict(hero_profile(), character=second, profile_id='p2')
        afk.write_json(self.d / 'profiles' / f'{ROOM}--p2.json', prof)
        self.app.refresh_profiles()
        self.start(heroes=[dict(slot=1), dict(slot=3, stance='west')], hq=4)
        armed = sorted(afk.load_state(self.d / 'state.json')['expeditions'].values(), key=lambda a: a['expedition_id'])
        self.assertEqual([a['hero'] for a in armed], [afk.hero_key(HERO), afk.hero_key(second)])
        record = json.loads(Path(self.state()['town']['siege']['path']).read_text(encoding='utf-8'))
        self.assertEqual([(h['slot'], h['stance'], h['shooter']['class_name']) for h in record['heroes']],
                         [(1, 'roam', 'Viking'), (3, 'west', 'Marksman')])
        self.edit(lambda s: s['town'].update(siege=None))
        with self.assertRaisesRegex(ValueError, 'has 2 hero posts'):
            self.act('defense_start', room=ROOM, level=1, hours=0.25, heroes=[dict(slot=1), dict(slot=3), dict(slot=4)], stone=0)

    def test_the_towns_share_is_collected_once_in_the_region(self):
        self.start(level=4)
        self.age(40)
        def replay(*args):
            plan = json.loads(Path(args[1]).read_text(encoding='utf-8'))
            afk.write_json(self.d / 'sessions' / f"{plan['expedition_id']}.result.json", dict(rewards_saved=True))
        self.app.cli.side_effect = replay
        self.live['room'] = 'Act_01_01'
        with self.assertRaisesRegex(ValueError, 'Load any offline hero in'):
            self.act('defense_collect')
        self.live['room'] = ROOM
        self.act('defense_collect')
        plan_path = next(self.d.glob('plans/worker_defense_*.json'))
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        self.assertIs(plan['exp'], False)
        self.assertTrue(all(p['room'] == ROOM for p in plan['packets']))
        self.assertNotIn('g' * 64, {p['hash'] for p in plan['packets']}, 'an old-build packet never replays')
        self.assertTrue(self.state()['town']['history'][0]['town_collected'])
        self.assertTrue(self.app.defense_view()['siege']['town_collected'])
        with self.assertRaisesRegex(ValueError, 'No finished siege is waiting'):
            self.act('defense_collect')

    def test_repairs_and_the_retreat_happen_between_waves(self):
        self.start(level=8, hours=1.0, stone=0)
        self.age(11)
        before = self.state()['camp']['resources']['stone']
        try:
            self.act('defense_repair', stone=50)
            self.assertLess(self.state()['camp']['resources']['stone'], before)
        except ValueError as error:
            self.assertIn('Every wall is whole', str(error))
        self.act('defense_retreat')
        st = self.state()
        self.assertTrue(st['town']['siege']['settled'])
        self.assertEqual((st['town']['history'][0]['outcome'], st['town']['history'][0]['waves']), ('retreated', 2))
        with self.assertRaisesRegex(ValueError, 'not under siege'):
            self.act('defense_retreat')

    def test_a_siege_needs_a_known_region_a_defender_and_free_posts(self):
        self.fund(stone=0, walls=1, hq=1)
        with self.assertRaisesRegex(ValueError, 'knows no monster'):
            self.act('defense_start', room='Act_09_09', level=1, hours=1, heroes=[], stone=0)
        with self.assertRaisesRegex(ValueError, 'Nobody would defend'):
            self.act('defense_start', room=ROOM, level=1, hours=1, heroes=[], stone=0)
        with self.assertRaisesRegex(ValueError, 'has 0 hero posts'):
            self.act('defense_start', room=ROOM, level=1, hours=1, heroes=[dict(slot=1)], stone=0)
        with self.assertRaisesRegex(ValueError, 'only 0 stone'):
            self.act('defense_start', room=ROOM, level=1, hours=1, heroes=[], stone=10)


class AfterSiegeTests(TownFolder):
    def test_the_town_builds_repairs_and_moves_towers_again_once_a_siege_is_over(self):
        self.fund(gold=5_000_000, stone=5_000, walls=2, hq=2, workshop=2)
        self.edit(lambda st: (st['town']['towers'].update(t1=dict(id='t1', kind='ballista', level=3, place='keep', priority=None, perk=None)),
                              st['camp']['stock'].update({'14:28': 200})))
        self.act('defense_start', room=ROOM, level=1, hours=0.25, heroes=[], stone=0)
        with self.assertRaisesRegex(ValueError, 'the town is under siege'):
            self.act('fort_build', kind='ballista', place='north')
        self.age(20)
        self.assertTrue(self.state()['town']['siege']['settled'])
        self.act('fort_build', kind='ballista', place='north')
        self.act('fort_arrange', tower='t1', place='east')
        self.edit(lambda st: town.set_health(st['town'], dict(north=10.0, east=2_500.0, south=2_500.0, west=2_500.0), 5_000.0,
                                             datetime.now(timezone.utc)))
        self.act('wall_repair', stone=10)

    def test_pages_cannot_save_the_town(self):
        st = self.app.town_load(persist=False)
        with self.assertRaisesRegex(RuntimeError, 'a page tried to write the town'):
            self.app.town_save(st)

    def test_the_view_never_shows_the_fall_before_it_happens(self):
        self.fund(stone=1_000, walls=1, hq=1, workshop=1)
        self.edit(lambda st: st['town']['towers'].update(t1=dict(id='t1', kind='ballista', level=1, place='keep', priority=None, perk=None)))
        self.act('defense_start', room=ROOM, level=40, hours=2.0, heroes=[], stone=0)
        record = json.loads(Path(self.state()['town']['siege']['path']).read_text(encoding='utf-8'))
        self.assertLess(defense.end_wave(record), record['waves_total'], 'this siege falls early')
        view = self.app.defense_view()['siege']
        planned = afk.parse_iso(record['started_at']) + timedelta(hours=2)
        self.assertEqual((view['ends_at'], view['over']), (afk.iso(planned), False), 'the planned end until it happens')


class WatchTests(TownFolder):
    def watch(self, **args):
        self.fund(gold=0, stone=1_000, walls=2, hq=2, workshop=1)
        self.edit(lambda st: st['town']['towers'].update(t1=dict(id='t1', kind='ballista', level=3, place='keep', priority=None, perk=None)))
        return self.act('defense_watch', **dict(dict(room=ROOM, level=2, hours=0.25, stone=0), **args))

    def test_the_watch_keeps_sieges_coming_back_to_back_and_catches_up(self):
        self.watch()
        first = self.state()['town']['siege']
        self.assertTrue(first['watch'])
        self.age(40)                                  # three 5-minute waves each: two sieges ended meanwhile
        self.app.settle_town_late()
        st = self.state()
        ended = [h for h in st['town']['history']]
        self.assertEqual(len(ended), 2)
        self.assertTrue(all(h['watch'] for h in ended))
        self.assertEqual(ended[1]['id'], first['id'])
        self.assertEqual(st['town']['siege']['started_at'], ended[0]['ended_at'], 'the next siege starts where the last ended')
        self.assertFalse(st['town']['siege']['settled'])
        self.assertEqual(st['town']['watch']['started'], 3)
        with self.assertRaisesRegex(ValueError, "already under siege \\(the watch's\\)"):
            self.act('defense_start', room=ROOM, level=1, hours=0.25, heroes=[], stone=0)
        self.act('defense_watch', off=True)
        self.assertIsNone(self.state()['town']['watch'])
        self.assertFalse(self.state()['town']['siege']['settled'], 'the siege under way runs to its end')

    def test_the_watch_pauses_while_town_shares_wait_and_needs_towers(self):
        with self.assertRaisesRegex(ValueError, 'The watch needs towers'):
            self.fund(stone=0, walls=1); self.act('defense_watch', room=ROOM, level=1, hours=0.25, stone=0)
        self.watch()
        def waiting(st):
            st['town']['siege']['settled'] = True
            st['town']['history'] = [dict(id=f'old{i}', path='x', room=ROOM, region=ROOM, level=1, outcome='held', waves=3, waves_total=3,
                                          kills=1, spoils=0, stone_back=0, ended_at='2026-09-25T00:00:00Z', record={}, town_collected=False)
                                     for i in range(8)]
        self.edit(waiting)
        self.app.settle_town_late()
        self.assertEqual(self.state()['town']['watch']['paused'], '8 town shares wait to be collected')
        self.assertEqual(len(self.app.defense_view()['waiting']), 8)

    def test_a_waiting_town_share_is_never_dropped_from_the_history(self):
        rows = [dict(id=f's{i}', town_collected=i != 25) for i in range(30)]
        kept = town.keep_history(rows, dict(id='new', town_collected=False))
        self.assertEqual(len(kept), 21)
        self.assertEqual(kept[0]['id'], 'new'); self.assertEqual(kept[-1]['id'], 's25')
        again = town.normalize(dict(history=kept))['history']
        self.assertEqual([h['id'] for h in again], [h['id'] for h in kept], 'a load keeps it too')

    def test_the_watch_stands_down_after_the_keep_falls(self):
        self.watch(level=45, hours=0.5)
        self.age(60)
        self.app.settle_town_late()
        st = self.state()
        self.assertEqual(st['town']['history'][0]['outcome'], 'fell')
        self.assertIn('the keep fell', st['town']['watch']['paused'])
        self.assertTrue(st['town']['siege']['settled'], 'no siege starts on a fallen keep')

    def test_every_waiting_share_of_the_region_is_collected_oldest_first(self):
        self.watch()
        self.age(40)
        self.app.settle_town_late()
        collected = []
        def replay(*args):
            plan = json.loads(Path(args[1]).read_text(encoding='utf-8'))
            collected.append(plan['defense_id'])
            afk.write_json(self.d / 'sessions' / f"{plan['expedition_id']}.result.json", dict(rewards_saved=True))
        self.app.cli.side_effect = replay
        self.act('defense_watch', off=True)
        self.act('defense_collect', all=True)
        history = self.state()['town']['history']
        self.assertTrue(all(h['town_collected'] for h in history))
        self.assertEqual(collected, [h['id'] for h in reversed(history)])
        with self.assertRaisesRegex(ValueError, 'No town share waits'):
            self.act('defense_collect', all=True)


class TradeTests(TownFolder):
    def test_a_wagon_trades_away_and_comes_home_into_the_coffer_and_stock(self):
        self.fund(gold=200_000, trading_post=1)
        self.edit(lambda st: st['camp']['stock'].update({'14:27': 300}))
        run = self.act('trade_send', town='emberfall', cargo={'14:27': 300}, orders={'14:0': 10}, purse=50_000)
        st = self.state()
        self.assertEqual((st['trade']['coffer'], st['camp']['stock']), (150_000, {}))
        with self.assertRaisesRegex(ValueError, 'still on the road'):
            self.act('trade_unload', run=run['id'])
        def back(st):
            r = st['trade']['runs'][0]
            r.update(arrives_at='2020-01-01T00:00:00Z', returns_at='2020-01-01T05:00:00Z')
        self.edit(back)
        done = self.act('trade_unload', run=run['id'])
        st = self.state()
        self.assertEqual(st['camp']['stock'], {'14:0': 10})
        self.assertEqual(st['trade']['coffer'], 150_000 + done['entry']['gold_back'])
        self.assertGreater(done['entry']['earned'], 0)
        with self.assertRaisesRegex(ValueError, 'No such wagon'):
            self.act('trade_unload', run=run['id'])

    def test_wagons_need_a_trading_post_and_gold_in_the_coffer(self):
        self.fund(gold=10)
        with self.assertRaisesRegex(ValueError, 'Build a Trading Post'):
            self.act('trade_send', town='ironhold', cargo={}, orders={'14:27': 1}, purse=10)
        self.fund(gold=10, trading_post=1)
        with self.assertRaisesRegex(ValueError, 'holds only 10 gold'):
            self.act('trade_send', town='ironhold', cargo={}, orders={'14:27': 1}, purse=1_000)


class MarketTests(TownFolder):
    def visit(self):
        self.ident = f"keymaster@{merchants.watch_of(datetime.now(timezone.utc))}"
        return dict(id=self.ident, merchant='keymaster', name='Keymaster Brann', text='', arrives_at='2026-09-25T00:00:00Z',
                    leaves_at='2099-01-01T00:00:00Z', offers=[dict(key='12:8', side='sell', qty=3, price=60_000.0),
                                                          dict(key='13:1', side='buy', qty=20, price=2_500.0)])

    def test_merchants_sell_into_the_stock_and_buy_out_of_it_through_the_coffer(self):
        self.fund(gold=400_000, market=2)
        self.edit(lambda st: st['camp']['stock'].update({'13:1': 15}))
        with patch.object(merchants, 'find', return_value=self.visit()):
            self.act('market_buy', visit=self.ident, good='12:8', count=2)
            st = self.state()
            self.assertEqual(st['camp']['stock'].get('12:8'), 2)
            paid = 400_000 - st['trade']['coffer']
            self.assertGreater(paid, 100_000)
            with self.assertRaisesRegex(ValueError, 'has only 1 more'):
                self.act('market_buy', visit=self.ident, good='12:8', count=2)
            self.act('market_sell', visit=self.ident, good='13:1', count=10)
            st = self.state()
            self.assertEqual(st['camp']['stock']['13:1'], 5)
            self.assertGreater(st['trade']['coffer'], 400_000 - paid)
            with self.assertRaisesRegex(ValueError, 'has only 5'):
                self.act('market_sell', visit=self.ident, good='13:1', count=6)
            with self.assertRaisesRegex(ValueError, 'does not buy'):
                self.act('market_sell', visit=self.ident, good='14:27', count=1)
        self.assertEqual(len(self.state()['market']['history']), 2)


class PageTests(TownFolder):
    def test_pages_never_write_and_the_monitor_keeps_what_settled(self):
        self.fund(gold=100_000, stone=5_000, workshop=1, walls=1, trading_post=1, market=1)
        self.edit(lambda st: st['camp']['stock'].update({'14:28': 100, '14:27': 50}))
        self.act('fort_build', kind='ballista', place='north')
        self.act('trade_send', town='ironhold', cargo={'14:27': 50}, orders={}, purse=0)
        def due(st):
            st['town']['queue'][0]['ready_at'] = '2020-01-01T00:00:00Z'
            st['trade']['runs'][0].update(arrives_at='2020-01-01T00:00:00Z', returns_at='2020-01-01T01:00:00Z')
        st = self.app.town_load(); due(st); self.app.town_save(st)
        before = (self.d / 'workers.json').read_bytes()
        views = (self.app.town_view(), self.app.defense_view(), self.app.trade_view(), self.app.market_view(), self.app.town_summary())
        self.assertEqual((self.d / 'workers.json').read_bytes(), before, 'a page never writes')
        self.assertEqual([t['kind'] for t in views[0]['towers']], ['ballista'], 'but it shows the finished tower')
        self.assertEqual(views[2]['runs'][0]['state'], 'arrived')
        self.app.settle_town_late()
        kept = json.loads((self.d / 'workers.json').read_text(encoding='utf-8'))
        self.assertEqual(list(kept['town']['towers']), ['t1'])
        self.assertEqual(kept['trade']['runs'][0]['state'], 'arrived')


class MonitorSafetyTests(TownFolder):
    def test_the_monitor_settles_a_late_payout_before_any_action_has_run(self):
        self.fund(gold=50_000)
        self.game.silent = 1
        with self.assertRaises(ValueError):
            self.act('coffer_collect', amount=20_000)
        self.game.run_late()
        self.app.job = None                      # a fresh panel: no action has run yet
        self.app.settle_late_credits()
        self.assertEqual([c['state'] for c in self.state()['credits'].values()], ['paid'])
        self.assertIn('left the coffer', self.app.pause_note or '')

    def test_a_workers_page_never_writes_over_a_running_action(self):
        self.fund(gold=10)
        before = (self.d / 'workers.json').read_bytes()
        self.assertTrue(self.app.job_lock.acquire(blocking=False))
        try:
            self.app.workers_overview()
        finally:
            self.app.job_lock.release()
        self.assertEqual((self.d / 'workers.json').read_bytes(), before)
        self.app.workers_overview()
        self.assertNotEqual((self.d / 'workers.json').read_bytes(), before, 'between actions the rolled candidates are kept')


class RackTests(TownFolder):
    def test_basic_keys_bought_from_a_merchant_hang_on_the_key_rack(self):
        self.fund(gold=100_000, market=1)
        ident = f"keymaster@{merchants.watch_of(datetime.now(timezone.utc))}"
        visit = dict(id=ident, merchant='keymaster', name='Keymaster Brann', text='', arrives_at='2026-09-25T00:00:00Z',
                     leaves_at='2099-01-01T00:00:00Z', offers=[dict(key='12:0', side='sell', qty=10, price=1_000.0)])
        with patch.object(merchants, 'find', return_value=visit):
            self.act('market_buy', visit=ident, good='12:0', count=4)
        st = self.state()
        self.assertEqual((st['camp']['keys'].get('0'), st['camp']['stock'].get('12:0')), (4, None))


class ShipmentTests(TownFolder):
    def test_an_unanswered_shipment_stays_planned_and_is_never_made_twice(self):
        self.edit(lambda st: st['camp']['stock'].update({'13:1': 5}))
        silent = FakeGame(self.d)
        silent.send = lambda line, timeout=30: silent.commands.append(line) or None   # the game does not answer in time
        with patch.object(afk, 'Ipc', silent):
            with self.assertRaisesRegex(ValueError, 'did not answer in time.*stays planned'):
                self.app.action('stock_send', dict(items={'13:1': 3}))
        st = self.state()
        self.assertEqual(st['camp']['stock'], {'13:1': 2}, 'not given back: the game may still make them')
        self.assertIsNotNone(st['town']['sending'])
        with self.assertRaisesRegex(ValueError, 'has not worked on this shipment yet'):
            self.act('stock_close_partial')
        with patch.object(self.app, 'transfer_worker_haul') as transfer:
            self.act('stock_send', items={'13:1': 1})        # sending again finishes the planned one first
        st = self.state()
        self.assertEqual((st['camp']['stock'], st['town']['sending']), ({'13:1': 2}, None))
        transfer.assert_called_once()

    def test_the_town_page_shows_a_shipment_under_way_and_an_empty_stock_can_finish_it(self):
        self.edit(lambda st: st['camp']['stock'].update({'13:1': 3}))
        silent = FakeGame(self.d)
        silent.send = lambda line, timeout=30: silent.commands.append(line) or None   # the game does not answer in time
        with patch.object(afk, 'Ipc', silent):
            with self.assertRaisesRegex(ValueError, 'did not answer in time'):
                self.app.action('stock_send', dict(items={'13:1': 3}))
        self.assertEqual(self.state()['camp']['stock'].get('13:1', 0), 0, 'every unit went into the shipment')
        view = self.app.town_view()['sending']
        self.assertEqual((view['stage'], [(i['key'], i['count']) for i in view['items']]), ('waiting', [('13:1', 3)]))
        self.assertNotIn('plan', view, 'no local paths on a page')
        with patch.object(self.app, 'transfer_worker_haul') as transfer:
            self.act('stock_send')                          # no new goods: the shipment under way is finished
        self.assertIsNone(self.state()['town']['sending'])
        self.assertIsNone(self.app.town_view()['sending'])
        transfer.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'Choose the goods'):
            self.act('stock_send')                          # nothing under way and nothing asked for

    def test_a_shipment_the_game_refused_gives_the_goods_back(self):
        self.edit(lambda st: st['camp']['stock'].update({'13:1': 5}))
        refusing = FakeGame(self.d)
        refusing.send = lambda line, timeout=30: ['worker deliver: refused item 13:1']
        with patch.object(afk, 'Ipc', refusing):
            with self.assertRaisesRegex(ValueError, 'made nothing: worker deliver: refused item 13:1. The goods are back'):
                self.app.action('stock_send', dict(items={'13:1': 3}))
        st = self.state()
        self.assertEqual((st['camp']['stock'], st['town']['sending']), ({'13:1': 5}, None))

    def test_a_stopped_shipment_is_closed_as_partial(self):
        self.edit(lambda st: st['camp']['stock'].update({'13:1': 5, '15:2': 4}))
        stopping = FakeGame(self.d)
        def stop(line, timeout=30):
            plan = json.loads(Path(line.split(' ', 3)[3]).read_text(encoding='utf-8'))
            item = dict(itemType=13, itemDefinitionStruct=dict(b=1, o=2))
            (self.d / 'spool' / f"{plan['delivery_id']}.ndjson").write_text(json.dumps(dict(kind='item', item=item)) + '\n', encoding='utf-8')
            afk.write_json(self.d / 'sessions' / f"{plan['delivery_id']}.result.json", dict(delivery_id=plan['delivery_id'], state='error',
                                                                                          error='a stack could not be created'))
            return ['ok']
        stopping.send = stop
        with patch.object(afk, 'Ipc', stopping):
            with self.assertRaisesRegex(ValueError, 'stopped part way.*Close it as partial'):
                self.app.action('stock_send', dict(items={'13:1': 3, '15:2': 4}))
        self.assertEqual(self.app.town_view()['sending']['stage'], 'stopped')
        with patch.object(self.app, 'transfer_worker_haul') as transfer:
            done = self.act('stock_close_partial')
        self.assertEqual((done['made'], done['back']), ({'13:1': 2}, {'13:1': 1, '15:2': 4}))
        st = self.state()
        self.assertEqual((st['camp']['stock'], st['town']['sending']), ({'13:1': 3, '15:2': 4}, None))
        transfer.assert_called_once()

    def test_goods_reach_the_vault_as_items_the_game_makes(self):
        self.edit(lambda st: st['camp']['stock'].update({'15:17': 3, '14:64': 2}))
        with patch.object(self.app, 'transfer_worker_haul') as transfer:
            self.act('stock_send', items={'15:17': 2, '14:64': 2})
        st = self.state()
        self.assertEqual(st['camp']['stock'], {'15:17': 1})
        self.assertIsNone(st['town']['sending'])
        line = next(c for c in self.game.commands if 'worker deliver' in c)
        plan = json.loads(Path(line.split(' ', 3)[3]).read_text(encoding='utf-8'))
        self.assertTrue(plan['delivery_id'].startswith('worker_town_'))
        self.assertEqual(sorted((i['type'], i['id'], i['amount']) for i in plan['items']), [(14, 64, 2), (15, 17, 2)])
        transfer.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'has only 1'):
            self.act('stock_send', items={'15:17': 2})
        with self.assertRaisesRegex(ValueError, 'not a town good'):
            self.act('stock_send', items={'11:14': 1})


if __name__ == '__main__':
    unittest.main()
