"""Reconcile completed native saves without ever replaying rewards.

An interrupted, partial or contradictory claim is held for review. A completed
native checkpoint must match the exact plan bytes and complete item spool.
"""
import contextlib
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path


def read(path):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return None


@contextlib.contextmanager
def data_lock(data):
    data.mkdir(parents=True, exist_ok=True)
    with (data/'reward-operation.lock').open('a+b') as stream:
        if not stream.tell(): stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e: raise ValueError('Another reward operation is running.') from e
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


SAVED='saved (character and account save performed)'


def resumable(data, ident):
    """A paused claim the plugin can continue from its saved position.

    Pause, a closed game window or leaving the region with the expedition hero
    saves the game and writes an "aborted" or "paused" checkpoint with that
    save receipt. Only such a checkpoint, for the exact plan, without failed
    calls or a failure record, continues; everything else still needs review.
    """
    if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', ident): return False
    if (data/'sessions'/f'{ident}.failure.json').exists(): return False
    p=read(data/'sessions'/f'{ident}.progress.json') or {}
    plan_path=data/'plans'/f'{ident}.json'
    if p.get('state') not in ('aborted','paused') or p.get('checkpoint_version')!=2 or p.get('expedition_id')!=ident: return False
    if not plan_path.is_file() or p.get('plan_hash')!=hashlib.sha256(plan_path.read_bytes()).hexdigest(): return False
    done,total=p.get('calls_done'),p.get('calls_total')
    if type(done) is not int or type(total) is not int or not 0<=done<total: return False
    if p.get('failed')!=0 or p.get('skipped')!=0: return False
    saved=p.get('save_committed') is True and p.get('saved')==SAVED
    return saved or (p.get('saved')=='nothing to save' and done==0)


def inspect(data, ident):
    if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', ident): raise ValueError('Invalid claim ID.')
    p=data/'sessions'/f'{ident}.progress.json'
    plan_path=data/'plans'/f'{ident}.json'
    spool=data/'spool'/f'{ident}.ndjson'
    if not p.exists() and not spool.exists() and not (data/'sessions'/f'{ident}.failure.json').exists():
        return dict(id=ident,status='not_started',recoverable=False,resumable=False,reasons=[])
    reasons=[]
    progress=read(p) or {}
    plan=read(plan_path) or {}
    if (data/'sessions'/f'{ident}.failure.json').exists(): reasons.append('A native failure record requires review.')
    if progress.get('state')!='done': reasons.append('Native delivery has no completed checkpoint.')
    if progress.get('checkpoint_version')!=2 or progress.get('expedition_id')!=ident: reasons.append('Checkpoint identity or version is invalid.')
    if plan.get('expedition_id')!=ident or not plan_path.is_file(): reasons.append('The matching claim plan is missing.')
    elif progress.get('plan_hash')!=hashlib.sha256(plan_path.read_bytes()).hexdigest(): reasons.append('The plan differs from the native checkpoint.')
    counts=[q.get('count') for q in plan.get('packets',[])]
    valid_counts=bool(counts) and all(type(n) is int and n>=0 for n in counts)
    expected=sum(counts) if valid_counts else -1
    if expected<=0 or progress.get('calls_done')!=expected or progress.get('calls_total')!=expected:
        reasons.append('The recorded call counts are incomplete or inconsistent.')
    if progress.get('failed')!=0 or progress.get('skipped')!=0: reasons.append('Some reward calls failed or were skipped.')
    saved=progress.get('save_committed') is True and progress.get('saved')=='saved (character and account save performed)'
    if not saved: reasons.append('A completed character and account save is not confirmed. Older room-end-only receipts need review.')
    rows=[]
    try:
        for line in spool.read_text(encoding='utf-8').splitlines():
            if line.strip():
                row=json.loads(line)
                if not isinstance(row,dict): raise ValueError('Invalid record')
                rows.append(row)
    except (OSError,ValueError): reasons.append('The item spool is missing or contains unreadable records.')
    ids=[r.get('seq') for r in rows]
    if any(type(n) is not int or n<=0 for n in ids) or ids!=list(range(1,len(ids)+1)) or any(r.get('expedition_id')!=ident for r in rows):
        reasons.append('The spool has invalid, duplicate or foreign record identifiers.')
    summaries=[r for r in rows if r.get('kind')=='summary']
    items=[r for r in rows if r.get('kind')=='item']
    if not summaries or rows[-1].get('kind')!='summary': reasons.append('The final spool summary is missing.')
    else:
        summary=summaries[-1]
        if summary.get('calls')!=expected or summary.get('items')!=len(items) or progress.get('items')!=len(items):
            reasons.append('The spool and checkpoint reward counts disagree.')
    if any(not isinstance(r.get('item'),dict) for r in items): reasons.append('A native item record is incomplete.')
    for key in ('gold','exp'):
        n=progress.get(key)
        if type(n) not in (int,float) or not math.isfinite(n) or n<0: reasons.append('The '+key+' counter is invalid.')
        elif summaries and summaries[-1].get('gold' if key=='gold' else 'exp_credited')!=int(n):
            reasons.append('The '+key+' summary does not match the checkpoint.')
    if reasons and resumable(data,ident):
        return dict(id=ident,status='paused',recoverable=False,resumable=True,
                    reasons=['Delivery was paused at a saved position. Claim again with the same hero in the same region to continue.'],
                    items=len(items),calls_done=progress.get('calls_done'),calls_total=progress.get('calls_total'))
    return dict(id=ident,status='saved' if not reasons else 'needs_review',recoverable=not reasons,resumable=False,reasons=reasons,
                items=len(items),calls_done=progress.get('calls_done'),calls_total=progress.get('calls_total'))


