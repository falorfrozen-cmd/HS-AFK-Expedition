"""Isolated browser fixture. Never reads or writes the user's AFK data or game.

0.7.0 seeds two heroes (a farm expedition and a Siege), one delivered claim
with unique drops (collection, wishlist, share card), a Siege record and a
worker; hiring and collecting a haul are simulated without the game. Windows
notifications and Task Scheduler are switched off.
"""
import json, os, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'verification/independent-rewards-0.5.0'
WORK.mkdir(parents=True,exist_ok=True)
os.environ['AFK_NOTIFY_DISABLED']='1'
with tempfile.TemporaryDirectory(prefix='afk-panel-qa-') as tmp:
    os.environ['LOCALAPPDATA']=tmp
    sys.path.insert(0,str(ROOT/'tools'))
    import afk,panel,siege,workers
    hero=dict(identity_version=2,slot=2,name='Suh',**{'class':8},class_name='Samurai',level=100)
    other=dict(identity_version=2,slot=5,name='SeraphTest',**{'class':14},class_name='White Mage',level=1)
    panel.characters=lambda data:[hero,other]
    def packets(extra=()):
        rows=[dict(kind='kill',rank=3,count=40,weight=40,hash='f'*64,monster_key='e_orc_warrior_3',exp=40,native_exp=10),
              dict(kind='kill',rank=4,count=8,weight=8,hash='e'*64,monster_key='e_orc_warrior_3',exp=90,native_exp=20),
              dict(kind='kill',rank=2,count=2,weight=2,hash='d'*64,monster_key='e_treasure_goblin_3',exp=60,native_exp=15)]
        return rows+list(extra)
    def profile(ident,character,room,pace,extra=()):
        return dict(profile_id=ident,room=room,character=character,game_build='QA',forgepact={},farm_context=dict(schema=1,hash='a'*64),
                    rate_basis='farm-clock',coverage=1,reward_baseline=dict(schema=1,complete=True,magic_find=1000),basis_seconds=600,
                    kills=50,kills_per_min=pace,breaks=0,breaks_per_min=0,packets=packets(extra),
                    quality=dict(window_rates=[pace*f for f in (0.9,1.1,1.0,0.95,1.05,1.0,0.9,1.1)]))
    boss=dict(kind='kill',rank=9,count=1,weight=1,hash='c'*64,monster_key='e_boss_fixture',exp=900,native_exp=200)
    afk.write_json(afk.PROFILES/'qa.json',profile('qa',hero,'Act_03_03',60.0,[boss]))
    afk.write_json(afk.PROFILES/'qa2.json',profile('qa2',other,'Act_01_01',25.0))
    capture=afk.SESSIONS/'previous.ndjson';capture.parent.mkdir(parents=True,exist_ok=True)
    capture.write_text(json.dumps(dict(kind='farm_clock',room='Act_03_03',seconds=5))+'\n',encoding='utf-8')
    afk.write_json(afk.DATA/'panel-calibration.json',dict(character=hero,room='Act_03_03',session=str(capture)))
    # One delivered claim with uniques: the collection, wishlist hits and a share card have data.
    done='farm_fixture_done_claim';now=datetime.now(timezone.utc)
    afk.write_json(afk.PLANS/f'{done}.json',dict(panel_version=panel.VERSION,expedition_id=done,character=hero,zones=[dict(room='Act_03_03')],
                                                 hours=2.0,scale=1.0,preview=dict(kills=7200),label='Suh · The Desert · 2 h',label_region='The Desert'))
    afk.write_json(afk.SESSIONS/f'{done}.result.json',dict(state='done',rewards_saved=True,calls_done=7200,calls_total=7200,items=3,gold=150000,
                                                           exp=48000000,started=(now-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                           updated=now.strftime('%Y-%m-%dT%H:%M:%SZ'),saved='saved (character and account save performed)',
                                                           stages=dict(replay='done',save='done',ingest='done')))
    rows=[dict(expedition_id=done,seq=i+1,kind='item',t=now.strftime('%Y-%m-%dT%H:%M:%SZ'),packet='f'*64,type=t,name=n,placed=True,filter_visible=True,
               item=dict(itemType=t,itemInfoStruct={'27':r,'28':n})) for i,(n,r,t) in enumerate([("Harlequinn's Crest",6,0),('Angel',6,3),('Shield of the Abyss',9,6)])]
    (afk.SPOOL).mkdir(parents=True,exist_ok=True)
    (afk.SPOOL/f'{done}.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    afk.write_json(afk.DATA/'wishlist.json',dict(schema=1,items=[dict(key='helmet_harlequin_crest',added_at='2026-09-20T00:00:00Z')]))
    # Two heroes on the roster: Suh farms, SeraphTest holds a Siege.
    def arm(plan):
        path=afk.PLANS/f"{plan['expedition_id']}.json";afk.write_json(path,plan)
        st=afk.load_state();st['expeditions'][plan['expedition_id']]=dict(expedition_id=plan['expedition_id'],plan=str(path),hours=plan['hours'],
            started_at=(now-timedelta(minutes=47)).strftime('%Y-%m-%dT%H:%M:%SZ'),hero=afk.hero_key(plan['character']),mode=plan.get('mode','farm'))
        afk.save_state(st)
    mods=panel.reward_modifiers.normalize()
    farm=afk.make_plan(2.0,[('Act_03_03',1)],'farm_fixture_live',40,'pickup',profile_overrides={'Act_03_03':afk.read_json(afk.PROFILES/'qa.json')})
    panel.reward_modifiers.apply_to_plan(farm,afk.read_json(afk.PROFILES/'qa.json'),mods)
    farm.update(panel_version=panel.VERSION,label='Suh · The Desert · 2 h',label_hero='Suh',label_region='The Desert');arm(farm)
    held=siege.build_plan(afk.read_json(afk.PROFILES/'qa2.json'),3,4.0,'siege_fixture_live',mods,seed=11)
    held.update(panel_version=panel.VERSION,label='Siege L3 · SeraphTest · The Forest · 4 h',label_hero='SeraphTest',label_region='The Forest',farm_context=dict(schema=1,hash='a'*64))
    arm(held)
    siege.record_claim(afk.DATA,afk.hero_key(other),'Act_01_01',dict(level=3,waves_fought=31))
    # A worker on a trip, and a level 14 one ready to collect.
    crew=workers.empty();a=workers.new_worker(crew,'Brom');a['xp']=sum(workers.xp_to_next(l) for l in range(1,14));a['level']=14
    a['skills']=dict(swift_pick=3,full_cart=3,long_shift=2,keen_eye=2,gem_sense=2,foreman=1)
    workers.start_trip(crew,a['id'],29,6,at=now-timedelta(hours=7),seed=3)
    b=workers.new_worker(crew,'Dulga');workers.start_trip(crew,b['id'],27,2,at=now-timedelta(minutes=30),seed=4)
    workers.save(afk.DATA,crew)
    control=Path(tmp)/'control.json';stop=Path(tmp)/'stop'
    afk.write_json(control,dict(gold=5000000,live=dict(character=other,room='Act_01_01',capture_on=False,replay_running=False,game_build='QA',farm_context=dict(hash='b'*64))))
    class FixturePanel(panel.Panel):
        def snapshot(self,focus=None):
            controls=afk.read_json(control,{})
            live=controls.get('live')
            self.live=live;self.live_at=time.monotonic();self.game_running=bool(live)
            result=super().snapshot(focus)
            # Test-only presentation states. They never alter native actions or saves.
            presentation=controls.get('presentation',{})
            for key in ('characters','profiles','calibration','rewards','armed','plan',
                        'progress','recovery','job','background','delivery','regions',
                        'support_warnings','expeditions','workers','collection'):
                if key in presentation:result[key]=presentation[key]
            return result
        def fresh(self):
            live=afk.read_json(control,{}).get('live')
            if not live:raise ValueError('Game closed in fixture.')
            return live
        def sync_notification(self):
            self.notification={}
        def send_payment(self,request,amount):
            # The game's purchase path, simulated: the fixture's gold pays and the receipt is written;
            # the panel's own payment flow does the rest.
            controls=afk.read_json(control,{});gold=controls.get('gold',0);ok=gold>=amount
            if ok:controls['gold']=gold-amount;afk.write_json(control,controls)
            receipt=dict(request_id=request,ok=ok,amount=amount,gold_before=gold,gold_after=gold-amount if ok else gold,
                         error='' if ok else f'not enough gold ({gold:,} of {amount:,})',at=datetime.now(timezone.utc).isoformat())
            afk.write_json(self.data/'models'/f'worker-pay-{request}.json',receipt)
            return receipt
        def collect_worker(self,worker_id):
            # A delivery the game would make, simulated: the planned haul is "created" as-is.
            state=workers.load(self.data);w=workers.find(state,worker_id);trip=w.get('trip')
            if not trip:raise ValueError(f"{w['name']} is not on a trip.")
            plan=workers.delivery_plan(w,trip,workers.trip_view(w)['credited_work_hours'])
            created={f"{i['type']}:{i['id']}":i['amount'] for i in plan['items']}
            outputs={'14:9':max(1,sum(plan['prospect'].values())//3)} if plan['prospect'] else {}
            afk.write_json(self.data/'sessions'/f"{plan['delivery_id']}.result.json",dict(delivery_id=plan['delivery_id'],state='done',created=created,prospect_outputs=outputs))
            applied=workers.apply_delivery(state,worker_id,plan,dict(created=created,prospect_outputs=outputs));workers.save(self.data,state)
            self.log(f"Fixture: {w['name']} delivered {plan['ore_total']:,} ore (+{plan['xp']:,} XP).");return applied
        def action(self,name,args):
            allowed=('start','cancel','save_modifiers','wishlist_add','wishlist_remove','worker_hire','worker_respec','worker_learn',
                     'worker_rename','worker_start','worker_cancel','worker_collect')
            if name not in allowed:raise ValueError('Fixture allows local actions only.')
            return super().action(name,args)
    app=FixturePanel(afk.DATA);server=panel.ThreadingHTTPServer(('127.0.0.1',9567),panel.Handler);server.app=app
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    info=dict(url='http://127.0.0.1:9567',temp=tmp,data=str(afk.DATA),control=str(control),stop=str(stop),pid=os.getpid())
    (WORK/'fixture-info.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    print(json.dumps(info),flush=True)
    try:
        while not stop.exists():time.sleep(.2)
    finally:
        server.shutdown();server.server_close();thread.join()
