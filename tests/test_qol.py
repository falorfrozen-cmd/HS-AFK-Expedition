"""0.6 quality-of-life: Vault transfer filter, pause/continue, delivery speed and
estimates, background claim, repeat, loadout warning and the expedition summary.
Only temporary data folders are used; no game or Item Editor is contacted.
"""
import hashlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import afk,ingest_spool,loot_filter,panel,product_data,recovery

HERO=dict(identity_version=2,slot=1,name='Hero',**{'class':13})
SAVED='saved (character and account save performed)'


def item(seq,tier,type_=3,visible=True,name=None):
    return dict(expedition_id='e_claim',seq=seq,kind='item',type=type_,name=name or f'Item {seq}',filter_visible=visible,
                item=dict(itemType=float(type_),itemInfoStruct={'27':tier,'28':name or f'Item {seq}'}))


class LootFilterTests(unittest.TestCase):
    def test_defaults_reproduce_the_game_filter_only(self):
        s=loot_filter.normalize()
        self.assertTrue(s['respect_game_filter']);self.assertTrue(s['keep_stackables'])
        self.assertTrue(all(s['rarities'][r] for r in loot_filter.GEAR_RARITIES))
        self.assertIsNone(loot_filter.decide(item(1,6),s))
        self.assertEqual(loot_filter.decide(item(2,6,visible=False),s),'game_filter')
    def test_rarity_and_stackable_choices(self):
        s=loot_filter.normalize(dict(rarities=dict(Satanic=False,Other=False),keep_stackables=True))
        self.assertEqual(loot_filter.decide(item(1,6),s),'rarity')
        self.assertIsNone(loot_filter.decide(item(2,9),s))
        self.assertEqual(loot_filter.decide(item(3,1),s),'rarity')
        self.assertIsNone(loot_filter.decide(item(4,1,type_=12),s),'keys are kept whatever their tier')
        s=loot_filter.normalize(dict(keep_stackables=False,respect_game_filter=False))
        self.assertEqual(loot_filter.decide(item(5,1,type_=14),s),'stackables')
        self.assertIsNone(loot_filter.decide(item(6,6,visible=False),s))
    def test_invalid_settings_are_refused(self):
        for bad in (dict(rarities=dict(Legendary=True)),dict(keep_stackables='yes'),dict(schema=2),dict(extra=1),dict(rarities=dict(Heroic=1))):
            with self.subTest(bad=bad),self.assertRaises(ValueError):loot_filter.normalize(bad)
    def test_read_spool_orders_best_first_and_counts_what_stays(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'e_claim.ndjson'
            rows=[item(1,6),item(2,1,type_=12),item(3,9),item(4,6,visible=False),item(5,10),item(6,4),item(7,6)]
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows)+json.dumps(dict(kind='summary',seq=8))+'\n')
            left={}
            groups,unreadable,filtered=ingest_spool.read_spool(path,settings=loot_filter.normalize(dict(rarities=dict(Set=False))),left_out=left)
            self.assertEqual((unreadable,filtered,left),(0,1,{'rarity':1}))
            self.assertEqual([r['seq'] for r in groups['e_claim']],[5,3,1,7,2],'Unholy, Heroic, Satanic in spool order, keys last')
            groups,_,filtered=ingest_spool.read_spool(path)
            self.assertEqual((len(groups['e_claim']),filtered),(6,1),'without a filter the old behaviour is unchanged')


class PauseAndResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
        self.path=self.d/'plans/e_claim.json';afk.write_json(self.path,dict(expedition_id='e_claim',hours=1,packets=[dict(count=10)]))
        self.progress=dict(expedition_id='e_claim',checkpoint_version=2,state='aborted',plan_hash=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                           calls_done=4,calls_total=10,failed=0,skipped=0,save_committed=True,saved=SAVED)
        self.write()
    def write(self,**changes):afk.write_json(self.d/'sessions/e_claim.progress.json',dict(self.progress,**changes))
    def test_saved_pause_and_saved_abort_can_continue(self):
        self.assertTrue(recovery.resumable(self.d,'e_claim'))
        self.write(state='paused');self.assertTrue(recovery.resumable(self.d,'e_claim'))
        review=recovery.inspect(self.d,'e_claim')
        self.assertEqual((review['status'],review['resumable'],review['recoverable']),('paused',True,False))
        self.assertTrue(panel.progress_view(self.d,'e_claim')['resumable'])
        self.assertFalse(panel.progress_view(self.d,'e_claim').get('reconciliation_required'))
    def test_unsaved_running_failed_or_changed_pauses_still_need_review(self):
        for changes in (dict(state='running'),dict(state='paused',save_committed=False,saved=''),dict(state='error'),dict(failed=1),
                        dict(plan_hash='x'*64),dict(calls_done=10),dict(saved='saved (room-end save performed)'),dict(checkpoint_version=1)):
            self.write(**changes)
            with self.subTest(changes=changes):
                self.assertFalse(recovery.resumable(self.d,'e_claim'))
                self.assertNotEqual(recovery.inspect(self.d,'e_claim')['status'],'paused')
        self.write();afk.write_json(self.d/'sessions/e_claim.failure.json',{})
        self.assertFalse(recovery.resumable(self.d,'e_claim'))
    def test_paused_run_keeps_the_clock_armed_without_a_result_file(self):
        sessions=self.d/'sessions'
        with patch.object(afk,'DATA',self.d),patch.object(afk,'SESSIONS',sessions),patch.object(afk,'Ipc') as ipc,\
             patch.object(afk,'forgepact_current',return_value={}),patch.object(afk,'forgepact_text',return_value=''):
            session=ipc.return_value
            session.alive.return_value=True
            def send(line,timeout=30):
                if line=='afk who':return ['who: '+json.dumps(HERO)]
                if line=='afk status':return ['build id: B','room: Act_01_01']
                if line.startswith('afk expedition start'):return ['expedition e_claim: resumed at 4/10 calls']
                raise AssertionError('no save or further command after a pause: '+line)
            session.send.side_effect=send
            plan=dict(expedition_id='e_claim',hours=1,character=HERO,game_build='B',forgepact={},zones=[dict(room='Act_01_01')],packets=[dict(count=10)],reward_modifiers=None)
            with patch.object(afk,'require_reward_plan'):
                result=afk.run_plan(self.path,plan,self.d,ingest=True)
        self.assertTrue(result['paused']);self.assertTrue(result['resumable'])
        self.assertFalse((sessions/'e_claim.result.json').exists())
    def test_panel_pause_runs_beside_a_claim_job(self):
        afk.write_json(self.d/'state.json',dict(armed=dict(expedition_id='e',plan=str(self.path))))
        afk.write_json(self.d/'sessions/e_claim.progress.json',dict(self.progress,state='running'))
        app=panel.Panel(self.d);app.job_lock.acquire()
        try:
            with patch.object(panel.subprocess,'run') as run:
                run.return_value.stdout='expedition e_claim: aborted calls=4/10';run.return_value.stderr=''
                self.assertTrue(app.submit('pause_delivery',{})['accepted'])
                deadline=time.monotonic()+2
                while app.pause_lock.locked() and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(run.call_args.args[0][-1],'pause')
            with self.assertRaises(ValueError):app.submit('claim',{})
        finally:app.job_lock.release()
    def test_claim_continues_a_saved_pause_and_refuses_an_unsaved_one(self):
        afk.write_json(self.d/'state.json',dict(armed=dict(expedition_id='e',plan=str(self.d/'plans/e.json'))))
        plan=dict(zones=[dict(room='Act_01_01')],character=HERO,game_build='B',farm_context=dict(schema=1,hash='a'*64))
        afk.write_json(self.d/'plans/e.json',plan)
        live=dict(character=HERO,game_build='B',farm_context=plan['farm_context'],room='Act_01_01')
        app=panel.Panel(self.d)
        with patch.object(app,'fresh',return_value=live),patch.object(app,'cli') as cli:
            app.action('claim',dict(speed='fast'))
            self.assertEqual(cli.call_args.args,('claim','--speed','fast'))
            self.write(state='paused',save_committed=False,saved='');cli.reset_mock()
            with self.assertRaises(ValueError):app.action('claim',{})
            cli.assert_not_called()


