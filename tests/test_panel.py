import io,json,sys,tempfile,threading,time,unittest,urllib.request,urllib.error
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import panel,afk

class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'afk';self.root.mkdir()
    def good(self):
        return dict(room='Act_01_01',character=dict(identity_version=2,slot=1,name='Hero',**{'class':13}),
                    farm_context=dict(schema=1,hash='a'*64),rate_basis='farm-clock',forgepact={},coverage=1,basis_seconds=240,
                    reward_baseline=dict(schema=1,complete=True,magic_find=1000),kills=50,game_build='B',packets=[dict(kind='kill',rank=1)])
    def test_legacy_profile_cannot_be_selected(self):
        p=self.good();p['character'].pop('identity_version');self.assertTrue(panel.profile_problems(p))
    def test_context_clock_build_and_coverage_are_required(self):
        self.assertEqual(panel.profile_problems(self.good(),'B'),[])
        for key,value in [('farm_context',None),('rate_basis','wall'),('game_build','old'),('coverage',.94),('basis_seconds',59.999),('kills',29),('quality',dict(status='invalid'))]:
            p=self.good();p[key]=value
            with self.subTest(key=key):self.assertTrue(panel.profile_problems(p,'B'))
    def test_same_name_different_save_slots_are_distinct(self):
        c=self.good()['character'];self.assertFalse(panel.same_character(c,dict(c,slot=0)))
    def test_unsupported_activity_and_boss_packets_are_closed(self):
        for changes in [dict(room='Unstable_Rift_03_03'),dict(packets=[dict(kind='kill',rank=5)])]:
            p=self.good();p.update(changes);self.assertTrue(panel.profile_problems(p))
    def test_failure_overrides_done_progress(self):
        afk.write_json(self.root/'sessions/x.progress.json',dict(state='done',calls_done=100,calls_total=100))
        afk.write_json(self.root/'sessions/x.failure.json',dict(state='error',calls_done=30,calls_total=100,error='write failed'))
        result=panel.progress_view(self.root,'x');self.assertEqual(result['percent'],30);self.assertTrue(result['reconciliation_required'])
    def test_vault_failure_does_not_keep_saved_rewards_armed(self):
        self.assertTrue(afk.run_succeeded(dict(state='done',rewards_saved=True,success=False,stages=dict(ingest='error'))))
        self.assertFalse(afk.run_succeeded(dict(state='done',rewards_saved=False,success=False)))
    def test_missing_progress_does_not_mean_done(self):
        self.assertNotIn('state',panel.progress_view(self.root,'x'))
    def test_launch_skips_old_version_and_reuses_current_version(self):
        def instance(version):
            return io.BytesIO(json.dumps(dict(application='hero-siege-afk-farm',version=version)).encode())
        with patch.object(sys,'argv',['panel.py','--no-browser']),patch.object(urllib.request,'urlopen',side_effect=[instance('0.4.0'),instance(panel.VERSION)]) as request,patch.object(panel,'Panel',side_effect=AssertionError('Should reuse the current instance')):
            panel.main()
        self.assertEqual(request.call_count,2)
        self.assertIn(':8788/',request.call_args.args[0])
    def test_server_does_not_share_a_listening_port(self):
        server=panel.LocalPanelServer(('127.0.0.1',0),panel.Handler)
        self.addCleanup(server.server_close)
        with self.assertRaises(OSError):
            panel.LocalPanelServer(('127.0.0.1',server.server_port),panel.Handler)
    def test_changed_loadout_refuses_validation_but_never_a_claim(self):
        app=panel.Panel(self.root);p=self.good();s=dict(character=p['character'],game_build='B',farm_context=dict(hash='b'*64))
        with self.assertRaises(ValueError):app.context_matches(p,s)
        app.context_matches(p,s,loadout=False)
        with self.assertRaises(ValueError):app.context_matches(p,dict(s,game_build='other'),loadout=False)
        with self.assertRaises(ValueError):app.context_matches(p,dict(s,character=dict(p['character'],slot=9)),loadout=False)
    def test_profile_aliases_are_not_duplicated(self):
        p=self.good();afk.write_json(self.root/'profiles/Act_01_01.json',p);afk.write_json(self.root/'profiles/Act_01_01--alias.json',p)
        self.assertEqual(len(panel.Panel(self.root).profiles),1)
    def recording(self,seconds=47,kills=790):
        path=self.root/'sessions/current.ndjson';path.parent.mkdir(exist_ok=True)
        rows=[dict(kind='farm_clock',room='Act_01_01',seconds=min(1,seconds-i)) for i in range(int(seconds))]
        rows += [dict(kind='kill',room='Act_01_01',packet='p') for _ in range(kills)]
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        app=panel.Panel(self.root);app.calibration=dict(session=str(path),room='Act_01_01',character=self.good()['character'])
        app.live=dict(capture_on=True,capture_file=str(path));app.live_at=time.monotonic()
        app.job=dict(output='')
        return app,path
    def test_short_recording_is_explained_after_stop_and_after_panel_restart(self):
        app,path=self.recording();before=path.read_bytes()
        with patch.object(app,'cli'),patch.object(app,'fresh',side_effect=AssertionError('Saving must not depend on a live hero after capture stops')):
            with self.assertRaisesRegex(ValueError,'Recorded 47 of 60 required seconds and 790'):
                app.action('capture_stop',{})
        self.assertEqual(path.read_bytes(),before)
        result=app.capture_view();self.assertFalse(result['running']);self.assertEqual(result['outcome']['status'],'insufficient')
        self.assertEqual(result['outcome']['remaining_seconds'],13)
        restored=panel.Panel(self.root).capture_view();self.assertEqual(restored['outcome']['status'],'insufficient')
        self.assertEqual(list((self.root/'profiles').glob('*.json')),[])
    def test_zero_exit_without_a_new_profile_is_not_success_even_with_an_older_usable_profile(self):
        app,path=self.recording(240,50);old=dict(self.good(),session='previous.ndjson')
        afk.write_json(self.root/'profiles/previous.json',old)
        with patch.object(app,'cli'):
            with self.assertRaisesRegex(ValueError,'without a saved profile'):
                app.action('capture_stop',{})
        self.assertEqual(app.capture_view()['outcome']['status'],'unsaved')
    def test_success_requires_a_usable_profile_from_this_recording(self):
        app,path=self.recording(240,50)
        def cli(*args):
            if args[:2]==('profile','build'):
                afk.write_json(self.root/'profiles/new.json',dict(self.good(),session=str(path)))
        with patch.object(app,'cli',side_effect=cli):app.action('capture_stop',{})
        result=app.capture_view()['outcome'];self.assertEqual(result['status'],'saved');self.assertEqual(result['profile_id'],'new')
    def test_saved_but_ineligible_profile_does_not_report_calibration_success(self):
        app,path=self.recording(240,50)
        def cli(*args):
            if args[:2]==('profile','build'):
                afk.write_json(self.root/'profiles/rejected.json',dict(self.good(),session=str(path),coverage=.8))
        with patch.object(app,'cli',side_effect=cli):
            with self.assertRaisesRegex(ValueError,'coverage is below 95%'):app.action('capture_stop',{})
        self.assertEqual(app.capture_view()['outcome']['status'],'rejected')
    def test_minimum_readiness_changes_without_calling_it_saved(self):
        app,path=self.recording(60,30);result=app.capture_view()['outcome']
        self.assertTrue(result['minimum_ready']);self.assertEqual(result['status'],'ready')
        self.assertNotIn('profile_id',result)
    def test_one_minute_profile_passes_existing_integrity_guards(self):
        p=dict(self.good(),basis_seconds=60,kills=30,quality=dict(status='short'))
        self.assertEqual(panel.profile_problems(p,'B'),[])
        self.assertTrue(panel.profile_problems(dict(p,coverage=.94),'B'))
        self.assertTrue(panel.profile_problems(dict(p,reward_baseline=dict(complete=False)),'B'))
    def test_stop_without_profile_never_builds_even_if_minimum_is_reached_during_click(self):
        app,path=self.recording(240,50);before=path.read_bytes()
        with patch.object(app,'cli') as cli:app.action('capture_stop',dict(save_profile=False))
        self.assertEqual([call.args for call in cli.call_args_list],[('capture','auto','off'),('capture','off')])
        self.assertEqual(path.read_bytes(),before);self.assertEqual(app.capture_view()['outcome']['status'],'unsaved')
        self.assertIn('You stopped without saving',app.capture_view()['outcome']['message'])
    def test_validation_is_not_reported_as_a_new_calibration(self):
        app,path=self.recording(240,50);app.calibration['validation_reference']='reference.json'
        self.assertEqual(app.capture_view()['outcome']['status'],'validation')
    def start_offline(self,live):
        p=self.good();p.update(kills_per_min=12.5,breaks=0,breaks_per_min=0)
        p['packets']=[dict(kind='kill',rank=1,count=50,weight=1,hash='f'*64,monster_key='test',exp=10,native_exp=10)]
        afk.write_json(self.root/'profiles/example.json',p)
        paths={k:self.root/v for k,v in dict(DATA='',PACKETS='packets',SESSIONS='sessions',SPOOL='spool',PROFILES='profiles',PLANS='plans',STATE='state.json',CONFIG='config.json').items()}
        with patch.dict(afk.__dict__,paths),patch.object(panel,'characters',return_value=[p['character']]):
            app=panel.Panel(self.root);app.live=live
            def cli(*args):
                self.assertEqual(args[0],'start')
                afk.cmd_start(SimpleNamespace(plan=args[1]))
            with patch.object(app,'cli',side_effect=cli),patch.object(app,'fresh',side_effect=AssertionError('Offline start contacted the game')),patch.object(afk,'Ipc',side_effect=AssertionError('Offline start sent IPC')):
                app.action('start',dict(slot=1,profile='example',hours=.25))
            state=afk.load_state(self.root/'state.json');plan=afk.read_json(Path(afk.armed_list(state)[0]['plan']))
            self.assertEqual(plan['character'],p['character']);self.assertEqual(plan['farm_context'],p['farm_context'])
            self.assertEqual(plan['preview']['kills'],188)
            before=(self.root/'state.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'already active'):
                app.action('start',dict(slot=1,profile='example',hours=.25))
            self.assertEqual((self.root/'state.json').read_bytes(),before)
    def test_start_uses_saved_profile_with_game_closed(self):
        self.start_offline(None)
    def test_start_does_not_depend_on_other_live_character(self):
        self.start_offline(dict(character=dict(self.good()['character'],slot=5,name='Other'),room='Town_01_rm',replay_running=False))
    def test_claim_keeps_character_build_and_region_guards_but_not_the_loadout(self):
        p=self.good();p['zones']=[dict(room=p['room'])]
        afk.write_json(self.root/'plans/example.json',p)
        afk.write_json(self.root/'state.json',dict(armed=dict(expedition_id='example',plan=str(self.root/'plans/example.json'))))
        base=dict(character=p['character'],game_build='B',farm_context=p['farm_context'],room=p['room'])
        for changes in (dict(character=dict(p['character'],slot=5)),dict(room='Town_01_rm'),dict(game_build='old')):
            app=panel.Panel(self.root)
            with self.subTest(changes=changes),patch.object(app,'fresh',return_value=dict(base,**changes)),patch.object(app,'cli') as cli,patch.object(panel.recovery,'inspect',return_value=dict(recoverable=False,status='not_started')):
                with self.assertRaises(ValueError):app.action('claim',{})
                cli.assert_not_called()
        app=panel.Panel(self.root)
        with patch.object(app,'fresh',return_value=dict(base,farm_context=dict(hash='b'*64))),patch.object(app,'cli') as cli,             patch.object(panel.recovery,'inspect',return_value=dict(recoverable=False,status='not_started')):
            app.action('claim',{})
        self.assertEqual(cli.call_args.args[0],'claim','a changed loadout, level or setting never blocks a claim')
    def test_capture_cache_invalidates_on_file_change_and_freshness(self):
        app=panel.Panel(self.root);p=self.root/'sessions/capture.ndjson';p.parent.mkdir()
        p.write_text(json.dumps(dict(kind='farm_clock',room='Act_01_01',seconds=5))+'\n',encoding='utf-8')
        app.calibration=dict(session=str(p),room='Act_01_01',character=self.good()['character'])
        app.live=dict(capture_on=True,capture_file=str(p),character=self.good()['character']);app.live_at=time.monotonic()
        with patch.object(afk,'read_ndjson',wraps=afk.read_ndjson) as read_capture:
            self.assertEqual(app.capture_view()['seconds'],5)
            self.assertTrue(app.capture_view()['running']);self.assertEqual(read_capture.call_count,1)
            with p.open('a',encoding='utf-8') as stream:stream.write(json.dumps(dict(kind='kill',room='Act_01_01',packet='p'))+'\n')
            self.assertEqual(app.capture_view()['kills'],1);self.assertEqual(read_capture.call_count,2)
            app.live_at=0
            self.assertFalse(app.capture_view()['running']);self.assertEqual(read_capture.call_count,2)
    def test_second_job_cannot_race_first(self):
        app=panel.Panel(self.root);app.job_lock.acquire()
        with self.assertRaises(ValueError):app.submit('claim',{})
        app.job_lock.release()
    def test_local_action_does_not_wait_for_game_monitor(self):
        app=panel.Panel(self.root);app.lock.acquire();finished=threading.Event()
        try:
            with patch.object(app,'action',side_effect=lambda *args:finished.set()):
                self.assertTrue(app.submit('start',{})['accepted'])
                self.assertTrue(finished.wait(2),'Local clock waited for the game connection')
        finally:app.lock.release()
        self.assertTrue(app.job_lock.acquire(timeout=2));app.job_lock.release()
    def test_recording_another_hero_does_not_cancel_active_expedition(self):
        app=panel.Panel(self.root);hero=self.good()['character']
        afk.write_json(self.root/'state.json',dict(armed=dict(expedition_id='other_hero')))
        before=(self.root/'state.json').read_bytes()
        live=dict(character=hero,room='Act_01_01',replay_running=False,capture_on=False,reward_baseline=dict(available=True))
        started=dict(live,capture_on=True,capture_file=str(self.root/'sessions/new.ndjson'),captured_at='now')
        with patch.object(app,'fresh',side_effect=[live,started]),patch.object(app,'selected',return_value=hero),patch.object(app,'cli') as cli:
            app.action('capture_start',{})
            self.assertEqual(cli.call_args.args,('capture','on'))
        self.assertEqual((self.root/'state.json').read_bytes(),before)
    def test_http_origin_host_and_token_guards(self):
        app=panel.Panel(self.root);server=panel.ThreadingHTTPServer(('127.0.0.1',0),panel.Handler);server.app=app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        with urllib.request.urlopen(base) as response:self.assertIn(app.token,response.read().decode())
        for headers in [{},{'X-AFK-Token':app.token,'Origin':'https://example.com'},{'X-AFK-Token':app.token,'Host':'untrusted.example'}]:
            request=urllib.request.Request(base+'/api/action',data=b'{"action":"claim"}',headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,403)
        with patch.object(app,'submit',return_value={'accepted':True}) as submit:
            request=urllib.request.Request(base+'/api/action',data=b'{"action":"claim"}',headers={'X-AFK-Token':app.token,'Origin':base})
            with urllib.request.urlopen(request) as response:self.assertEqual(response.status,202)
            submit.assert_called_once_with('claim',{})

class FarmClockTests(unittest.TestCase):
    def test_clock_excludes_town_and_keeps_walking(self):
        records=[dict(kind='session_start',build='B',character={},forgepact={},farm_context=dict(schema=1,hash='a'*64)),
                 dict(kind='kill',room='Act_01_01',packet='p',t='2026-09-21T00:00:01Z'),
                 dict(kind='kill',room='Act_01_01',packet='p',t='2026-09-21T00:05:01Z'),
                 dict(kind='farm_clock',room='Act_01_01',seconds=120),dict(kind='farm_clock',room='Town_01_rm',seconds=180)]
        packet=dict(deep=True,complete=True,build='B',room='Act_01_01',exp=10,monster_key='monster')
        with patch.object(afk,'read_ndjson',return_value=records),patch.object(afk,'packet_index',return_value={'p':packet}):
            p=afk.build_profile(Path('unused'),None,False)[0]
        self.assertEqual(p['rate_basis'],'farm-clock');self.assertEqual(p['basis_seconds'],120);self.assertEqual(p['kills_per_min'],1)
    def test_changed_context_invalidates_entire_capture(self):
        with patch.object(afk,'read_ndjson',return_value=[dict(kind='context_invalid')]):
            with self.assertRaises(SystemExit):afk.build_profile(Path('unused'),None,False)

if __name__=='__main__':unittest.main()
