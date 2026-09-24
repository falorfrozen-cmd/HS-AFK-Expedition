"""AFK FARM local panel. Python 3.13 standard library; localhost only."""
from __future__ import annotations
import argparse, base64, hashlib, json, math, os, re, secrets, shutil, subprocess, sys, threading, time, uuid, webbrowser, zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
import afk
import reward_modifiers
import ingest_spool
import loot_filter
import notify
import calibration, recovery, validate_farm
import collection
import workers
from product_data import Presentation, SUPPORT, support_warnings

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'web'
CLASSES={i+1:n for i,n in enumerate(('Viking','Pyromancer','Marksman','Pirate','Nomad','Redneck','Necromancer','Samurai','Paladin','Amazon','Demon Slayer','Demonspawn','Shaman','White Mage','Marauder','Plague Doctor','Shield Lancer','Illusionist','Jotunn','Exo','Butcher','Stormweaver','Bard','Prophet'))}
XOR=bytes.fromhex('e3953db1016bb65854383f46a17429cc454551f2a7f7abb726f137a88191e67e')
VERSION='0.7.0'
IDENTIFIER=re.compile(r'[A-Za-z0-9_-]{1,120}\Z')

def require(ok,message):
    if not ok: raise ValueError(message)

def read(path,default=None):
    return afk.read_json(path,default)

def characters(data):
    """Read names/classes only. The HSS codec matches the sibling Item Editor.
    Never write, migrate or repair a save from the panel.
    """
    result=[]
    for p in (data.parent/'hs2saves').glob('herosiege*.hss'):
        m=re.fullmatch(r'herosiege(\d+)\.hss',p.name)
        if not m or p.stat().st_size<1000: continue
        try:
            raw=zlib.decompress(base64.b64decode(''.join(p.read_text(encoding='utf-8').split()).replace('\0',''),validate=True))
            txt=bytes(b^XOR[i%len(XOR)] for i,b in enumerate(raw)).decode('utf-16-le')
            def field(name):
                match=re.search(r'(?:^|\n)'+name+r'="?([^"\r\n]*)',txt)
                return match.group(1) if match else ''
            name=field('name'); cls=int(float(field('class'))); level=int(float(field('level')))
            if name: result.append(dict(slot=int(m[1]),name=name,**{'class':cls},class_name=CLASSES.get(cls,f'Class {cls}'),level=level,identity_version=2))
        except (OSError,ValueError,zlib.error,UnicodeError): continue
    return sorted(result,key=lambda c:c['slot'])

def valid_identity(c):
    try: afk.character_key(c); return True
    except SystemExit: return False

def same_character(a,b):
    return valid_identity(a) and valid_identity(b) and afk.character_key(a)==afk.character_key(b)

def profile_problems(p,build=None):
    issues=[]
    if not valid_identity(p.get('character')): issues.append('Legacy character identity · recalibration required')
    if not re.fullmatch(r'Act_\d{2}_\d{2}',p.get('room','')): issues.append('This activity is not supported in the initial release')
    context=p.get('farm_context') or {}
    if context.get('schema')!=1 or not re.fullmatch(r'[a-f0-9]{64}',context.get('hash','')): issues.append('Missing equipment and talent fingerprint')
    if p.get('rate_basis')!='farm-clock': issues.append('Recalibrate using the current region timer')
    if build and p.get('game_build')!=build: issues.append('Game version has changed')
    if not (p.get('reward_baseline') or {}).get('complete'): issues.append('Record a new route with the current plugin to enable independent rewards')
    if p.get('coverage',0)<.95: issues.append('Kill capture coverage is below 95%')
    if (p.get('quality') or {}).get('status')=='invalid': issues.append('The recording contains an invalid context or timer')
    if p.get('basis_seconds',0)<calibration.MIN_PROFILE_SECONDS or p.get('kills',0)<calibration.MIN_PROFILE_KILLS: issues.append('A sample of at least 1 minute and 30 kills is required')
    if not p.get('packets'): issues.append('No reward records')
    # Native monster rank labels: a special monster (boss, event monster) never
    # replays until `afk special verify` passed it; its kills are left out of
    # the plan instead of closing the whole calibration (0.7.0). A kill without
    # any rank cannot be told apart from one and still closes it.
    if any(q.get('rank') is None for q in p.get('packets',[]) if q.get('kind')=='kill'):
        issues.append('A recorded monster has no rank; record a new calibration')
    return issues

def capture_outcome(capture,stats,running,profiles):
    """Describe a recording from JSON evidence, never from a successful CLI exit."""
    seconds=stats.get('seconds',0);kills=stats.get('kills',0)
    remaining_seconds=max(0,math.ceil(calibration.MIN_PROFILE_SECONDS-seconds))
    remaining_kills=max(0,calibration.MIN_PROFILE_KILLS-kills)
    result=dict(min_seconds=calibration.MIN_PROFILE_SECONDS,min_kills=calibration.MIN_PROFILE_KILLS,
                remaining_seconds=remaining_seconds,remaining_kills=remaining_kills,
                minimum_ready=not (remaining_seconds or remaining_kills))
    def outcome(status,title,message,**extra):return dict(result,status=status,title=title,message=message,**extra)
    if capture.get('validation_reference'):
        return outcome('validation','Independent validation recording',
                       'This separate check does not create or replace a farming profile. See the validation result below.')
    if stats.get('invalid') or (stats.get('quality') or {}).get('status')=='invalid':
        return outcome('invalid','Recording cannot be used','The loadout changed or the region timer is invalid. Start a new recording with a stable loadout.')
    if running:
        if result['minimum_ready']:
            return outcome('ready','Ready to check and save','The time and kill minimums are met. Finish to check reward records and save the profile; this does not guarantee pace accuracy.')
        return outcome('recording','Keep recording',
                       f'{remaining_seconds} more seconds in this region and {remaining_kills} more kills are needed. Use Restart region when the map is cleared; town and loading time do not count.')
    matching=[p for p in profiles if p.get('session')==capture.get('session') and p.get('room')==capture.get('room')
              and same_character(p.get('character'),capture.get('character'))]
    saved=next((p for p in matching if p.get('usable')),None)
    if saved:
        return outcome('saved','Calibration saved','Your profile is ready. Open this recorded region in the expedition planner to start farming.',profile_id=saved['id'])
    if matching:
        return outcome('rejected','Profile is not usable',' · '.join(matching[0].get('problems') or ['The saved profile did not pass eligibility checks.']))
    if not result['minimum_ready']:
        return outcome('insufficient','No profile saved — below the minimum',
                       f'Recorded {seconds:.0f} of {calibration.MIN_PROFILE_SECONDS} required seconds and {kills} of {calibration.MIN_PROFILE_KILLS} required kills. '
                       'The raw recording is preserved. Start a new recording and keep it running across region restarts until both minimums are met.')
    if capture.get('stopped_without_profile'):
        return outcome('unsaved','Recording stopped — no profile saved','You stopped without saving a profile. The raw recording is preserved.')
    return outcome('unsaved','No usable profile saved',capture.get('save_error') or
                   'The recording ended without a saved profile for this session. Open the activity log for details; the raw recording is preserved.')


def progress_view(data,expedition):
    failure=read(data/'sessions'/f'{expedition}.failure.json')
    p=failure or read(data/'sessions'/f'{expedition}.progress.json') or {}
    total=p.get('calls_total',0); done=p.get('calls_done',0)
    p=dict(p,percent=max(0,min(100,100*done/total)) if total else 0)
    if failure: p['reconciliation_required']=True
    if p.get('state')=='error': p['reconciliation_required']=True
    p['resumable']=not failure and recovery.resumable(data,expedition)
    return p


def zone_names():
    try:return {z['room']:z['name'] for z in json.loads((WEB/'zones.json').read_text(encoding='utf-8'))}
    except (OSError,ValueError,KeyError,TypeError):return {}


def expedition_label(room,hours,hero=None):
    """Readable name of an expedition's Vault category: hero, region and duration."""
    return afk.expedition_label(hero,zone_names().get(room,room),hours)


# What happens to items the game's loot filter hides during delivery: sold
# below Satanic and broken down like the Prospector from Satanic up, or kept.
FILTERED_ITEMS=('convert','keep')


def load_preferences(data):
    prefs=read(data/'preferences.json',{}) or {}
    prefs=prefs if isinstance(prefs,dict) else {}
    speed=prefs.get('delivery_speed');ready=prefs.get('ready_notification');filtered=prefs.get('filtered_items')
    return dict(schema=1,delivery_speed=speed if speed in afk.DELIVERY_SPEEDS else 'normal',
                ready_notification=ready if type(ready) is bool else True,
                filtered_items=filtered if filtered in FILTERED_ITEMS else 'convert')


def delivery_seconds(result):
    """Wall time of a finished delivery, from its native checkpoint times."""
    try:
        return max(0,round((afk.parse_iso(result['updated'])-afk.parse_iso(result['started'])).total_seconds()))
    except (KeyError,TypeError,ValueError):
        return None

