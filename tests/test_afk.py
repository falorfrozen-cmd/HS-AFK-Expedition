"""Regression tests for the September audit. No game, saves or user Vault used."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'afk.py'
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location('afk', SOURCE)
afk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afk)
CHAR = {'name': 'SameName', 'class': 13, 'slot': 1, 'identity_version': 2}


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name in ('DATA', 'PACKETS', 'SESSIONS', 'SPOOL', 'PROFILES', 'PLANS'):
            path = root / name
            path.mkdir()
            p = patch.object(afk, name, path)
            p.start(); self.addCleanup(p.stop)
        for name in ('STATE', 'CONFIG'):
            p = patch.object(afk, name, root / (name + '.json'))
            p.start(); self.addCleanup(p.stop)
        p = patch.object(afk, 'forgepact_current', return_value={})
        p.start(); self.addCleanup(p.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__(); self.addCleanup(self.output.__exit__, None, None, None)

    def profile(self, room='A', **changes):
        p = dict(room=room, character=copy.deepcopy(CHAR), game_build='B', forgepact={},
                 basis_seconds=120, kills=120, breaks=0, kills_per_min=60, breaks_per_min=0,
                 events_usable=120, coverage=1, packets=[dict(hash='a', count=120, weight=1,
                 kind='kill', monster_key='monster', exp=10)], rate_basis='wall')
        p.update(changes)
        afk.write_json(afk.PROFILES / (room + '.json'), p)
        return p

    def plan(self):
        return dict(expedition_id='test', character=copy.deepcopy(CHAR), game_build='B', forgepact={},
                    zones=[dict(room='A')],packets=[dict(count=10)])

    def fake_run(self, who, progress=None, ingest=False, save='save: saved (character and account save performed)'):
        commands = []
        class FakeIpc:
            def __init__(self, *args): pass
            def alive(self): return True
            def send(self, line, **kw):
                commands.append(line)
                if line == 'afk who': return ['who: ' + json.dumps(who)]
                if line == 'afk status': return ['build id : B', 'room : A']
                if line == 'afk save': return [save]
                if line.startswith('afk expedition start'): return ['expedition test: already done']
                raise AssertionError(line)
        afk.write_json(afk.SESSIONS / 'test.progress.json', progress or
                       dict(state='done', calls_done=10, calls_total=10, failed=0, skipped=0,
                            saved='saved (room-end save performed)'))
        with patch.object(afk, 'Ipc', FakeIpc):
            result = afk.run_plan(Path('memory'), self.plan(), Path('memory'), ingest)
        return result, commands

    def test_same_name_different_slot_rejected(self):
        with self.assertRaises(SystemExit): self.fake_run(dict(CHAR, slot=0))

    def test_unknown_and_legacy_identity_rejected(self):
        for who in (None, {}, dict(CHAR, slot=None), dict(CHAR, identity_version=1)):
            with self.subTest(who=who), self.assertRaises(SystemExit): self.fake_run(who)

    def test_matching_character_passes(self):
        result, _ = self.fake_run(dict(CHAR, level=101))
        self.assertEqual(result['state'], 'done')

    def test_claim_reads_its_own_state_when_another_panel_polls(self):
        context={'hash':'expected'}
        plan=dict(self.plan(),farm_context=context)
        commands=[]
        class FakeIpc:
            def __init__(self,*args):pass
            def alive(self):return True
            def send(self,line,**kw):
                commands.append(line)
                if line.startswith('afk session state '):
                    token=line.split()[3]
                    self_test.assertTrue(line.endswith(' isolated'))
                    afk.write_json(afk.DATA/'models'/f'session-state-{token}.json',dict(request_id=token,online=False,farm_context=context))
                    afk.write_json(afk.DATA/'models/session-state.json',dict(request_id='other-panel',online=False,farm_context={'hash':'other'}))
                    return ['state written']
                if line=='afk who':return ['who: '+json.dumps(CHAR)]
                if line=='afk status':return ['build id : B','room : A']
                if line=='afk save':return ['save: saved (character and account save performed)']
                if line.startswith('afk expedition start'):return ['expedition test: already done']
                raise AssertionError(line)
        self_test=self
        afk.write_json(afk.SESSIONS/'test.progress.json',dict(state='done',calls_done=10,calls_total=10,failed=0,skipped=0,saved='saved (character and account save performed)'))
        with patch.object(afk,'Ipc',FakeIpc):
            result=afk.run_plan(Path('memory'),plan,Path('memory'),False)
        self.assertEqual(result['state'],'done')
        self.assertFalse(list((afk.DATA/'models').glob('session-state-*.json')))

    def test_native_xp_requires_every_kill_and_preserves_zero(self):
        meta=dict(deep=True,complete=True,monster_key='m',room='A',build='B',exp=400)
        for second,complete,expected in ((20,True,10),(None,False,None),(0,True,0)):
            recs=[dict(kind='session_start',build='B',character=CHAR,forgepact={},reward_baseline=dict(schema=1,available=True,magic_find=1000))]
            recs += [dict(kind='kill',t=f'2026-09-18T00:0{i}:00Z',room='A',packet='good',kill_exp=400,native_kill_exp=xp) for i,xp in enumerate((0,second))]
            with patch.object(afk,'read_ndjson',return_value=recs),patch.object(afk,'packet_index',return_value={'good':meta}):
                profile=afk.build_profile(Path('memory'),'A',False)[0]
            self.assertEqual(profile['reward_baseline']['complete'],complete)
            self.assertEqual(profile['packets'][0]['native_exp'],expected)

    def test_mixed_profile_context_rejected(self):
        for changes in (dict(character=dict(CHAR, slot=0)), dict(character=None),
                        dict(game_build='old'), dict(forgepact={'density': 3})):
            self.profile('A'); self.profile('B', **changes)
            with self.subTest(changes=changes), self.assertRaises(SystemExit):
                afk.make_plan(1, [('A', 1), ('B', 1)], 'test', 40, 'none')

    def test_old_character_stamp_requires_recapture(self):
        self.profile(character={'name': 'SameName', 'class': 13, 'slot': 0})
        with self.assertRaises(SystemExit): afk.make_plan(1, [('A', 1)], 'test', 40, 'none')

    def test_partial_packet_coverage_does_not_inflate_survivor(self):
        self.profile(kills=100, kills_per_min=50, events_usable=96, coverage=.96,
                     packets=[dict(hash='a', count=96, weight=1, kind='kill', monster_key='monster', exp=10)])
        p = afk.make_plan(120/3600, [('A', 1)], 'test', 40, 'none')
        self.assertEqual(p['preview']['calls'], 96)
        self.assertEqual(p['preview']['exp'], 960)

    def test_low_coverage_rejected(self):
        self.profile(events_usable=1, coverage=1/120)
        with self.assertRaises(SystemExit): afk.make_plan(1, [('A', 1)], 'test', 40, 'none')

    def test_scale_preserves_total_and_recomputes_xp(self):
        pk = [dict(hash=str(i), count=101, kind='kill' if i % 2 else 'break', exp=i*3,
                   room='A', extra=i==0) for i in range(10)]
        base = dict(expedition_id='base', packets=pk, per_frame=40, exp=True, gold='none',
                    zones=[dict(room='A', minutes=480, kills=505, breaks=505)],
                    extras=[dict(packet='0', count=101)], preview=dict(calls=1010, kills=505,
                    breaks=505, exp=12345, items_estimate=None, gold_estimate=None))
        for factor in (0, .001, .017, .5, 1):
            with self.subTest(factor=factor):
                p = afk.scale_plan(base, factor, 'scaled')
                self.assertEqual(sum(x['count'] for x in p['packets']), round(1010*factor))
                self.assertEqual(p['preview']['calls'], sum(x['count'] for x in p['packets']))
                self.assertEqual(p['preview']['exp'], int(sum(x['count']*x['exp'] for x in p['packets'])))
                self.assertEqual(p['extras'][0]['count'], sum(x['count'] for x in p['packets'] if x.get('extra')))

    def test_per_room_duration_excludes_other_room_visits(self):
        recs = [dict(kind='session_start', build='B', character=CHAR, forgepact={})]
        recs += [dict(kind='kill', t='2026-09-18T'+t+'Z', room=r, packet='a')
                 for t,r in [('00:00:00','A'), ('00:01:00','A'), ('00:02:00','B'),
                             ('00:29:00','B'), ('00:30:00','A'), ('00:31:00','A')]]
        meta = dict(deep=True, complete=True, monster_key='m', room='A', exp=10, build='B')
        with patch.object(afk, 'read_ndjson', return_value=recs), patch.object(afk, 'packet_index', return_value={'a':meta}):
            p = afk.build_profile(Path('memory'), 'A', False)[0]
        self.assertEqual(p['wall_seconds'], 122)

    def test_failed_run_returns_nonzero(self):
        afk.write_json(afk.PLANS/'p.json', self.plan())
        for result in (dict(state='error'), dict(state='aborted'), dict(state='done',failed=1),
                       dict(state='done',skipped=3), dict(state='done',success=False)):
            with patch.object(afk,'print_preview'), patch.object(afk,'game_bin'), patch.object(afk,'run_plan',return_value=result):
                with self.subTest(result=result), self.assertRaises(SystemExit):
                    afk.cmd_run(SimpleNamespace(plan=str(afk.PLANS/'p.json'),no_ingest=True,anywhere=False,forgepact_ignore=False))

    def test_done_run_retries_ingest_without_replay(self):
        with patch.object(afk, 'ingest_spool', return_value=0) as ingest:
            result, _ = self.fake_run(CHAR, ingest=True)
        ingest.assert_called_once()
        self.assertTrue(result['success'])

    def test_ingest_and_save_failures_not_reported_as_success(self):
        with patch.object(afk, 'ingest_spool', return_value=2):
            result, _ = self.fake_run(CHAR, ingest=True)
        self.assertFalse(result['success'])
        self.assertEqual(result['stages']['ingest'], 'error')
        result, _ = self.fake_run(CHAR, save='save: EXCEPTION')
        self.assertFalse(result['success'])
        self.assertEqual(result['stages']['save'], 'error')

    def test_old_save_and_incomplete_done_checkpoint_do_not_settle_clock(self):
        result,_=self.fake_run(CHAR,save='save: saved (room-end save performed)')
        self.assertFalse(result['rewards_saved'])
        result,_=self.fake_run(CHAR,progress=dict(state='done',calls_done=9,calls_total=10,failed=0,skipped=0))
        self.assertFalse(result['rewards_saved'])

    def test_ingest_exit_propagates(self):
        spool = afk.SPOOL / 'test.ndjson'; spool.write_text('{}\n')
        with patch.dict(sys.modules, {'ingest_spool': SimpleNamespace(main=lambda argv: 2)}):
            self.assertEqual(afk.ingest_spool(spool, None), 2)

    def test_checkpoint_failure_sidecar_overrides_stale_progress(self):
        afk.write_json(afk.SESSIONS/'test.failure.json', dict(state='error',error='checkpoint write failed'))
        result,_ = self.fake_run(CHAR)
        self.assertFalse(result['success'])
        self.assertEqual(result['stages']['replay'],'error')

    def test_profile_context_ids_preserve_same_room_characters(self):
        p = self.profile()
        self.assertNotEqual(afk.profile_key(p), afk.profile_key(dict(p,character=dict(CHAR,slot=0))))
        self.assertNotEqual(afk.profile_key(p), afk.profile_key(dict(p,forgepact={'density':2})))
        self.assertNotEqual(afk.profile_key(p), afk.profile_key(dict(p,game_build='C')))
        self.assertEqual(afk.character_key(dict(CHAR,slot=20))[0],20)

    def test_build_profile_excludes_missing_and_incomplete_packets(self):
        recs = [dict(kind='session_start',build='B',character=CHAR,forgepact={})]
        recs += [dict(kind='kill',t=f'2026-09-18T00:0{i}:00Z',room='A',packet=h,kill_exp=10)
                 for i,h in enumerate(['good','incomplete','missing'])]
        meta = dict(deep=True,complete=True,monster_key='m',room='A',build='B',exp=10)
        with patch.object(afk,'read_ndjson',return_value=recs), patch.object(afk,'packet_index',return_value={
                'good':meta,'incomplete':dict(meta,complete=False)}):
            p = afk.build_profile(Path('memory'), 'A', False)[0]
        self.assertEqual(p['events_usable'],1)
        self.assertEqual(p['events_reassigned_incomplete'],0)
        self.assertEqual(p['events_orphaned_incomplete'],1)
        self.assertEqual(p['events_dropped_old_format'],1)
        self.assertEqual(p['coverage'],1/3)
        self.assertAlmostEqual(p['kills_per_min'],60/121)

    def test_settings_change_invalidates_session(self):
        with patch.object(afk,'read_ndjson',return_value=[{'kind':'context_invalid'}]):
            with self.assertRaises(SystemExit): afk.build_profile(Path('memory'),None,False)

    def test_ingest_cli_reports_unreadable_and_skipped_records(self):
        ingest_spec = importlib.util.spec_from_file_location('ingest_under_test',SOURCE.with_name('ingest_spool.py'))
        mod = importlib.util.module_from_spec(ingest_spec); ingest_spec.loader.exec_module(mod)
        path = afk.SPOOL/'bad.ndjson'; path.write_text('invalid JSON\n')
        self.assertNotEqual(mod.main(['ingest_spool.py',str(path)]),0)
        with patch.object(mod,'read_spool',return_value=({'x':[{}]},0,0)), patch.object(mod,'discover_editor',return_value='local'), \
             patch.object(mod,'ingest',return_value=dict(deposited=0,duplicate=0,skipped=[dict(seq=1,reason='bad item')],collections={})), \
             patch.object(mod,'get_json',return_value={'deposited':0}):
            self.assertNotEqual(mod.main(['ingest_spool.py',str(path)]),0)


if __name__ == '__main__': unittest.main()