def settle_partial(data, ident):
    """The player keeps what an interrupted claim delivered and gives up the rest.

    Only for a claim that needs review (not resumable, not a completed save). No
    reward is generated or repeated: the partial result is recorded as such, the
    armed clock is freed, and the checkpoint, spool and plan stay untouched for the
    recovery report. Whether the game saved the delivered XP is not confirmed.
    """
    from afk import write_json, iso, now_utc
    review=inspect(data,ident)
    if review['status']!='needs_review':raise ValueError('Only a claim that needs review can be closed as partial.')
    state=read(data/'state.json') or {}
    armed=state.get('armed')
    if not armed or armed.get('expedition_id')+'_claim'!=ident: raise ValueError('This is not the currently armed claim.')
    p=read(data/'sessions'/f'{ident}.progress.json') or {}
    if p.get('state') in ('running','paused') and (data/'sessions'/f'{ident}.progress.json').stat().st_mtime>time.time()-30:
        raise ValueError('Delivery still looks active. Pause it or close the game, then try again.')
    previous=read(data/'sessions'/f'{ident}.result.json') or {}
    done,total=p.get('calls_done') or 0,p.get('calls_total') or 0
    result=dict(p,state='partial',checkpoint_state=p.get('state'),rewards_saved=False,success=False,partial=True,settled_by='player',
                settled_at=iso(now_utc()),review_reasons=review['reasons'],
                stages=dict(replay='partial',save='unconfirmed',ingest='done' if previous.get('stages',{}).get('ingest')=='done' else 'pending'))
    write_json(data/'sessions'/f'{ident}.result.json',result)
    plan=read(data/'plans'/f'{ident}.json') or {}
    fraction=done/total if total else 0
    state['last_claim']=dict(expedition_id=ident,credited_hours=float(plan.get('hours',0))*float(plan.get('scale',1))*fraction,
                             at=iso(now_utc()),result=result,partial=True)
    state.pop('armed')
    write_json(data/'state.json',state)
    return result


def settle(data, ident):
    from afk import write_json, iso, now_utc
    review=inspect(data,ident)
    if not review['recoverable']: raise ValueError('Recovery refused: '+' '.join(review['reasons']))
    state=read(data/'state.json') or {}
    armed=state.get('armed')
    if not armed or armed.get('expedition_id')+'_claim'!=ident: raise ValueError('This is not the currently armed claim.')
    p=read(data/'sessions'/f'{ident}.progress.json')
    previous=read(data/'sessions'/f'{ident}.result.json') or {}
    ingest='done' if previous.get('stages',{}).get('ingest')=='done' else 'pending'
    result=dict(p,rewards_saved=True,success=ingest=='done',stages=dict(replay='done',save='done',ingest=ingest),
                reconciled_at=iso(now_utc()),recovery_method='native-checkpoint-and-spool')
    # Persist the result first. An interruption before settlement is safe to retry.
    write_json(data/'sessions'/f'{ident}.result.json',result)
    plan=read(data/'plans'/f'{ident}.json')
    state['last_claim']=dict(expedition_id=ident,credited_hours=float(plan.get('hours',0))*float(plan.get('scale',1)),at=iso(now_utc()),result=result)
    state.pop('armed')
    write_json(data/'state.json',state)
    return result