class SettlePartialTests(unittest.TestCase):
    """An interrupted claim that needs review can be closed, keeping what was delivered."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
        self.path=self.d/'plans/e_claim.json';afk.write_json(self.path,dict(expedition_id='e_claim',hours=2,scale=1,packets=[dict(count=100)]))
        afk.write_json(self.d/'state.json',dict(armed=dict(expedition_id='e',plan=str(self.d/'plans/e.json'))))
        self.progress=dict(expedition_id='e_claim',checkpoint_version=2,state='running',plan_hash=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                           calls_done=80,calls_total=100,failed=0,skipped=0,save_committed=False,saved='',items=5)
        afk.write_json(self.d/'sessions/e_claim.progress.json',self.progress)
        (self.d/'spool').mkdir();(self.d/'spool/e_claim.ndjson').write_text(json.dumps(item(1,6))+'\n')
        self.age()
    def age(self):
        old=time.time()-120;os.utime(self.d/'sessions/e_claim.progress.json',(old,old))
    def test_closing_keeps_records_frees_the_clock_and_marks_the_result_partial(self):
        before=(self.d/'spool/e_claim.ndjson').read_bytes()
        result=recovery.settle_partial(self.d,'e_claim')
        self.assertEqual((result['partial'],result['rewards_saved'],result['settled_by'],result['stages']['replay']),(True,False,'player','partial'))
        self.assertEqual((result['state'],result['checkpoint_state'],result['calls_done']),('partial','running',80))
        state=recovery.read(self.d/'state.json')
        self.assertNotIn('armed',state);self.assertTrue(state['last_claim']['partial'])
        self.assertAlmostEqual(state['last_claim']['credited_hours'],1.6)
        self.assertEqual((self.d/'spool/e_claim.ndjson').read_bytes(),before)
        self.assertEqual(recovery.read(self.d/'sessions/e_claim.progress.json')['state'],'running','the checkpoint is not rewritten')
    def test_only_a_claim_that_needs_review_and_is_not_live(self):
        afk.write_json(self.d/'sessions/e_claim.progress.json',dict(self.progress,state='aborted',save_committed=True,saved=SAVED));self.age()
        with self.assertRaises(ValueError,msg='a saved pause continues instead'):recovery.settle_partial(self.d,'e_claim')
        afk.write_json(self.d/'sessions/e_claim.progress.json',self.progress)
        with self.assertRaises(ValueError,msg='a checkpoint written seconds ago may still be delivering'):recovery.settle_partial(self.d,'e_claim')
        self.age();afk.write_json(self.d/'state.json',{})
        with self.assertRaises(ValueError,msg='only the armed claim'):recovery.settle_partial(self.d,'e_claim')
        self.assertFalse((self.d/'sessions/e_claim.result.json').exists())
    def test_panel_refuses_while_the_game_is_delivering(self):
        app=panel.Panel(self.d)
        with patch.object(app,'current_live',return_value=dict(replay_running=True)),self.assertRaises(ValueError):
            app.action('settle_partial',{})
        self.assertIn('armed',recovery.read(self.d/'state.json'))
    def test_panel_transfers_a_claim_the_player_closed_as_partial(self):
        recovery.settle_partial(self.d,'e_claim')
        app=panel.Panel(self.d);app.job=dict(output='')
        with patch.object(app,'cli') as cli,patch.object(panel.recovery,'data_lock'):
            app.action('ingest',dict(id='e_claim'))
        self.assertEqual(cli.call_args.args[:2],('ingest',self.d/'spool/e_claim.ndjson'))


class ContinueFromRecordedPositionTests(unittest.TestCase):
    """A delivery a crash cut short continues only from a position its item
    records end at exactly, and only after the player accepts it."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
        recovery._SPOOL_CHECKS.clear()
        self.path=self.d/'plans/e_claim.json';afk.write_json(self.path,dict(expedition_id='e_claim',hours=2,scale=1,packets=[dict(count=100)]))
        afk.write_json(self.d/'state.json',dict(armed=dict(expedition_id='e',plan=str(self.d/'plans/e.json'))))
        self.progress=dict(expedition_id='e_claim',checkpoint_version=2,state='running',plan_hash=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                           calls_done=80,calls_total=100,failed=0,skipped=0,save_committed=False,saved='',items=3)
        (self.d/'spool').mkdir();self.spool(3);self.write()
    def write(self,**changes):
        afk.write_json(self.d/'sessions/e_claim.progress.json',dict(self.progress,**changes))
        old=time.time()-120;os.utime(self.d/'sessions/e_claim.progress.json',(old,old))
    def spool(self,count,tail=''):
        recovery._SPOOL_CHECKS.clear()
        (self.d/'spool/e_claim.ndjson').write_text(''.join(json.dumps(item(n,6))+'\n' for n in range(1,count+1))+tail)
    def test_matching_records_continue_only_after_the_player_accepts(self):
        review=recovery.inspect(self.d,'e_claim')
        self.assertEqual((review['status'],review['continuable'],review['continue_blockers']),('needs_review',True,[]))
        self.assertFalse(recovery.resumable(self.d,'e_claim'),'never without the player')
        accepted=recovery.accept_position(self.d,'e_claim')
        self.assertEqual((accepted['resume_accepted'],accepted['resume_accepted_by'],accepted['resume_basis']),(True,'player',dict(calls_done=80,items=3,spool_bytes=None,set_aside=None,set_aside_records=0)))
        self.assertEqual(accepted['state'],'running','the checkpoint position and state are kept')
        self.assertTrue(recovery.resumable(self.d,'e_claim'))
        review=recovery.inspect(self.d,'e_claim')
        self.assertEqual((review['status'],review['resumable'],review['accepted']),('paused',True,True))
        self.assertIn('recorded position',review['reasons'][0])
        view=panel.progress_view(self.d,'e_claim')
        self.assertTrue(view['resumable']);self.assertTrue(view['resume_accepted']);self.assertFalse(view.get('reconciliation_required'))
    def test_records_that_do_not_end_at_the_checkpoint_refuse(self):
        seq_gap=json.dumps(dict(item(5,6)))+'\n'
        cases=[('ahead',lambda:self.spool(4),'hold 4 items but the checkpoint counts 3'),
               ('behind',lambda:self.spool(2),'hold 2 items but the checkpoint counts 3'),
               ('summary',lambda:self.spool(3,json.dumps(dict(expedition_id='e_claim',seq=4,kind='summary'))+'\n'),'already end with a summary'),
               ('gap',lambda:self.spool(2,seq_gap),'gap or a duplicate'),
               ('unreadable',lambda:self.spool(3,'{not json\n'),'unreadable'),
               ('error state',lambda:self.write(state='error'),'stopped while running'),
               ('failed calls',lambda:self.write(failed=2),'failed or were skipped'),
               ('changed plan',lambda:self.write(plan_hash='x'*64),'plan differs'),
               ('failure record',lambda:afk.write_json(self.d/'sessions/e_claim.failure.json',{}),'failure record')]
        for label,apply,reason in cases:
            with self.subTest(label):
                self.setUp();apply()
                allowed,reasons=recovery.continuable(self.d,'e_claim')
                self.assertFalse(allowed);self.assertIn(reason,' '.join(reasons))
                with self.assertRaises(ValueError):recovery.accept_position(self.d,'e_claim')
                self.assertNotIn('resume_accepted',recovery.read(self.d/'sessions/e_claim.progress.json') or {})
    def test_records_written_after_the_checkpoint_are_set_aside_before_continuing(self):
        spool=self.d/'spool/e_claim.ndjson';size=spool.stat().st_size
        tail=json.dumps(item(4,6))+'\n'+'{"expedition_id":"e_claim","seq":5,"ki'
        with spool.open('a',encoding='utf-8') as stream:stream.write(tail)
        self.write(spool_bytes=size);recovery._SPOOL_CHECKS.clear()
        review=recovery.inspect(self.d,'e_claim')
        self.assertEqual((review['continuable'],review['records_after_checkpoint']),(True,2),'a torn last line is part of the tail')
        self.assertFalse(recovery.resumable(self.d,'e_claim'))
        accepted=recovery.accept_position(self.d,'e_claim')
        self.assertEqual(spool.stat().st_size,size,'the spool ends at the checkpoint again')
        basis=accepted['resume_basis']
        self.assertEqual((basis['spool_bytes'],basis['set_aside_records']),(size,2))
        self.assertEqual(Path(basis['set_aside']).read_text(encoding='utf-8'),tail,'nothing is lost')
        self.assertEqual(Path(basis['set_aside']).parent.name,'set-aside')
        self.assertTrue(recovery.resumable(self.d,'e_claim'))
    def test_a_checkpoint_size_outside_the_records_refuses(self):
        size=(self.d/'spool/e_claim.ndjson').stat().st_size
        for limit,reason in ((size+10,'shorter than the checkpoint'),(size-5,'record boundary'),(-1,'spool size is invalid')):
            with self.subTest(limit=limit):
                self.write(spool_bytes=limit);recovery._SPOOL_CHECKS.clear()
                allowed,reasons=recovery.continuable(self.d,'e_claim')
                self.assertFalse(allowed);self.assertIn(reason,' '.join(reasons))
    def test_pause_markers_of_an_earlier_resume_are_allowed(self):
        rows=[item(1,6),item(2,6),dict(expedition_id='e_claim',seq=3,kind='partial',calls=40),item(4,6)]
        recovery._SPOOL_CHECKS.clear()
        (self.d/'spool/e_claim.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.assertEqual(recovery.continuable(self.d,'e_claim'),(True,[]))
    def test_accepting_refuses_a_live_checkpoint_and_another_claim(self):
        afk.write_json(self.d/'sessions/e_claim.progress.json',self.progress)
        with self.assertRaises(ValueError):recovery.accept_position(self.d,'e_claim')
        self.write();afk.write_json(self.d/'state.json',{})
        with self.assertRaises(ValueError):recovery.accept_position(self.d,'e_claim')
    def test_records_that_change_after_acceptance_stop_the_continue(self):
        recovery.accept_position(self.d,'e_claim')
        self.spool(4)
        self.assertFalse(recovery.resumable(self.d,'e_claim'))
        self.assertEqual(recovery.inspect(self.d,'e_claim')['status'],'needs_review')
    def test_panel_accepts_only_while_the_game_is_not_delivering(self):
        app=panel.Panel(self.d);app.job=dict(output='')
        with patch.object(app,'current_live',return_value=dict(replay_running=True)),self.assertRaises(ValueError):
            app.action('accept_position',{})
        with patch.object(app,'current_live',return_value=None):app.action('accept_position',{})
        self.assertTrue(recovery.read(self.d/'sessions/e_claim.progress.json')['resume_accepted'])
    def test_an_accepted_position_can_still_be_closed_as_partial(self):
        recovery.accept_position(self.d,'e_claim');self.write(**dict(recovery.read(self.d/'sessions/e_claim.progress.json')))
        result=recovery.settle_partial(self.d,'e_claim')
        self.assertTrue(result['partial']);self.assertNotIn('armed',recovery.read(self.d/'state.json'))


class RegionComparisonTests(unittest.TestCase):
    """Calibrated regions of one hero side by side: calibration pace and XP,
    delivered gold per hour at x1 and item rarities per hour."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
        self.app=panel.Panel(self.d)
        other=dict(HERO,slot=4,name='Other')
        self.app.profiles=[
            dict(id='desert_old',room='Act_03_03',character=HERO,usable=True,built_at='2026-09-01',kills_per_min=100.0,exp_per_min=1000.0),
            dict(id='desert_new',room='Act_03_03',character=HERO,usable=True,built_at='2026-09-20',kills_per_min=200.0,exp_per_min=5000.0),
            dict(id='glacier',room='Act_02_05',character=HERO,usable=False,built_at='2026-09-21',kills_per_min=700.0,exp_per_min=90000.0),
            dict(id='elsewhere',room='Act_01_01',character=other,usable=True,built_at='2026-09-21',kills_per_min=50.0,exp_per_min=10.0)]
    def claim(self,ident,room,hours,scale,gold,tiers,mods=None,**result):
        afk.write_json(self.d/'plans'/f'{ident}.json',dict(expedition_id=ident,panel_version='0.6.0',character=HERO,zones=[dict(room=room)],
                                                          hours=hours,scale=scale,reward_modifiers=mods))
        afk.write_json(self.d/'sessions'/f'{ident}.result.json',dict(dict(rewards_saved=True,gold=gold),**result))
        (self.d/'spool').mkdir(exist_ok=True)
        (self.d/'spool'/f'{ident}.ndjson').write_text(''.join(json.dumps(dict(item(n,t),expedition_id=ident))+'\n' for n,t in enumerate(tiers,1)))
    def test_rows_per_region_with_gold_at_x1_and_rarities_per_hour(self):
        self.claim('a_claim','Act_03_03',2,0.5,1000,[10,7,9,9,6,6,6,1],mods=dict(gold=5.0,magic_find=10.0))
        self.claim('b_claim','Act_03_03',1,1,300,[6])
        self.claim('c_claim','Act_02_05',2,1,8000,[6,6],rewards_saved=False,partial=True,calls_done=50,calls_total=100)
        self.claim('d_claim','Act_02_05',1,1,999,[10],rewards_saved=False)
        mine=next(e for e in self.app.regions_view() if e['character']==HERO)
        self.assertEqual([r['room'] for r in mine['rows']],['Act_02_05','Act_03_03'],'highest calibration XP per hour first')
        glacier,desert=mine['rows']
        self.assertEqual((desert['profile'],desert['usable'],desert['xp_per_hour'],desert['expeditions'],desert['hours']),('desert_new',True,300000.0,2,2.0))
        self.assertAlmostEqual(desert['gold_per_hour'],(1000/5+300)/2)
        self.assertEqual(desert['rarities_per_hour'],dict(Unholy=.5,Angelic=.5,Heroic=1.0,Satanic=2.0))
        self.assertEqual(desert['magic_find'],[1.0,10.0])
        self.assertEqual((glacier['usable'],glacier['expeditions'],glacier['hours'],glacier['gold_per_hour']),(False,1,1.0,8000.0),
                         'a partial claim counts its delivered share; an unsaved one does not count')
        self.assertEqual(len(self.app.regions_view()),2,'one entry per hero')
    def test_calibration_only_regions_have_no_expedition_numbers(self):
        row=next(e for e in self.app.regions_view() if e['character']==HERO)['rows'][1]
        self.assertEqual((row['gold_per_hour'],row['rarities_per_hour'],row['expeditions']),(None,{},0))


class DeliverySpeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.rates=Path(self.tmp.name)/'delivery-rate.json'
        patcher=patch.object(afk,'RATE_FILE',self.rates);patcher.start();self.addCleanup(patcher.stop)
    def test_speeds_set_the_frame_budget_and_estimates_learn_this_computer(self):
        plan=dict(packets=[dict(count=6000)],per_frame=40)
        for speed,(per_frame,budget) in afk.DELIVERY_SPEEDS.items():
            afk.apply_delivery_speed(plan,speed)
            self.assertEqual((plan['per_frame'],plan['frame_budget_ms'],afk.delivery_speed(plan)),(per_frame,budget,speed))
        self.assertAlmostEqual(afk.estimate_delivery_seconds(6000,'normal'),6000/afk.DEFAULT_CALL_RATES['normal'])
        afk.record_delivery_rate('normal',6000,60)
        self.assertEqual(afk.learned_call_rate('normal'),100)
        afk.record_delivery_rate('normal',6000,120)
        self.assertEqual(afk.learned_call_rate('normal'),75,'smoothed with the previous run')
        afk.record_delivery_rate('normal',100,1)
        self.assertEqual(afk.learned_call_rate('normal'),75,'too short a run is ignored')
        with self.assertRaises(SystemExit):afk.apply_delivery_speed(plan,'warp')
    def test_preview_uses_the_measured_rate_instead_of_frames(self):
        plan=dict(packets=[dict(hash='h',count=102006,kind='kill',room='Act_02_05')],per_frame=40,zones=[dict(room='Act_02_05')])
        afk.rebuild_preview(plan)
        self.assertAlmostEqual(plan['preview']['seconds_to_replay'],102006/50,places=0)
        self.assertGreater(plan['preview']['seconds_to_replay'],600,'the old 40-calls-per-frame guess said 42.5 s')


class PanelQolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)/'afk';self.d.mkdir()
    def profile(self,**changes):
        return dict(dict(room='Act_01_01',character=HERO,farm_context=dict(schema=1,hash='a'*64,inputs=dict(level=1,forgepact={})),rate_basis='farm-clock',
                    forgepact={},coverage=1,basis_seconds=240,reward_baseline=dict(schema=1,complete=True,magic_find=1000),kills=50,
                    game_build='B',packets=[dict(kind='kill',rank=1)]),**changes)
    def test_settings_actions_save_filter_and_speed(self):
        app=panel.Panel(self.d);app.job=dict(output='')
        app.action('save_loot_filter',dict(settings=dict(rarities=dict(Satanic=False))))
        self.assertFalse(loot_filter.load(self.d/'loot-filter.json')['rarities']['Satanic'])
        app.action('save_preferences',dict(delivery_speed='max'))
        self.assertEqual(panel.load_preferences(self.d)['delivery_speed'],'max')
        with self.assertRaises(ValueError):app.action('save_preferences',dict(delivery_speed='warp'))
        with self.assertRaises(ValueError):app.action('save_loot_filter',dict(settings=dict(rarities=dict(Mythic=True))))
    def test_repeat_offers_the_last_settled_expedition(self):
        afk.write_json(self.d/'profiles/Act_01_01.json',self.profile())
        afk.write_json(self.d/'plans/farm_1.json',dict(zones=[dict(room='Act_01_01')],character=HERO,hours=2))
        state=dict(last_claim=dict(expedition_id='farm_1_claim'))
        app=panel.Panel(self.d)
        view=app.repeat_view(state,None)
        self.assertEqual((view['room'],view['hours'],view['slot'],view['reason']),('Act_01_01',2.0,1,None))
        self.assertTrue(view['profile'])
        self.assertIsNone(app.repeat_view(state,dict(expedition_id='busy')),'no repeat while an expedition is armed')
    def test_loadout_warning_compares_the_loaded_hero_only(self):
        afk.write_json(self.d/'profiles/Act_01_01.json',self.profile())
        app=panel.Panel(self.d);pid=app.profiles[0]['id']
        same=dict(character=HERO,farm_context=self.profile()['farm_context'])
        changed=dict(character=HERO,farm_context=dict(schema=1,hash='b'*64,inputs=dict(level=2,forgepact={})))
        self.assertEqual(app.live_matches(same),{pid:True})
        self.assertEqual(app.live_matches(changed),{pid:False})
        self.assertEqual(app.live_matches(dict(changed,character=dict(HERO,slot=4))),{})
        self.assertEqual(app.live_matches(None),{})
    def test_background_claim_needs_verified_setup_and_a_free_game(self):
        afk.write_json(self.d/'plans/e.json',dict(character=HERO,zones=[dict(room='Act_01_01')]))
        armed=dict(expedition_id='e',plan=str(self.d/'plans/e.json'))
        app=panel.Panel(self.d)
        ready=dict(verified_build=True,plugin_current=True)
        with patch.object(app,'installation',return_value=ready):
            self.assertTrue(app.background_view(armed,{},dict(status='not_started'),None)['available'])
            app.game_running=True
            other=dict(character=dict(HERO,slot=5,name='Other'),room='Town_01_rm')
            self.assertIn('Another hero',app.background_view(armed,{},dict(status='not_started'),other)['reason'])
            menu=dict(character=None,room='Main_Menu_rm')
            self.assertTrue(app.background_view(armed,{},dict(status='not_started'),menu)['available'])
            self.assertFalse(app.background_view(armed,{},dict(status='needs_review'),None)['available'])
            self.assertFalse(app.background_view(armed,dict(state='running'),dict(status='not_started'),None)['available'])
        with patch.object(app,'installation',return_value=dict(ready,verified_build=False)):
            self.assertIn('verified',app.background_view(armed,{},dict(status='not_started'),None)['reason'])
    def test_delivery_view_estimates_remaining_calls(self):
        app=panel.Panel(self.d)
        with patch.object(afk,'RATE_FILE',self.d/'delivery-rate.json'):
            view=app.delivery_view(dict(expedition_id='e'),None,dict(calls_total=10000,calls_done=5000))
        self.assertEqual(view['calls'],5000)
        normal=next(s for s in view['speeds'] if s['id']=='normal')
        self.assertEqual(normal['seconds'],round(5000/afk.DEFAULT_CALL_RATES['normal']))
    def test_expedition_label_names_region_and_duration(self):
        self.assertEqual(panel.expedition_label('Act_01_01',2.0),'Outskirts of Inoya · 2 h')
        self.assertEqual(panel.expedition_label('Unknown_rm',.5),'Unknown_rm · 30 min')
        self.assertEqual(panel.expedition_label('Act_01_01',1.25,'Suh'),'Suh · Outskirts of Inoya · 1.25 h')
        self.assertEqual(afk.expedition_label('Suh','The Glacial Trail',0.4972),'Suh · The Glacial Trail · 30 min')


class SummaryTests(unittest.TestCase):
    def test_loot_summary_lists_best_visible_drops_by_rarity(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);(data/'spool').mkdir()
            rows=[item(1,6,name='Sword'),item(2,9,name='Crown'),item(3,6,name='Sword'),item(4,10,visible=False,name='Hidden'),
                  item(5,1,type_=12,name='Key'),item(6,7,name='Halo')]
            (data/'spool/e_claim.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            presentation=product_data.Presentation(Path(__file__).resolve().parents[1])
            loot=presentation.loot(data,'e_claim')
            self.assertIn(('loot','e_claim'),presentation.cache,'the summary must not overwrite the cache key')
            self.assertIs(presentation.loot(data,'e_claim'),loot)
        self.assertEqual([b['name'] for b in loot['best']],['Halo','Crown','Sword'])
        self.assertEqual(loot['best'][2]['count'],2)
        self.assertEqual(loot['stackables'],1)
        self.assertEqual(loot['visible_rarities'],{'Satanic':2,'Heroic':1,'Angelic':1},'keys count only as keys and materials')
        self.assertEqual(loot['filtered'],1)


if __name__=='__main__':unittest.main()
