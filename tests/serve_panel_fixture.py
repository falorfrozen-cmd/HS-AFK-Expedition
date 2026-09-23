"""Isolated browser fixture. Never reads or writes the user's AFK data or game."""
import json, os, sys, tempfile, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'verification/independent-rewards-0.5.0'
WORK.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='afk-panel-qa-') as tmp:
    os.environ['LOCALAPPDATA']=tmp
    sys.path.insert(0,str(ROOT/'tools'))
    import afk,panel
    hero=dict(identity_version=2,slot=2,name='Suh',**{'class':8},class_name='Samurai',level=100)
    other=dict(identity_version=2,slot=5,name='SeraphTest',**{'class':14},class_name='White Mage',level=1)
    panel.characters=lambda data:[hero,other]
    profile=dict(profile_id='qa',room='Act_03_03',character=hero,game_build='QA',forgepact={},
                 farm_context=dict(schema=1,hash='a'*64),rate_basis='farm-clock',coverage=1,
                 reward_baseline=dict(schema=1,complete=True,magic_find=1000),basis_seconds=240,kills=50,kills_per_min=12.5,breaks=0,breaks_per_min=0,
                 packets=[dict(kind='kill',rank=1,count=50,weight=1,hash='f'*64,monster_key='fixture',exp=40,native_exp=10)])
    afk.write_json(afk.PROFILES/'qa.json',profile)
    capture=afk.SESSIONS/'previous.ndjson';capture.parent.mkdir(parents=True,exist_ok=True)
    capture.write_text(json.dumps(dict(kind='farm_clock',room='Act_03_03',seconds=5))+'\n',encoding='utf-8')
    afk.write_json(afk.DATA/'panel-calibration.json',dict(character=hero,room='Act_03_03',session=str(capture)))
    control=Path(tmp)/'control.json';stop=Path(tmp)/'stop'
    afk.write_json(control,dict(live=dict(character=other,room='Act_01_01',capture_on=False,replay_running=False,game_build='QA',farm_context=dict(hash='b'*64))))
    class FixturePanel(panel.Panel):
        def snapshot(self):
            controls=afk.read_json(control,{})
            live=controls.get('live')
            self.live=live;self.live_at=time.monotonic();self.game_running=bool(live)
            result=super().snapshot()
            # Test-only presentation states. They never alter native actions or saves.
            presentation=controls.get('presentation',{})
            for key in ('characters','profiles','calibration','rewards','armed','plan',
                        'progress','recovery','job','background','delivery','regions',
                        'support_warnings'):
                if key in presentation:result[key]=presentation[key]
            return result
        def fresh(self):
            live=afk.read_json(control,{}).get('live')
            if not live:raise ValueError('Game closed in fixture.')
            return live
        def action(self,name,args):
            if name not in ('start','cancel','save_modifiers'):raise ValueError('Fixture allows local timer actions only.')
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