class Panel:
    def __init__(self,data=afk.DATA):
        self.data=Path(data); self.token=secrets.token_urlsafe(32)
        self.lock=threading.Lock(); self.job_lock=threading.Lock(); self.state_lock=threading.Lock(); self.closed=threading.Event()
        self.capture_lock=threading.Lock(); self.capture_cache=None
        self.job=None; self.live=None; self.live_at=0; self.game_running=False; self.connection_error=None
        self.editor=None; self.editor_at=0; self.last_profiles=0; self.profiles=[]; self.chars=[]; self.notification={}; self.results_cache=None
        self.calibration=read(self.data/'panel-calibration.json',{}) or {}
        self.presentation=Presentation(ROOT)
        self.collection=collection.Collection(self.data,ROOT)
        self.recovery_cache=None; self.recovery_checked_at=0
        self.pause_lock=threading.Lock(); self.pause_note=None
        self.refresh_profiles()

    def game_bin(self):
        value=(read(self.data/'config.json',{}) or {}).get('game_bin','')
        p=Path(value)
        require(value and (p/'Hero_Siege.exe').is_file(),'Choose your Hero Siege game folder in Settings.')
        return p

    def refresh_profiles(self):
        profiles=[]; seen=set(); build=(read(self.data/'build.json',{}) or {}).get('game_build'); verified=afk.verified_specials()
        for f in sorted((self.data/'profiles').glob('*.json')):
            p=read(f,{})
            if not isinstance(p,dict): continue
            key=json.dumps([p.get('profile_id'),p.get('room'),p.get('character'),p.get('built_at')],sort_keys=True)
            if key in seen: continue
            seen.add(key); problems=profile_problems(p,build)
            special=sum(q.get('count',0) for q in p.get('packets',[]) if not afk.is_chest(q) and not afk.replayable(q,verified))
            chests=sum(q.get('count',0) for q in p.get('packets',[]) if afk.is_chest(q))
            profiles.append(dict(p,id=f.stem,problems=problems,usable=not problems,special_kills=special,chest_breaks=chests))
        self.profiles=profiles; self.chars=characters(self.data); self.last_profiles=time.monotonic()

    def monitor(self):
        from game_session import Session,running
        while not self.closed.wait(3):
            if self.lock.acquire(blocking=False):
                try:
                    folder=self.game_bin(); pid=running(folder/'Hero_Siege.exe')
                    self.game_running=pid is not None
                    if pid:
                        self.live=Session(folder,self.data).state(pid);self.live_at=time.monotonic();self.connection_error=None
                    else: self.live=None;self.connection_error=None
                except (Exception,SystemExit) as e:
                    self.live=None;self.connection_error=str(e)
                finally:self.lock.release()
            if time.monotonic()-self.editor_at>20:
                self.editor=ingest_spool.discover_editor(.1);self.editor_at=time.monotonic()
            self.settle_late_payments()
            self.sync_notification()

    def sync_notification(self):
        # Keeps one Windows "ready" task per armed expedition (and worker trip)
        # in step with the roster and the setting; a failure is shown, never raised.
        try:
            entries=[]
            for armed in afk.armed_list(afk.load_state(self.data/'state.json')):
                plan=read(Path(armed['plan']),{}) if armed.get('plan') else {}
                entries.append(self.ready_entry(armed,plan or {}))
            entries+=self.worker_entries()
            self.notification=notify.sync_all(self.data,[e for e in entries if e],load_preferences(self.data)['ready_notification'])
        except Exception as e:
            self.notification=dict(error=f'Notification task not updated: {e}')

    def ready_entry(self,armed,plan):
        """When an expedition's notification fires: its end, a Siege's report time."""
        if plan.get('mode')=='siege':
            import siege
            try:ends=afk.parse_iso(armed['started_at'])+timedelta(hours=siege.report_hours(plan))
            except (KeyError,TypeError,ValueError):return None
            title,message=notify.texts(plan)
            return notify.entry('exp-'+armed['expedition_id'],ends,title,message)
        return notify.expedition_entry(armed,plan)

    def notification_view(self,armed):
        """The setting's state for the focus expedition, plus every scheduled task."""
        n=self.notification or {}
        tasks=n.get('tasks') or {}
        key='exp-'+armed['expedition_id'] if armed else None
        scheduled=dict(expedition_id=armed['expedition_id'],at=tasks[key]) if key and key in tasks else None
        errors=n.get('errors') or {}
        return dict(windows=os.name=='nt',scheduled=scheduled,tasks=[dict(key=k,at=v) for k,v in sorted(tasks.items(),key=lambda kv:kv[1])],
                    error=n.get('error') or (errors.get(key) if key else None) or next(iter(errors.values()),None))

    def current_live(self):
        return self.live if time.monotonic()-self.live_at<15 else None

    def capture_view(self):
        capture=dict(self.calibration);path=capture.get('session')
        if not path:return None
        p=Path(path).resolve()
        if not p.is_relative_to((self.data/'sessions').resolve()):return dict(error='Calibration recording not found')
        try:stat=p.stat()
        except OSError:return dict(error='Calibration recording not found')
        room=capture.get('room');stamp=(str(p),room,stat.st_mtime_ns,stat.st_size)
        # Display cache only. Saving/validation still reads and verifies the source.
        with self.capture_lock:
            if not self.capture_cache or self.capture_cache[0]!=stamp:
                records=afk.read_ndjson(p)
                ticks=[r for r in records if r.get('kind')=='farm_clock' and r.get('room')==room]
                kills=[r for r in records if r.get('kind')=='kill' and r.get('room')==room]
                seconds=sum(float(r.get('seconds',0)) for r in ticks)
                stats=dict(seconds=seconds,kills=len(kills),rate=60*len(kills)/seconds if seconds else 0,
                           quality=calibration.quality(records,room),
                           invalid=any(r.get('kind')=='context_invalid' for r in records))
                self.capture_cache=(stamp,stats)
            stats=self.capture_cache[1]
        live=self.current_live()
        running=bool(not capture.get('stopped_at') and live and live.get('capture_on') and live.get('capture_file')==path)
        return dict(capture,**stats,running=running,outcome=capture_outcome(capture,stats,running,self.profiles))

    def hero_for_slot(self,slot):
        return next((c for c in self.chars if c['slot']==slot),None) if type(slot) is int else None

    def focus_armed(self,state,focus,live):
        """The expedition the top-level fields describe: the one asked for, else
        the selected hero's (none when that hero is free), else the live
        hero's, else the most recently started one."""
        focus=focus or {}
        if focus.get('expedition') in state['expeditions']:return state['expeditions'][focus['expedition']]
        if focus.get('slot') is not None:
            hero=self.hero_for_slot(focus['slot'])
            return afk.armed_for_hero(state,hero) if hero else None
        if live and valid_identity(live.get('character')):
            found=afk.armed_for_hero(state,live['character'])
            if found:return found
        roster=afk.armed_list(state)
        return roster[-1] if roster else None

    def review(self,ident,fresh=False):
        """Display cache per claim: reward/recovery actions always inspect fresh files."""
        cached=self.recovery_cache.get(ident) if isinstance(self.recovery_cache,dict) else None
        if fresh or not cached or time.monotonic()-cached[0]>2:
            if not isinstance(self.recovery_cache,dict):self.recovery_cache={}
            cached=(time.monotonic(),recovery.inspect(self.data,ident));self.recovery_cache[ident]=cached
        return cached[1]

    def roster_view(self,armed,live):
        """One row of the hero roster: who, where, how long, and how its claim stands."""
        plan=read(Path(armed['plan']),{}) if armed.get('plan') else {}
        plan=plan or {}
        ident=armed['expedition_id']+'_claim';progress=progress_view(self.data,ident);review=self.review(ident)
        room=(plan.get('zones') or [{}])[0].get('room')
        try:
            started=afk.parse_iso(armed['started_at']);elapsed=(datetime.now(timezone.utc)-started).total_seconds()/3600
            ready_at=(started+timedelta(hours=float(armed['hours']))).isoformat()
        except (KeyError,TypeError,ValueError):elapsed=None;ready_at=None
        row=dict(expedition_id=armed['expedition_id'],hero=afk.armed_hero(armed),character=plan.get('character'),mode=plan.get('mode','farm'),
                 label=plan.get('label'),room=room,region=zone_names().get(room,room),started_at=armed.get('started_at'),hours=armed.get('hours'),
                 ready_at=ready_at,ready=elapsed is not None and elapsed>=float(armed.get('hours') or 0),
                 progress=dict((k,progress.get(k)) for k in ('state','percent','calls_done','calls_total','resumable','reconciliation_required','pause')),
                 recovery=dict(status=review.get('status'),recoverable=review.get('recoverable'),resumable=review.get('resumable')),
                 live_hero=bool(live and same_character(live.get('character'),plan.get('character'))))
        if plan.get('mode')=='siege' and elapsed is not None:
            import siege
            row['siege']=siege.live_view(plan,elapsed)
            row['ready']=elapsed>=siege.report_hours(plan)
        return row

    def snapshot(self,focus=None):
        if time.monotonic()-self.last_profiles>3:self.refresh_profiles()
        state=afk.load_state(self.data/'state.json'); plan=None; progress={}
        live=self.current_live()
        armed=self.focus_armed(state,focus,live)
        if armed:
            plan=read(Path(armed['plan']))
            progress=progress_view(self.data,armed['expedition_id']+'_claim')
        rewards=[]
        for f in sorted((self.data/'sessions').glob('*.result.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            result=read(f,{}) or {}; ident=f.name.removesuffix('.result.json')
            plan_record=read(self.data/'plans'/f'{ident}.json',{}) or {}
            if not plan_record.get('panel_version'):continue
            rewards.append(dict(result,id=ident,character=plan_record.get('character'),room=(plan_record.get('zones') or [{}])[0].get('room'),
                                save_confirmed=result.get('rewards_saved') is True and result.get('saved')=='saved (character and account save performed)',
                                loot=self.presentation.loot(self.data,ident)))
            if len(rewards)>=20:break
        cfg=read(self.data/'config.json',{}) or {}
        with self.state_lock: job=json.loads(json.dumps(self.job)) if self.job else None
        review=self.review(armed['expedition_id']+'_claim') if armed else None
        focus_hero=(plan or {}).get('character') if armed else self.hero_for_slot((focus or {}).get('slot'))
        validations=[read(f,{}) for f in sorted((self.data/'validations').glob('*.result.json'),key=lambda p:p.stat().st_mtime,reverse=True)][:20]
        levels={afk.character_key(c):c.get('level') for c in self.chars if valid_identity(c)}
        for reward in rewards:
            sidecar=read(self.data/'sessions'/f"{reward['id']}.panel.json",{}) or {}
            reward['level_before']=sidecar.get('level_before')
            ch=reward.get('character')
            reward['level_now']=levels.get(afk.character_key(ch)) if valid_identity(ch) else None
            reward['delivery_seconds']=delivery_seconds(reward)
        if rewards:
            wishlist=collection.load_wishlist(self.data);_,firsts=self.collection.log(self.expedition_results())
            for reward in rewards:
                reward['wishlist_hits']=collection.wishlist_hits(self.collection,reward['id'],wishlist)
                reward['new_finds']=len(firsts.get(reward['id'],[]))
        filter_error=None
        try: vault_filter=loot_filter.load(self.data/'loot-filter.json')
        except ValueError as error:
            vault_filter=loot_filter.normalize();filter_error=str(error)
        modifier_error=None
        try: modifiers=reward_modifiers.load(self.data/'reward-modifiers.json')
        except ValueError as error:
            modifiers=reward_modifiers.normalize();modifier_error=str(error)
        return dict(version=VERSION,server_time=datetime.now(timezone.utc).isoformat(),characters=self.chars,profiles=self.profiles,
                    live=live,game_running=self.game_running,connection_error=self.connection_error,armed=armed,plan=plan,
                    progress=progress,last_claim=state.get('last_claim'),rewards=rewards,calibration=self.capture_view(),
                    job=job,editor=self.editor,config=dict(game_bin=cfg.get('game_bin',''),auto_capture=cfg.get('auto_capture',False)),
                    installation=self.installation(),recovery=review,support=SUPPORT,validations=validations,
                    reward_modifiers=modifiers,reward_modifiers_error=modifier_error,
                    modifier_fields=[dict(key=k,label=l,description=d) for k,l,d in reward_modifiers.FIELDS],
                    claim_context_matches=reward_modifiers.context_matches((plan or {}).get('farm_context'),(live or {}).get('farm_context'),bool((plan or {}).get('reward_modifiers'))),
                    support_warnings=support_warnings((live or {}).get('forgepact')),
                    portraits={str(c['slot']):self.portrait_key(c) for c in self.chars if (self.data/'portraits'/(self.portrait_key(c)+'.png')).is_file()},
                    loot_filter=vault_filter,loot_filter_error=filter_error,loot_filter_rarities=list(loot_filter.GEAR_RARITIES),
                    preferences=load_preferences(self.data),delivery=self.delivery_view(armed,plan,progress),
                    notification=self.notification_view(armed),
                    repeat=self.repeat_view(state,armed,afk.hero_key(focus_hero) if focus_hero else None),profile_live_matches=self.live_matches(live),regions=self.regions_view(),
                    background=self.background_view(armed,progress,review,live) if armed else self.background_view(None,{},None,live),pause_note=self.pause_note,
                    expeditions=[self.roster_view(a,live) for a in afk.armed_list(state)],focus=dict(focus or {}),
                    collection=self.collection_summary(),siege_records=self.siege_records(focus_hero),workers=self.workers_view())

    def delivery_view(self,armed,plan,progress):
        """Delivery speeds with this computer's estimate for the calls due now."""
        calls=None
        if progress.get('calls_total'):
            calls=max(0,progress['calls_total']-(progress.get('calls_done') or 0))
        elif armed and plan:
            try:
                elapsed=(datetime.now(timezone.utc)-afk.parse_iso(armed['started_at'])).total_seconds()/3600
                credited=max(0.0,min(elapsed,float(armed['hours']),afk.MAX_HOURS))
                calls=round(plan['preview']['calls']*credited/float(plan['hours'])) if plan.get('hours') else None
            except (KeyError,TypeError,ValueError,ZeroDivisionError):calls=None
        speeds=[dict(id=k,label={'normal':'Normal','fast':'Fast','max':'Maximum'}[k],per_frame=v[0],frame_budget_ms=v[1],
                     calls_per_second=afk.learned_call_rate(k),
                     seconds=None if calls is None else round(afk.estimate_delivery_seconds(calls,k)))
                for k,v in afk.DELIVERY_SPEEDS.items()]
        return dict(calls=calls,speeds=speeds,active_speed=afk.delivery_speed(read(self.data/'plans'/f"{armed['expedition_id']}_claim.json",{}) or {}) if armed else None)

    def repeat_view(self,state,armed,hero=None):
        """A hero's last settled expedition (else the newest one), to start it again with one click."""
        if armed:return None
        claims=state.get('last_claims') or {}
        record=claims.get(hero) if hero else state.get('last_claim')
        last=(record or {}).get('expedition_id','')
        plan=read(self.data/'plans'/f"{last.removesuffix('_claim')}.json",{}) or {}
        room=(plan.get('zones') or [{}])[0].get('room');ch=plan.get('character')
        if not room or not valid_identity(ch) or not plan.get('hours'):return None
        if 'expeditions' in state and afk.armed_for_hero(state,ch):return None
        profile=next((p for p in self.profiles if p['usable'] and p.get('room')==room and same_character(p.get('character'),ch)),None)
        return dict(room=room,hours=float(plan['hours']),slot=ch['slot'],name=ch['name'],profile=profile['id'] if profile else None,
                    mode=plan.get('mode','farm'),siege_level=(plan.get('siege') or {}).get('level'),
                    reason=None if profile else 'The saved calibration for this hero and region is no longer usable. Recalibrate first.')

    def expedition_results(self):
        """Delivered claims (saved, or closed as partial by the player) with their plans."""
        try:files=sorted((self.data/'sessions').glob('*.result.json'));key=tuple((f.name,f.stat().st_mtime_ns) for f in files)
        except OSError:return []
        if self.results_cache and self.results_cache[0]==key:return self.results_cache[1]
        out=[]
        for f in files:
            ident=f.name.removesuffix('.result.json');result=read(f,{}) or {};plan=read(self.data/'plans'/f'{ident}.json',{}) or {}
            if plan.get('panel_version') and (result.get('rewards_saved') is True or result.get('partial') is True):out.append((ident,result,plan))
        self.results_cache=(key,out);return out

    def regions_view(self):
        """Per hero, each calibrated region's measured pace and XP next to what its
        delivered expeditions yielded per hour: gold at x1, items at the Magic
        Find used. One row per region: the newest usable calibration, else the
        newest one, marked as needing recalibration."""
        latest={}
        for p in self.profiles:
            if not valid_identity(p.get('character')):continue
            key=(json.dumps(p['character'],sort_keys=True),p.get('room'))
            rank=(bool(p.get('usable')),str(p.get('built_at') or ''))
            if key not in latest or rank>(bool(latest[key].get('usable')),str(latest[key].get('built_at') or '')):latest[key]=p
        heroes={}
        for (hero,room),p in latest.items():heroes.setdefault(hero,[]).append(p)
        results=self.expedition_results();names=zone_names();out=[]
        for hero,profiles in heroes.items():
            rows=[]
            for p in profiles:
                hours=gold=0.0;counts={r:0 for r in ('Unholy','Angelic','Heroic','Satanic')};runs=0;magic=set()
                for ident,result,plan in results:
                    if (plan.get('zones') or [{}])[0].get('room')!=p['room'] or not same_character(plan.get('character'),p['character']):continue
                    done,total=result.get('calls_done') or 0,result.get('calls_total') or 0
                    fraction=done/total if result.get('partial') and total else 1.0
                    h=float(plan.get('hours') or 0)*float(plan.get('scale') or 1)*fraction
                    if h<=0:continue
                    mods=plan.get('reward_modifiers') or {}
                    hours+=h;runs+=1;gold+=float(result.get('gold') or 0)/float(mods.get('gold') or 1);magic.add(float(mods.get('magic_find') or 1))
                    found=self.presentation.loot(self.data,ident).get('rarities',{})
                    for r in counts:counts[r]+=found.get(r,0)
                rows.append(dict(profile=p['id'],usable=bool(p.get('usable')),room=p['room'],name=names.get(p['room'],p['room']),kills_per_min=p.get('kills_per_min'),
                                 xp_per_hour=(p.get('exp_per_min') or 0)*60 or None,gold_per_hour=gold/hours if hours else None,
                                 rarities_per_hour={r:c/hours for r,c in counts.items()} if hours else {},
                                 expeditions=runs,hours=hours,magic_find=sorted(magic)))
            rows.sort(key=lambda r:-(r['xp_per_hour'] or 0))
            out.append(dict(character=profiles[0]['character'],rows=rows))
        return out

    def live_matches(self,live):
        """Per profile of the loaded hero: does the current loadout still match?"""
        if not live or not valid_identity(live.get('character')) or not live.get('farm_context'):return {}
        return {p['id']:reward_modifiers.context_matches(p.get('farm_context'),live['farm_context'],True)
                for p in self.profiles if p['usable'] and same_character(p.get('character'),live['character'])}

    def background_view(self,armed,progress,review,live):
        """Whether Claim in background can run: verified automatic setup, game closed or at a menu."""
        def no(reason):return dict(available=False,reason=reason)
        if not armed:return no('No active expedition.')
        if review and review.get('status')=='needs_review' or progress.get('reconciliation_required'):return no('The previous claim needs review.')
        if progress.get('state') in ('running',) :return no('Delivery is already running.')
        setup=self.installation()
        if not setup.get('verified_build'):return no('Automatic setup is available for the verified game executable only.')
        if not setup.get('plugin_current'):return no('Install the current AFK plugin in Settings first.')
        if self.game_running and live and live.get('character') and live.get('room') not in ('Main_Menu_rm','Chose_rm'):
            plan_char=(read(Path(armed['plan']),{}) or {}).get('character')
            if not same_character(live.get('character'),plan_char):return no('Another hero is loaded. Return to the main menu or close the game first.')
        return dict(available=True,reason=None)

    def note(self,message):
        with self.state_lock:
            if self.job:self.job['output']=(self.job['output']+str(message)+'\n')[-40000:]
            else:self.pause_note=str(message)[-2000:]

    def target_armed(self,args,live=None):
        """The armed expedition an action is for: ``expedition`` (id), else the
        hero in ``slot`` if it has one, else the live hero's, else the only one."""
        state=afk.load_state(self.data/'state.json')
        ident=args.get('expedition')
        if ident is not None:
            require(isinstance(ident,str) and ident in state['expeditions'],'This expedition is not active.')
            return state['expeditions'][ident]
        hero=next((c for c in characters(self.data) if c['slot']==args.get('slot')),None) if type(args.get('slot')) is int else None
        found=afk.armed_for_hero(state,hero) if hero else None
        if found:return found
        roster=afk.armed_list(state);require(roster,'No active expedition.')
        if live and valid_identity(live.get('character')):
            found=afk.armed_for_hero(state,live['character'])
            if found:return found
        require(len(roster)==1,'Several heroes have an active expedition. Choose which one.')
        return roster[0]

    def pause(self,args=None):
        """Pause a running delivery at a saved position. Runs beside the claim job."""
        armed=self.target_armed(args or {},self.current_live())
        pr=progress_view(self.data,armed['expedition_id']+'_claim')
        require(pr.get('state') in ('running','paused'),'No reward delivery is running.')
        require(self.pause_lock.acquire(blocking=False),'Pause was already requested.')
        def run():
            try:
                env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['PYTHONDONTWRITEBYTECODE']='1'
                flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
                result=subprocess.run([sys.executable,'-B','-u',str(ROOT/'tools/afk.py'),'pause'],cwd=ROOT,capture_output=True,text=True,
                                      encoding='utf-8',errors='replace',env=env,creationflags=flags,timeout=60)
                self.note((result.stdout+result.stderr).strip() or 'Pause requested.')
            except (OSError,subprocess.SubprocessError) as error:self.note('Pause failed: '+str(error))
            finally:self.pause_lock.release()
        threading.Thread(target=run,daemon=True).start()
        return {'accepted':True}

    def installation(self):
        try:
            b=self.game_bin()
            return self.presentation.setup(b)
        except ValueError:return self.presentation.setup(None)

    def portrait_key(self,c):
        return hashlib.sha256(json.dumps(afk.character_key(c)).encode()).hexdigest()

    def fresh(self):
        from game_session import Session,running
        folder=self.game_bin();pid=running(folder/'Hero_Siege.exe');require(pid,'Launch the game and load your character first.')
        s=Session(folder,self.data).state(pid);self.live=s;self.live_at=time.monotonic();self.game_running=True
        require(s.get('online') is False,'AFK FARM supports local / offline characters only.')
        require(s.get('player_count')==1 and valid_identity(s.get('character')),'Your local character has not loaded.')
        require(s.get('hook_native') is True,'Install the current AFK plugin in Settings and restart the game.')
        require(s.get('farm_context') is not None,'Could not read the loadout. Expeditions are unavailable for this character.')
        return s

    def log(self,message):
        with self.state_lock:self.job['output']=(self.job['output']+str(message)+'\n')[-40000:]

    def cli(self,*args):
        env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['PYTHONDONTWRITEBYTECODE']='1'
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
        process=subprocess.Popen([sys.executable,'-B','-u',str(ROOT/'tools/afk.py'),*map(str,args)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env,creationflags=flags)
        for line in process.stdout:self.log(line.rstrip())
        code=process.wait(); require(code==0,f'Action failed (exit code {code}). See the activity log for details.')

    def submit(self,action,args):
        if action=='pause_delivery':return self.pause(args)
        require(self.job_lock.acquire(blocking=False),'Another action is in progress.')
        with self.state_lock:self.job=dict(id=uuid.uuid4().hex,action=action,state='running',output='',error=None,started_at=time.time())
        def run():
            try:
                if action in ('plan','start','cancel','recover','ingest','portrait','configure','save_modifiers','save_loot_filter','save_preferences','settle_partial','accept_position',
                              'wishlist_add','wishlist_remove','worker_learn','worker_rename','worker_start','worker_cancel','worker_transfer','worker_settle_partial'):
                    self.action(action,args)
                else:
                    with self.lock:self.action(action,args)
                with self.state_lock:self.job.update(state='done',finished_at=time.time())
            except (Exception,SystemExit) as e:
                with self.state_lock:self.job.update(state='error',error=str(e),finished_at=time.time())
            finally:
                try:self.refresh_profiles()
                finally:self.job_lock.release()
        threading.Thread(target=run,daemon=True).start()
        return {'accepted':True}

    def selected(self,args):
        slot=args.get('slot'); c=next((c for c in characters(self.data) if c['slot']==slot),None)
        require(c is not None,'Character not found. Refresh the list.'); return c

    def profile(self,args):
        self.refresh_profiles();p=next((p for p in self.profiles if p['id']==args.get('profile')),None)
        require(p is not None,'Calibration profile not found.');require(p['usable'],' · '.join(p['problems']))
        require(same_character(p['character'],self.selected(args)),'This profile belongs to another character.');return p

    def context_matches(self,p,s,loadout=True):
        # Claims pass loadout=False: gear, talents, levels or combat settings that
        # changed after the calibration never block delivery (the player's choice,
        # 2026-09-23); the calibrated pace still sets the rewards. Hero, game
        # version and region stay required: they decide who receives the rewards,
        # whether the recorded monsters are valid, and the drops' map.
        require(same_character(p['character'],s['character']),f"Load {p['character']['name']} (slot {p['character']['slot']+1}) to use this profile in the game. Currently loaded: {s['character']['name']}.")
        require(p['game_build']==s['game_build'],'The game version changed. Recalibration is required.')
        if loadout:require(reward_modifiers.context_matches(p.get('farm_context'),s.get('farm_context'),bool(p.get('reward_modifiers'))),'Gear, talents, level or combat settings changed. Recalibration is required.')

    def action(self,name,args):
        if name=='save_loot_filter':
            settings=loot_filter.normalize(args.get('settings'))
            afk.write_json(self.data/'loot-filter.json',settings)
            self.log('Vault transfer filter saved: '+loot_filter.describe(settings)+'. Items it keeps back stay in the expedition records.');return
        if name=='save_preferences':
            prefs=load_preferences(self.data);require(any(k in args for k in ('delivery_speed','ready_notification','filtered_items')),'Nothing to save.')
            if 'filtered_items' in args:
                mode=args.get('filtered_items');require(mode in FILTERED_ITEMS,'Choose convert or keep.');prefs['filtered_items']=mode
            if 'delivery_speed' in args:
                speed=args.get('delivery_speed');require(speed in afk.DELIVERY_SPEEDS,'Choose Normal, Fast or Maximum.');prefs['delivery_speed']=speed
            if 'ready_notification' in args:
                ready=args.get('ready_notification');require(type(ready) is bool,'Choose on or off.');prefs['ready_notification']=ready
            afk.write_json(self.data/'preferences.json',prefs)
            if 'delivery_speed' in args:self.log('Delivery speed saved: '+prefs['delivery_speed']+'. A paused delivery keeps its own speed.')
            if 'filtered_items' in args:
                self.log('Items your loot filter hides: '+('sold below Satanic, broken down like the Prospector from Satanic up' if prefs['filtered_items']=='convert'
                         else 'kept in the expedition records')+'. Applies to the next claim; a paused delivery keeps its own choice.')
            if 'ready_notification' in args:
                self.log('Windows notification when an expedition is ready: '+('on' if prefs['ready_notification'] else 'off')+'.')
                threading.Thread(target=self.sync_notification,daemon=True).start()
            return
        if name=='save_modifiers':
            settings=reward_modifiers.normalize(args.get('settings'))
            afk.write_json(self.data/'reward-modifiers.json',settings)
            self.log('Reward settings saved for future expeditions. Active expeditions keep their recorded settings.');return
        if name=='configure':
            path=Path(str(args.get('game_bin',''))).resolve();require((path/'Hero_Siege.exe').is_file(),'Select the bin folder containing Hero_Siege.exe.')
            cfg=read(self.data/'config.json',{}) or {};cfg['game_bin']=str(path);afk.write_json(self.data/'config.json',cfg);self.log('Game folder saved.');return
        if name=='install':
            from game_session import running
            b=self.game_bin();require(running(b/'Hero_Siege.exe') is None,'Close the game before installing the plugin.')
            setup=self.installation()
            require(setup.get('verified_build'),'This executable has not been verified for automatic setup.')
            require(setup['aurie'] and setup['yytk'],'Aurie and YYToolkit are required. Install both components first.')
            source=ROOT/'plugin_build/HSAfkExpeditionPlugin.dll';require(source.is_file(),'AFK plugin not found in the distribution.')
            target=b/'mods/aurie/HSAfkExpeditionPlugin.dll'
            if target.exists():
                backup=self.data/'plugin-backups'/f'{int(time.time())}-HSAfkExpeditionPlugin.dll';backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
            shutil.copy2(source,target);self.log('AFK plugin installed. The previous DLL was backed up in the data folder.');return
        if name in ('launch','close'):
            from game_session import Session
            session=Session(self.game_bin(),self.data)
            if name=='close':self.log(json.dumps(session.close()));self.live=None;self.game_running=False
            else:
                c=self.selected(args);result=session.prepare(c['slot'],c['name'],c['class'],120);self.log(json.dumps(result,ensure_ascii=False));self.fresh()
            return
        if name=='restart_region':
            from game_session import Session,travel_target
            s=self.fresh();c=self.selected(args)
            require(same_character(c,s['character']),'Load the selected hero first.')
            require(not s['replay_running'],'Wait for reward delivery to finish.')
            match=re.fullmatch(r'Act_(\d{2})_\d{2}',s['room']);require(match,'Enter a regular Act region first.')
            town=travel_target('Town_'+match[1]+'_rm')
            session=Session(self.game_bin(),self.data)
            session.travel(town,c['slot'],c['name'],c['class'],120)
            session.travel(s['room'],c['slot'],c['name'],c['class'],120)
            self.fresh();self.log('Region restarted through town. Equipment and skills were not changed.');return
        if name in ('capture_start','validate_start'):
            s=self.fresh();c=self.selected(args);require(same_character(c,s['character']),'The selected character is not loaded in the game.')
            require(re.fullmatch(r'Act_\d{2}_\d{2}',s['room']),'Enter a regular Act region to calibrate.')
            require(not s['replay_running'],'Calibration cannot start while rewards are being delivered.')
            require(not s.get('capture_on'),'Finish the current recording first.')
            require((s.get('reward_baseline') or {}).get('available'),(s.get('reward_baseline') or {}).get('error','Update the AFK plugin before calibrating.'))
            reference=None
            if name=='validate_start':
                p=self.profile(args);self.context_matches(p,s)
                require(p['room']==s['room'],'Return to the profile region first.')
                reference=self.data/'validations'/('validation_'+uuid.uuid4().hex+'.reference.json')
                validate_farm.freeze(p,reference)
                time.sleep(1.05)  # Capture timestamps have one-second resolution.
            self.cli('capture','auto','off');self.cli('capture','off');self.cli('capture','on');s=self.fresh()
            require(s.get('capture_on') and s.get('capture_file'),'Could not start calibration. Check the activity log.')
            self.calibration=dict(session=s['capture_file'],room=s['room'],character=s['character'],started_at=s['captured_at'])
            if reference:self.calibration['validation_reference']=str(reference)
            afk.write_json(self.data/'panel-calibration.json',self.calibration);return
        if name=='capture_stop':
            require(self.calibration.get('session'),'No calibration was started from this panel.')
            self.cli('capture','auto','off');self.cli('capture','off')
            self.calibration['stopped_at']=datetime.now(timezone.utc).isoformat()
            self.calibration.pop('save_error',None)
            self.calibration['stopped_without_profile']=args.get('save_profile') is False
            afk.write_json(self.data/'panel-calibration.json',self.calibration)
            if self.calibration['stopped_without_profile']:
                self.log('Recording stopped without saving a profile. The raw recording is preserved.');return
            try:
                if self.calibration.get('validation_reference'):
                    reference=Path(self.calibration['validation_reference'])
                    result=validate_farm.evaluate(read(reference),Path(self.calibration['session']))
                    afk.write_json(reference.with_name(reference.name.replace('.reference.json','.result.json')),result)
                    self.log('Independent validation: '+result['status'])
                else:
                    self.cli('profile','build','--session',self.calibration['session'],'--room',self.calibration['room'],
                             '--min-seconds',str(calibration.MIN_PROFILE_SECONDS),'--min-events',str(calibration.MIN_PROFILE_KILLS))
                    self.refresh_profiles()
                    outcome=(self.capture_view() or {}).get('outcome',{})
                    require(outcome.get('status')=='saved',outcome.get('message','No usable calibration profile was saved.'))
                    self.log('Calibration saved and checked: '+outcome['profile_id'])
            except (Exception,SystemExit) as error:
                self.calibration['save_error']=str(error)
                afk.write_json(self.data/'panel-calibration.json',self.calibration)
                raise
            return
        if name in ('plan','start'):
            # Starting freezes a saved profile and arms a local clock. It must not
            # require the game, another hero's context, or a responsive IPC channel.
            p=self.profile(args)
            require(not afk.armed_for_hero(afk.load_state(self.data/'state.json'),p['character']),
                    "This hero's expedition is already active. Claim or cancel it first; other heroes can start their own.")
            hours=float(args.get('hours',1));require(math.isfinite(hours) and .25<=hours<=8,'Duration must be between 15 minutes and 8 hours.')
            mode=args.get('mode','farm');require(mode in ('farm','siege'),'Choose an expedition or a Siege.')
            ident=mode+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
            modifiers=reward_modifiers.load(self.data/'reward-modifiers.json')
            hero=(p.get('character') or {}).get('name')
            if mode=='siege':
                import siege
                level=args.get('siege_level')
                plan=siege.build_plan(p,level,hours,ident,modifiers,specials=self.verified_for(p))
                label='Siege L'+str(level)+' · '+expedition_label(p['room'],plan['hours'],hero)
            else:
                plan=afk.make_plan(hours,[(p['room'],1)],ident,40,'pickup',profile_overrides={p['room']:p})
                reward_modifiers.apply_to_plan(plan,p,modifiers)
                afk.rebuild_preview(plan)
                label=expedition_label(p['room'],hours,hero)
            plan['farm_context']=p['farm_context'];plan['panel_version']=VERSION
            plan.update(label=label,label_hero=hero,label_region=zone_names().get(p['room'],p['room']))
            path=self.data/'plans'/f'{ident}.json';afk.write_json(path,plan)
            self.cli('start',path)
            if mode=='siege':self.log(f"Siege L{plan['siege']['level']} started. Waves come every {plan['siege']['wave_minutes']} minutes; the report is ready when the gate falls or the time is up.")
            return
        if name=='claim':
            armed=self.target_armed(args,self.current_live())
            plan=read(Path(armed['plan']));require(plan,'Could not read the expedition plan.')
            review=recovery.inspect(self.data,armed['expedition_id']+'_claim')
            if review['recoverable']:
                with recovery.data_lock(self.data):recovery.settle(self.data,review['id'])
                self.log('Recovered the saved claim. No rewards were replayed. Transfer queued items from Loot.');return
            pr=progress_view(self.data,armed['expedition_id']+'_claim')
            require(not pr.get('reconciliation_required'),'The claim has an uncertain outcome. Rewards were not repeated. The records need review.')
            if pr.get('state') in ('running','paused','aborted') and not pr.get('resumable'):raise ValueError('The previous claim is incomplete. It was not retried automatically.')
            s=self.fresh();self.context_matches(plan,s,loadout=False)
            require(s['room']==plan['zones'][0]['room'],'Return to the calibrated region to claim rewards: '+plan['zones'][0]['room'])
            if pr.get('resumable'):
                # A started delivery is bound to the loadout it began with (its plan is fixed).
                claim=read(self.data/'plans'/f"{armed['expedition_id']}_claim.json",{}) or {}
                require(not claim.get('farm_context') or reward_modifiers.context_matches(claim['farm_context'],s.get('farm_context'),bool(claim.get('reward_modifiers'))),
                        'This paused delivery began with another loadout, level or settings. Restore them to continue it, or close it as a partial delivery.')
            self.claim(armed,args.get('speed'));return
        if name=='claim_background':
            self.claim_background(args);return
        if name=='recover':
            armed=self.target_armed(args,self.current_live())
            with recovery.data_lock(self.data):recovery.settle(self.data,armed['expedition_id']+'_claim')
            self.log('Saved rewards reconciled without replay. Queued items can be transferred from Loot.');return
        if name=='settle_partial':
            # The player keeps what an interrupted claim delivered and gives up the
            # rest. Nothing is generated again; the records stay for the report.
            live=self.current_live();armed=self.target_armed(args,live)
            require(not (live and live.get('replay_running')),'Delivery is running in the game. Pause it first.')
            with recovery.data_lock(self.data):result=recovery.settle_partial(self.data,armed['expedition_id']+'_claim')
            self.log(f"Claim closed as a partial delivery ({result.get('calls_done',0):,} of {result.get('calls_total',0):,} reward calls). "
                     'Delivered rewards stay; nothing was generated again. The expedition clock is free.');return
        if name=='accept_position':
            # The player accepts the recorded position of a delivery a crash cut
            # short; the item records end exactly there. Nothing runs here.
            live=self.current_live();armed=self.target_armed(args,live)
            require(not (live and live.get('replay_running')),'Delivery is running in the game. Pause it first.')
            with recovery.data_lock(self.data):p=recovery.accept_position(self.data,armed['expedition_id']+'_claim')
            self.recovery_cache=None
            self.log(f"Recorded position accepted ({p['calls_done']:,} of {p['calls_total']:,} reward calls). "
                     'Claim with the same hero in the same region to deliver the rest. Delivered calls are not repeated.');return
        if name=='cancel':
            armed=self.target_armed(args)
            require(not progress_view(self.data,armed['expedition_id']+'_claim').get('state'),'An expedition cannot be cancelled here once delivery has started.')
            self.cli('cancel','--expedition',armed['expedition_id']);return
        if name=='ingest':
            ident=args.get('id','');require(bool(IDENTIFIER.fullmatch(ident)),'Invalid reward ID.')
            spool=self.data/'spool'/f'{ident}.ndjson';require(spool.is_file(),'Reward file not found.')
            review=recovery.inspect(self.data,ident);settled=read(self.data/'sessions'/f'{ident}.result.json',{}) or {}
            require(review['recoverable'] or (settled.get('partial') is True and settled.get('settled_by')=='player'),
                    'Only complete, saved and consistent rewards, or a claim you closed as partial, can be transferred.')
            plan=read(self.data/'plans'/f'{ident}.json',{}) or {}
            label=plan.get('label') or expedition_label((plan.get('zones') or [{}])[0].get('room',''),float(plan.get('hours') or 0)*float(plan.get('scale') or 1),
                                                          (plan.get('character') or {}).get('name'))
            with recovery.data_lock(self.data):self.cli('ingest',spool,'--label',label)
            path=self.data/'sessions'/f'{ident}.result.json';result=read(path)
            if result:
                result.setdefault('stages',{})['ingest']='done'
                result['success']=afk.run_succeeded(result)
                afk.write_json(path,result)
            return
        if name=='portrait':
            c=self.selected(args);raw=base64.b64decode(args.get('png',''),validate=True)
            require(32<=len(raw)<=4*1024*1024 and raw.startswith(b'\x89PNG\r\n\x1a\n'),'Choose a PNG screenshot, up to 4 MB.')
            import struct
            width,height=struct.unpack('>II',raw[16:24]);require(0<width<=4096 and 0<height<=4096,'Portrait dimensions must be 4096 pixels or smaller.')
            path=self.data/'portraits'/(self.portrait_key(c)+'.png');path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw);self.log('Character screenshot saved locally. It does not change the game save.');return
        if name.startswith('worker_'):return self.worker_action(name,args)
        if name=='verify_special':
            ident=args.get('hash','');require(isinstance(ident,str) and bool(re.fullmatch(r'[a-f0-9]{12,64}',ident)),'Choose a special monster to verify.')
            s=self.fresh();require(not s['replay_running'],'Wait for reward delivery to finish.')
            self.cli('special','verify',ident,'--runs','3');self.refresh_profiles();return
        if name=='wishlist_add':
            collection.wishlist_add(self.data,self.collection.catalog,args.get('key'))
            self.log('Added to the wishlist: '+self.collection.catalog.by_key[args['key']]['name']+'. You get a Windows notification when an expedition brings it.');return
        if name=='wishlist_remove':
            collection.wishlist_remove(self.data,args.get('key'));self.log('Removed from the wishlist.');return
        raise ValueError('Unknown action.')

    def verified_for(self,p):
        """Verified special monster packets of this calibration's region and build."""
        verified=afk.verified_specials()
        return [q['hash'] for q in p.get('packets',[]) if q.get('kind')=='kill' and q.get('hash') in verified
                and verified[q['hash']].get('room')==p.get('room') and verified[q['hash']].get('build')==p.get('game_build')]

    def siege_forecast(self,query):
        """/api/siege-forecast?profile=ID&level=L&hours=H: what a Siege there usually looks like."""
        import siege
        from urllib.parse import parse_qs
        values=parse_qs(query or '');ident=(values.get('profile') or [''])[0]
        p=next((p for p in self.profiles if p['id']==ident and p['usable']),None);require(p is not None,'Choose a usable calibration.')
        try:level=int((values.get('level') or ['0'])[0]);hours=float((values.get('hours') or ['2'])[0])
        except ValueError:raise ValueError('Invalid Siege level or duration.')
        require(1<=level<=siege.MAX_LEVEL and math.isfinite(hours) and .25<=hours<=afk.MAX_HOURS,'Invalid Siege level or duration.')
        result=siege.forecast(p,level,hours,specials=self.verified_for(p))
        hero=afk.hero_key(p['character'])
        result.update(suggested_level=siege.suggest_level(p,hours),best_waves=siege.best(self.data,hero,p['room'],level) if hero else 0,
                      wave_minutes=siege.WAVE_MINUTES,max_level=siege.MAX_LEVEL)
        return result

    def specials_view(self):
        """/api/specials: special monsters met in calibrations, and whether each may replay."""
        verified=afk.verified_specials();rows={}
        for p in self.profiles:
            for q in p.get('packets',[]):
                if q.get('kind')!='kill' or q.get('rank') in afk.ORDINARY_RANKS:continue
                row=rows.setdefault(q['hash'],dict(hash=q['hash'],monster_key=q.get('monster_key'),rank=q.get('rank'),room=p.get('room'),
                                                   region=zone_names().get(p.get('room'),p.get('room')),kills=0,profiles=[],
                                                   verified=q['hash'] in verified,verification=verified.get(q['hash'])))
                row['kills']+=q.get('count',0);row['profiles'].append(p['id'])
        return dict(specials=sorted(rows.values(),key=lambda r:(r['room'] or '',r['monster_key'] or '')))

    def siege_records(self,character):
        import siege
        hero=afk.hero_key(character) if character else None
        return (siege.load_records(self.data)['heroes'].get(hero) or {}) if hero else {}

    # ------------------------------------------------------------ workers
    def workers_view(self):
        """The crew in /api/state: levels, points and trips (details: /api/workers)."""
        state=workers.load(self.data)
        crew=[]
        for w in state['workers']:
            v=workers.view(w)
            crew.append(dict((k,v[k]) for k in ('id','name','type','level','xp_into_level','xp_for_next','points','max_trip_hours','trip')))
        return dict(crew=crew,hire_price=workers.hire_price(state),max_workers=workers.MAX_WORKERS,
                    pending_payments=[dict(request_id=k,**{x:v.get(x) for x in ('purpose','amount','state','error')})
                                      for k,v in state.get('payments',{}).items() if v.get('state') in ('pending','unknown','refused')][-5:])

    def workers_overview(self):
        return workers.overview(workers.load(self.data))

    def worker_entries(self):
        entries=[]
        for w in workers.load(self.data)['workers']:
            trip=workers.trip_view(w)
            if trip and not trip['ready']:
                entries.append(notify.entry('worker-'+w['id'],afk.parse_iso(trip['ready_at']),'AFK FARM: haul ready',
                               f"{w['name']} is back from the mine with {trip['ore_name']}. Open AFK FARM and collect it with any offline hero loaded."))
        return entries

    # A payment request gets exactly one receipt, written by the game when it runs
    # the command, and the game never runs one request twice. A reply that times
    # out is therefore not a refusal: the command may still run, so the payment
    # stays 'unknown' until its receipt appears, the purchase is completed then
    # (once), and no new payment starts while one is unanswered.
    def payment_receipt(self,request):
        return read(self.data/'models'/f'worker-pay-{request}.json',{}) or {}

    def send_payment(self,request,amount):
        """Ask the game to run one request: its receipt, {} if the game answered without one, None if it did not answer."""
        reply=afk.Ipc(self.game_bin()).send(f'afk worker pay {request} {int(amount)}',timeout=60)
        receipt=self.payment_receipt(request)
        return receipt if receipt or reply is not None else None

    def record_payment(self,request,receipt):
        """Store what the game did with a request; a paid purchase is completed exactly once."""
        state=workers.load(self.data);entry=state['payments'][request]
        if receipt is None:
            entry.update(state='unknown',error='the game did not answer in time')
        else:
            entry.update(state='paid' if receipt.get('ok') is True else 'refused',error=receipt.get('error') or (None if receipt else 'no receipt from the game'),
                         receipt=receipt or None)
            if entry['state']=='paid' and not entry.get('applied'):
                self.log(f"Paid {entry['amount']:,} gold ({receipt.get('gold_before'):,.0f} -> {receipt.get('gold_after'):,.0f}); the game saved.")
                self.apply_payment(state,request,entry,receipt);entry['applied']=True
        workers.save(self.data,state)
        return entry

    def apply_payment(self,state,request,entry,receipt):
        if entry['purpose']=='hire' and not any((w.get('payment') or {}).get('request_id')==request for w in state['workers']):
            w=workers.new_worker(state,entry.get('name'),payment=dict(request_id=request,amount=entry['amount'],at=receipt.get('at'),character=receipt.get('character')))
            self.log(f"{w['name']} joined your crew. Send them on a trip from the Workers page.")
        elif entry['purpose']=='respec':
            w=workers.respec(workers.find(state,entry.get('worker')))
            self.log(f"{w['name']}'s skills were reset; every point can be spent again.")

    def unanswered_payments(self):
        return [k for k,v in workers.load(self.data)['payments'].items() if v.get('state') in ('pending','unknown')]

    def settle_payments(self):
        """Complete payments whose receipt arrived after the panel stopped waiting (no game call)."""
        for request in self.unanswered_payments():
            receipt=self.payment_receipt(request)
            if receipt:self.record_payment(request,receipt)

    def settle_late_payments(self):
        # From the monitor: only between actions, so the worker records have one writer at a time.
        try:
            if not self.unanswered_payments() or not self.job_lock.acquire(blocking=False):return
            try:self.settle_payments()
            finally:self.job_lock.release()
        except Exception as error:
            if str(error)!=getattr(self,'settle_error',None):self.settle_error=str(error);self.log(f'An earlier payment is not settled yet: {error}')

    def finish_earlier_payments(self):
        """Before a new payment: settle earlier ones, sending a still unanswered one again under its own
        request id. True when an earlier purchase was completed now (the new one is not made)."""
        self.settle_payments();completed=False
        for request in self.unanswered_payments():
            entry=workers.load(self.data)['payments'][request]
            self.log(f"Finishing an earlier payment of {entry['amount']:,} gold first...")
            entry=self.record_payment(request,self.send_payment(request,entry['amount']))
            require(entry['state']!='unknown','The game has not answered an earlier payment yet. Nothing new was charged; try again with an offline hero loaded.')
            completed=completed or entry['state']=='paid'
        if completed:self.log('The earlier purchase was completed; nothing else was charged.')
        return completed

    def worker_pay(self,purpose,amount,extra=None):
        """Take ``amount`` gold from the loaded hero through the game's purchase path and complete the purchase.
        Returns None, and charges nothing new, when an earlier unanswered purchase was completed instead."""
        s=self.fresh();require(not s['replay_running'],'Wait for reward delivery to finish.')
        if self.finish_earlier_payments():return None
        state=workers.load(self.data);request=uuid.uuid4().hex
        state['payments'][request]=dict(purpose=purpose,amount=amount,state='pending',at=datetime.now(timezone.utc).isoformat(),
                                        character=s['character'],**(extra or {}))
        workers.save(self.data,state)
        self.log(f"Paying {amount:,} gold from {s['character']['name']} in the game...")
        entry=self.record_payment(request,self.send_payment(request,amount))
        require(entry['state']!='unknown','The game did not answer in time. If it takes the gold, AFK FARM completes the purchase by itself; '
                'it is never charged twice.')
        require(entry['state']=='paid','The game did not take the gold: '+(entry.get('error') or 'no receipt came back')+'. Nothing was bought.')
        return request,entry['receipt']

    def collect_worker(self,worker_id):
        """Deliver one worker's haul through the game, then send it to the Vault."""
        state=workers.load(self.data);w=workers.find(state,worker_id);trip=w.get('trip')
        require(trip,f"{w['name']} is not on a trip.")
        if trip.get('delivery'):
            path=Path(trip['delivery']['plan']);plan=read(path,{}) or {}
            require(plan.get('delivery_id')==trip['delivery']['delivery_id'],'The planned haul is missing; it was not made again.')
        else:
            view=workers.trip_view(w);plan=workers.delivery_plan(w,trip,view['credited_work_hours'])
            require(plan['items'] or plan['prospect'],f"{w['name']} has not mined anything yet.")
            path=self.data/'plans'/f"{plan['delivery_id']}.json";afk.write_json(path,plan)
            trip['delivery']=dict(delivery_id=plan['delivery_id'],plan=str(path),planned_at=plan['planned_at'])
            workers.save(self.data,state)
        ident=plan['delivery_id'];result_path=self.data/'sessions'/f'{ident}.result.json'
        if not result_path.exists():
            s=self.fresh();require(not s['replay_running'],'Wait for reward delivery to finish.')
            self.log(f"Delivering {w['name']}'s haul through the game...")
            reply=afk.Ipc(self.game_bin()).send(f'afk worker deliver {path}',timeout=120) or []
            for line in reply:self.log(line)
        result=read(result_path,{}) or {}
        if not result:
            require(not (self.data/'spool'/f'{ident}.ndjson').exists(),
                    'An earlier attempt stopped without a result. What it made stays in its records; close the haul as partial to keep it.')
            raise ValueError('The game gave no result for the haul. Keep an offline hero loaded and collect again; nothing was made.')
        require(result.get('state')=='done',f"The haul stopped part way ({result.get('error') or 'no reason given'}). What was made stays in its records; "
                'nothing is made twice. Close the haul as partial to keep that part.')
        state=workers.load(self.data)
        applied=workers.apply_delivery(state,worker_id,plan,result);workers.save(self.data,state)
        w=applied['worker']
        self.log(f"{w['name']}: +{plan['xp']:,} XP" + (f", now level {w['level']}" if applied['levels'] else '') + f"; {plan['ore_total']:,} ore mined.")
        self.transfer_worker_haul(ident,w['name'])
        return applied

    def transfer_worker_haul(self,ident,name=None):
        """Send a delivered haul to the Vault (AFK Materials); retry later if the editor is closed."""
        spool=self.data/'spool'/f'{ident}.ndjson'
        if not spool.is_file():return False
        label='AFK · Workers · '+(name or 'Miner')+' · '+datetime.now().strftime('%Y-%m-%d')
        try:
            with recovery.data_lock(self.data):self.cli('ingest',spool,'--label',label)
            ok=True
        except ValueError as error:
            self.log(f'The haul waits for the Vault: {error}');ok=False
        path=self.data/'sessions'/f'{ident}.result.json';result=read(path,{}) or {}
        result['ingest']='done' if ok else 'pending';afk.write_json(path,result)
        return ok

    def settle_worker_partial(self,worker_id):
        """Keep what a stopped delivery made, give up the rest; nothing is made again."""
        state=workers.load(self.data);w=workers.find(state,worker_id);trip=w.get('trip')
        require(trip and trip.get('delivery'),f"{w['name']} has no stopped haul to close.")
        plan=read(Path(trip['delivery']['plan']),{}) or {};ident=plan.get('delivery_id') or trip['delivery']['delivery_id']
        path=self.data/'sessions'/f'{ident}.result.json';result=read(path,{}) or {}
        require(result.get('state')!='done','This haul was delivered; collect it instead.')
        made={}
        spool=self.data/'spool'/f'{ident}.ndjson'
        for row in (afk.read_ndjson(spool) if spool.exists() else []):
            item=row.get('item') or {}
            if row.get('kind')!='item' or not isinstance(item,dict):continue
            d=item.get('itemDefinitionStruct') or {}
            key=f"{int(item.get('itemType',-1))}:{int(d.get('b',-1))}";made[key]=made.get(key,0)+int(d.get('o',1) or 1)
        asked=sum(i['amount'] for i in plan.get('items',[]))+sum((plan.get('prospect') or {}).values())
        share=min(1.0,sum(made.values())/asked) if asked else 0.0
        kept=dict(plan,xp=int(plan.get('xp',0)*share),ore_total=int(plan.get('ore_total',0)*share),digs=int(plan.get('digs',0)*share),prospect={})
        result=dict(result,delivery_id=ident,state='partial',partial=True,created=made,settled_by='player',
                    settled_at=datetime.now(timezone.utc).isoformat())
        afk.write_json(path,result)
        workers.apply_delivery(state,worker_id,kept,result);workers.save(self.data,state)
        self.log(f"{w['name']}'s haul closed as partial: {sum(made.values()):,} of {asked:,} units were made and kept.")
        if made:self.transfer_worker_haul(ident,w['name'])

    def collect_ready_workers(self):
        """After a claim, with the game still open: deliver every finished trip."""
        for w in workers.load(self.data)['workers']:
            trip=workers.trip_view(w)
            if trip and trip['ready']:
                try:self.collect_worker(w['id'])
                except (Exception,SystemExit) as error:self.log(f"{w['name']}'s haul was not collected: {error}")

    def worker_action(self,name,args):
        state=workers.load(self.data)
        if name=='worker_hire':
            price=workers.hire_price(state);require(price,f'Your crew is full ({workers.MAX_WORKERS} workers).')
            wanted=str(args.get('name') or '').strip() or None
            if wanted:require(bool(workers.NAME.fullmatch(wanted)),'Names use letters, digits, spaces, apostrophes and hyphens (up to 24).')
            self.worker_pay('hire',price,dict(name=wanted));return
        if name=='worker_respec':
            w=workers.find(state,args.get('worker'));require(workers.spent(w),f"{w['name']} has no skill points to reset.")
            self.worker_pay('respec',workers.respec_price(w),dict(worker=w['id']));return
        if name=='worker_learn':
            w=workers.learn(state,args.get('worker'),args.get('skill'));workers.save(self.data,state)
            self.log(f"{w['name']} learned {workers.NODES[args['skill']]['name']} (rank {workers.ranks(w,args['skill'])}).");return
        if name=='worker_rename':
            w=workers.rename(state,args.get('worker'),args.get('name'));workers.save(self.data,state);self.log(f"Renamed to {w['name']}.");return
        if name=='worker_start':
            trip=workers.start_trip(state,args.get('worker'),args.get('ore'),args.get('hours',1));workers.save(self.data,state)
            w=workers.find(state,args.get('worker'))
            self.log(f"{w['name']} went mining {workers.ORE_BY_ID[trip['ore']]['name']} for {trip['work_hours']:g} h of work (back in {trip['real_hours']:.2f} h).")
            threading.Thread(target=self.sync_notification,daemon=True).start();return
        if name=='worker_cancel':
            workers.cancel_trip(state,args.get('worker'));workers.save(self.data,state);self.log('Trip cancelled; nothing was mined.')
            threading.Thread(target=self.sync_notification,daemon=True).start();return
        if name=='worker_collect':
            targets=[args['worker']] if args.get('worker') else [w['id'] for w in state['workers'] if (workers.trip_view(w) or {}).get('ready')]
            require(targets,'No haul is ready yet.')
            for ident in targets:self.collect_worker(ident)
            threading.Thread(target=self.sync_notification,daemon=True).start();return
        if name=='worker_settle_partial':
            self.settle_worker_partial(args.get('worker'));return
        if name=='worker_transfer':
            ident=str(args.get('delivery',''));require(bool(IDENTIFIER.fullmatch(ident)) and ident.startswith('worker_'),'Invalid haul.')
            result=read(self.data/'sessions'/f'{ident}.result.json',{}) or {};require(result.get('state') in ('done','partial'),'Only a delivered haul can be transferred.')
            require(self.transfer_worker_haul(ident),'The Vault transfer did not complete. Open Item Editor and try again.');return
        raise ValueError('Unknown action.')

    def collection_summary(self):
        found,_=self.collection.log(self.expedition_results())
        return dict(total=len(self.collection.catalog.items),found=len(found),wishlist=len(collection.load_wishlist(self.data)['items']))

    def collection_view(self):
        return self.collection.view(self.expedition_results(),collection.load_wishlist(self.data))

    def announce_wishlist(self,ident):
        """After a delivered claim: one Windows notification for its wishlist drops."""
        try:
            result=read(self.data/'sessions'/f'{ident}.result.json',{}) or {}
            if not (result.get('rewards_saved') is True or result.get('partial') is True):return
            plan=read(self.data/'plans'/f'{ident}.json',{}) or {}
            hits=collection.wishlist_hits(self.collection,ident,collection.load_wishlist(self.data))
            if hits and collection.notify_hits_once(self.data,ident,hits,lambda title,message:notify.toast(self.data,title,message),
                                                    (plan.get('character') or {}).get('name'),plan.get('label_region')):
                self.log('Wishlist drop: '+', '.join(h['name'] for h in hits)+'.')
        except Exception as error:
            self.log(f'Wishlist check skipped: {error}')

    def share_summary(self,ident):
        """What a share card shows for one delivered claim (read-only)."""
        require(isinstance(ident,str) and bool(IDENTIFIER.fullmatch(ident)),'Invalid reward ID.')
        result=read(self.data/'sessions'/f'{ident}.result.json',{}) or {};plan=read(self.data/'plans'/f'{ident}.json',{}) or {}
        require(plan.get('panel_version') and (result.get('rewards_saved') is True or result.get('partial') is True),'Only a delivered expedition can be shared.')
        loot=self.presentation.loot(self.data,ident);ch=plan.get('character') or {}
        hero=next((c for c in self.chars if valid_identity(ch) and same_character(c,ch)),None) or {}
        sidecar=read(self.data/'sessions'/f'{ident}.panel.json',{}) or {}
        _,firsts=self.collection.log(self.expedition_results())
        by_key=self.collection.catalog.by_key
        new_finds=[dict(key=k,name=by_key[k]['name'],rarity=by_key[k]['rarity'],icon=by_key[k].get('icon')) for k in firsts.get(ident,[]) if k in by_key]
        done,total=result.get('calls_done') or 0,result.get('calls_total') or 0
        fraction=done/total if result.get('partial') and total else 1.0
        room=(plan.get('zones') or [{}])[0].get('room')
        best=[dict(b) for b in (loot.get('best') or [])[:8]]
        for b in best:   # names shared by a base item have no loot icon; a collectible's name is unique
            if not b.get('icon'):b['icon']=(self.collection.catalog.by_name.get(b.get('name')) or {}).get('icon')
        # The gold the claim brought: gold drops plus the filtered items the game sold
        # (MEASURED 2026-09-24: 808,164 dropped + 1,911,459 from sales; the card showed 808K).
        sales=int((result.get('conversion') or {}).get('sell_gold') or 0)
        gold=None if result.get('gold') is None else int(result['gold'])+sales
        return dict(schema=1,id=ident,version=VERSION,hero=dict(name=ch.get('name'),class_name=hero.get('class_name') or CLASSES.get(ch.get('class')),
                    level_before=sidecar.get('level_before'),level_now=hero.get('level')),region=zone_names().get(room,room),room=room,
                    mode=plan.get('mode','farm'),siege=plan.get('siege_claim'),hours=float(plan.get('hours') or 0)*float(plan.get('scale') or 1)*fraction,
                    kills=(plan.get('preview') or {}).get('kills'),exp=result.get('exp'),gold=gold,gold_drops=result.get('gold'),gold_sales=sales,items=loot.get('total'),
                    visible_rarities=loot.get('visible_rarities'),best=best,partial=bool(result.get('partial')),
                    wishlist_hits=collection.wishlist_hits(self.collection,ident,collection.load_wishlist(self.data)),
                    new_finds=new_finds[:8],new_finds_total=len(new_finds),delivered_at=result.get('updated') or result.get('settled_at'),
                    delivery_seconds=delivery_seconds(result))

    def claim(self,armed,speed=None):
        """Start or continue delivery; remember the hero's level for the summary."""
        speed=speed if speed in afk.DELIVERY_SPEEDS else load_preferences(self.data)['delivery_speed']
        ident=armed['expedition_id']+'_claim';sidecar=self.data/'sessions'/f'{ident}.panel.json'
        if not sidecar.exists():
            plan=read(Path(armed['plan']),{}) or {};ch=plan.get('character')
            hero=next((c for c in characters(self.data) if valid_identity(ch) and same_character(c,ch)),None)
            afk.write_json(sidecar,dict(level_before=hero.get('level') if hero else None,speed=speed,at=datetime.now(timezone.utc).isoformat()))
        try:self.cli('claim','--expedition',armed['expedition_id'],'--speed',speed,'--filtered',load_preferences(self.data)['filtered_items'])
        finally:
            self.announce_wishlist(ident)
            self.collect_ready_workers()

    def claim_background(self,args):
        """Open the game minimized, load the hero in its region, deliver at Maximum speed, close the game.

        Uses the verified automatic setup (game_session): no mouse or keyboard
        input, the game's own menu and travel routines. The game is closed again
        only when this action opened it and delivery finished or paused safely.
        """
        from game_session import Session,running
        armed=self.target_armed(args,self.current_live())
        plan=read(Path(armed['plan']));require(plan,'Could not read the expedition plan.')
        ident=armed['expedition_id']+'_claim'
        review=recovery.inspect(self.data,ident)
        require(review['status'] in ('not_started','paused') or review['recoverable'],'The previous claim needs review before another delivery.')
        view=self.background_view(armed,progress_view(self.data,ident),review,self.current_live())
        require(view['available'],view['reason'] or 'Claim in background is unavailable.')
        c=plan['character'];region=plan['zones'][0]['room']
        session=Session(self.game_bin(),self.data)
        launched=running(session.exe) is None
        live=None
        if not launched:
            try:live=self.fresh()
            except (ValueError,RuntimeError):live=None
        if not (live and same_character(live.get('character'),c) and live.get('room')==region):
            self.log(f"Preparing {c['name']} in the background{' (starting the game minimized)' if launched else ''}...")
            self.log(json.dumps(session.prepare(c['slot'],c['name'],c['class'],240),ensure_ascii=False)[:2000])
            self.log('Travelling to '+zone_names().get(region,region)+'...')
            self.log(json.dumps(session.travel(region,c['slot'],c['name'],c['class'],240),ensure_ascii=False)[:2000])
        s=self.fresh();self.context_matches(plan,s,loadout=False)
        require(s['room']==region,'The hero did not arrive in the expedition region.')
        self.claim(armed,'max')
        pr=progress_view(self.data,ident)
        if launched and (pr.get('state')=='done' or pr.get('resumable')):
            self.log('Closing the game that this action opened...')
            self.log(json.dumps(session.close()))
            self.live=None;self.game_running=False

def focus_query(query):
    """/api/state?slot=N or ?expedition=ID: which expedition the top-level fields describe."""
    from urllib.parse import parse_qs
    values=parse_qs(query or '');focus={}
    slot=(values.get('slot') or [''])[0]
    if re.fullmatch(r'\d{1,4}',slot):focus['slot']=int(slot)
    ident=(values.get('expedition') or [''])[0]
    if IDENTIFIER.fullmatch(ident):focus['expedition']=ident
    return focus


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    @property
    def app(self):return self.server.app
    def send(self,status,body,kind='application/json; charset=utf-8'):
        if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        elif isinstance(body,str):body=body.encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        try:
            self.end_headers();self.wfile.write(body)
        except ConnectionError:
            # Closing/reloading the browser may cancel an in-flight state poll.
            # The response is abandoned; do not try to send a second HTTP error.
            pass
    def allowed(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def do_GET(self):
        if not self.allowed():return self.send(403,{'error':'Host rejected'})
        try:
            route=urlsplit(self.path).path
            if route=='/api/state':return self.send(200,self.app.snapshot(focus_query(urlsplit(self.path).query)))
            if route=='/api/instance':return self.send(200,dict(application='hero-siege-afk-farm',version=VERSION))
            if route=='/api/collection':return self.send(200,self.app.collection_view())
            if route=='/api/siege-forecast':return self.send(200,self.app.siege_forecast(urlsplit(self.path).query))
            if route=='/api/specials':return self.send(200,self.app.specials_view())
            if route=='/api/workers':return self.send(200,self.app.workers_overview())
            if route=='/api/share':
                from urllib.parse import parse_qs
                return self.send(200,self.app.share_summary(parse_qs(urlsplit(self.path).query).get('id',[''])[0]))
            if route=='/api/recovery-report':
                from urllib.parse import parse_qs
                ident=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                report=recovery.inspect(self.app.data,ident)
                report['checkpoint']=read(self.app.data/'sessions'/f'{ident}.progress.json')
                report['failure']=read(self.app.data/'sessions'/f'{ident}.failure.json')
                return self.send(200,report)
            if route.startswith('/portraits/'):
                name=route.rsplit('/',1)[-1];require(bool(re.fullmatch('[a-f0-9]{64}\\.png',name)),'Invalid portrait')
                return self.send(200,(self.app.data/'portraits'/name).read_bytes(),'image/png')
            if route in ('/','/index.html'):
                return self.send(200,(WEB/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',self.app.token),'text/html; charset=utf-8')
            path=(WEB/unquote(route.lstrip('/'))).resolve()
            require(path.is_relative_to(WEB.resolve()) and path.is_file(),'File not found')
            kind={'.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.svg':'image/svg+xml','.json':'application/json; charset=utf-8','.woff2':'font/woff2'}.get(path.suffix,'application/octet-stream')
            self.send(200,path.read_bytes(),kind)
        except (OSError,ValueError) as e:self.send(404,{'error':str(e)})
    def do_POST(self):
        origin=self.headers.get('Origin')
        expected=f'http://{self.headers.get("Host","")}'
        if not self.allowed() or (origin and origin!=expected) or self.headers.get('X-AFK-Token')!=self.app.token:return self.send(403,{'error':'Request verification failed'})
        try:
            require(urlsplit(self.path).path=='/api/action','Unknown route')
            size=int(self.headers.get('Content-Length','0'));require(0<size<6*1024*1024,'Invalid request size')
            args=json.loads(self.rfile.read(size));require(isinstance(args,dict),'Invalid request')
            self.send(202,self.app.submit(args.pop('action',''),args))
        except (ValueError,TypeError) as e:self.send(400,{'error':str(e)})


class LocalPanelServer(ThreadingHTTPServer):
    # SO_REUSEADDR on Windows permits two listeners on one address. Never let
    # an older panel compete with an update for the same localhost port.
    allow_reuse_address=False
    def server_bind(self):
        import socket
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8787);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    # Reuse this version only. An older panel must not silently hide an update.
    import urllib.request
    occupied=set()
    for port in range(args.port,args.port+10):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/instance',timeout=.15) as response:
                occupied.add(port)
                instance=json.load(response)
            if instance.get('application')=='hero-siege-afk-farm' and instance.get('version')==VERSION:
                if not args.no_browser:webbrowser.open(f'http://127.0.0.1:{port}')
                return
        except (OSError,ValueError):pass
    app=Panel();server=None
    for port in range(args.port,args.port+10):
        if port in occupied:continue
        try:server=LocalPanelServer(('127.0.0.1',port),Handler);break
        except OSError:continue
    require(server,'Could not open a local panel port.');server.app=app
    url=f'http://127.0.0.1:{server.server_port}';notify.PANEL_URL=url+'/'   # the notifications' "Open AFK FARM" button
    threading.Thread(target=app.monitor,daemon=True).start()
    print(url,flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:app.closed.set();server.server_close()

if __name__=='__main__':main()
