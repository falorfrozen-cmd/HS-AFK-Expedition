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
_SPOOL_CHECKS={}


def spool_check(data, ident, progress):
    """Whether the item records reach exactly the checkpoint: (reason, tail).

    ``reason`` is None when the records up to the checkpoint are complete and
    consistent: one line per record, contiguous sequence numbers, item records
    and "partial" markers of earlier pauses only, as many items as the
    checkpoint counts. ``tail`` = (bytes, lines) written after the checkpoint.
    The plugin flushes the spool before every checkpoint and records its size
    as ``spool_bytes``, so a crash can only leave records past that point: the
    rewards of calls the checkpoint does not count, which a continue delivers
    again and which must therefore be set aside first. A checkpoint without
    ``spool_bytes`` (0.6.0 and older) must match the whole file.
    """
    items=progress.get('items');limit=progress.get('spool_bytes')
    if type(items) is not int or items<0: return 'The checkpoint item count is invalid.',(0,0)
    if limit is not None and (type(limit) is not int or limit<0): return 'The checkpoint spool size is invalid.',(0,0)
    path=data/'spool'/f'{ident}.ndjson'
    try: stat=path.stat()
    except FileNotFoundError:
        return (None,(0,0)) if items==0 and not limit else (f'The item records are missing but the checkpoint counts {items:,} items.',(0,0))
    except OSError: return 'The item records are unreadable.',(0,0)
    key=(str(path),stat.st_mtime_ns,stat.st_size,items,limit)
    if key in _SPOOL_CHECKS: return _SPOOL_CHECKS[key]
    try: raw=path.read_bytes()
    except OSError: return 'The item records are unreadable.',(0,0)
    end=len(raw) if limit is None else limit
    if end>len(raw): result=(f'The item records are shorter than the checkpoint recorded ({len(raw):,} of {end:,} bytes).',(0,0))
    elif end and raw[end-1:end]!=b'\n': result=('The checkpoint does not end at a record boundary.',(0,0))
    else:
        lines=count=0;reason=None
        for line in raw[:end].decode('utf-8',errors='replace').splitlines():
            if not line.strip(): continue
            try: row=json.loads(line)
            except ValueError: reason='The item records are unreadable.';break
            if not isinstance(row,dict) or row.get('expedition_id')!=ident: reason='The item records contain a foreign or unreadable record.';break
            lines+=1
            if row.get('seq')!=lines: reason='The item record sequence has a gap or a duplicate.';break
            if row.get('kind')=='item': count+=1
            elif row.get('kind')!='partial': reason='The item records already end with a summary.';break
        if reason is None and count!=items:
            reason=f'The item records hold {count:,} items but the checkpoint counts {items:,}.'
        tail=raw[end:]
        result=(reason,(len(tail),len([l for l in tail.splitlines() if l.strip()])))
    if len(_SPOOL_CHECKS)>32: _SPOOL_CHECKS.clear()
    _SPOOL_CHECKS[key]=result
    return result


def spool_mismatch(data, ident, progress):
    """Why the item records do not end exactly at the checkpoint, or None."""
    reason,(tail,_)=spool_check(data,ident,progress)
    return reason or (f'{tail:,} bytes of item records were written after the checkpoint.' if tail else None)


def continuable(data, ident):
    """Whether an interrupted delivery may continue from its recorded position.

    Only for a checkpoint left "running" by a crash, power loss or a killed
    game: same plan, no failed calls, no failure record, and item records that
    end exactly at the checkpoint. The player must still accept it (the save is
    not confirmed); returns (allowed, reasons it is not).
    """
    reasons=[]
    if (data/'sessions'/f'{ident}.failure.json').exists(): reasons.append('A native failure record exists.')
    p=read(data/'sessions'/f'{ident}.progress.json') or {}
    plan_path=data/'plans'/f'{ident}.json'
    if p.get('state')!='running': reasons.append('Only a delivery that stopped while running can continue from its recorded position.')
    if p.get('checkpoint_version')!=2 or p.get('expedition_id')!=ident: reasons.append('Checkpoint identity or version is invalid.')
    if not plan_path.is_file() or p.get('plan_hash')!=hashlib.sha256(plan_path.read_bytes()).hexdigest(): reasons.append('The plan differs from the checkpoint.')
    done,total=p.get('calls_done'),p.get('calls_total')
    if type(done) is not int or type(total) is not int or not 0<=done<total: reasons.append('The recorded call counts leave nothing to continue.')
    if p.get('failed')!=0 or p.get('skipped')!=0: reasons.append('Some reward calls failed or were skipped.')
    if not reasons:
        mismatch,_=spool_check(data,ident,p)
        if mismatch: reasons.append(mismatch)
    return not reasons,reasons


def resumable(data, ident):
    """A paused claim the plugin can continue from its saved position.

    Pause, a closed game window or leaving the region with the expedition hero
    saves the game and writes an "aborted" or "paused" checkpoint with that
    save receipt. Only such a checkpoint, for the exact plan, without failed
    calls or a failure record, continues; everything else still needs review,
    except a running checkpoint whose recorded position the player accepted.
    """
    if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', ident): return False
    if (data/'sessions'/f'{ident}.failure.json').exists(): return False
    p=read(data/'sessions'/f'{ident}.progress.json') or {}
    plan_path=data/'plans'/f'{ident}.json'
    accepted=p.get('state')=='running' and p.get('resume_accepted') is True
    if (p.get('state') not in ('aborted','paused') and not accepted) or p.get('checkpoint_version')!=2 or p.get('expedition_id')!=ident: return False
    if not plan_path.is_file() or p.get('plan_hash')!=hashlib.sha256(plan_path.read_bytes()).hexdigest(): return False
    done,total=p.get('calls_done'),p.get('calls_total')
    if type(done) is not int or type(total) is not int or not 0<=done<total: return False
    if p.get('failed')!=0 or p.get('skipped')!=0: return False
    if accepted: return spool_mismatch(data,ident,p) is None
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
        accepted=progress.get('state')=='running' and progress.get('resume_accepted') is True
        return dict(id=ident,status='paused',recoverable=False,resumable=True,accepted=accepted,
                    reasons=['You accepted the recorded position. Claim again with the same hero in the same region to continue.' if accepted else
                             'Delivery was paused at a saved position. Claim again with the same hero in the same region to continue.'],
                    items=len(items),calls_done=progress.get('calls_done'),calls_total=progress.get('calls_total'))
    result=dict(id=ident,status='saved' if not reasons else 'needs_review',recoverable=not reasons,resumable=False,reasons=reasons,
                items=len(items),calls_done=progress.get('calls_done'),calls_total=progress.get('calls_total'))
    if reasons:
        result['continuable'],result['continue_blockers']=continuable(data,ident)
        if result['continuable']:
            result['records_after_checkpoint']=spool_check(data,ident,progress)[1][1]
    return result


def armed_claim(data, ident):
    """(state, armed record) of an armed expedition whose claim is ``ident``."""
    from afk import load_state
    state=load_state(data/'state.json')
    armed=state['expeditions'].get(ident.removesuffix('_claim')) if ident.endswith('_claim') else None
    if not armed: raise ValueError('This is not the claim of an armed expedition.')
    return state,armed


def accept_position(data, ident):
    """The player accepts an interrupted delivery's recorded position.

    Marks the checkpoint resume_accepted so the next claim continues from it.
    Nothing is replayed here; the delivered calls are never repeated, and the
    save of the delivered XP and gold stays unconfirmed.
    """
    from afk import write_json, iso, now_utc
    review=inspect(data,ident)
    if review['status']!='needs_review':raise ValueError('Only a claim that needs review can continue from its recorded position.')
    armed_claim(data,ident)
    path=data/'sessions'/f'{ident}.progress.json'
    if path.stat().st_mtime>time.time()-30:
        raise ValueError('Delivery still looks active. Close the game or wait a moment, then try again.')
    allowed,reasons=continuable(data,ident)
    if not allowed: raise ValueError('This claim cannot continue from its recorded position: '+' '.join(reasons))
    p=read(path)
    _,(tail_bytes,tail_records)=spool_check(data,ident,p)
    set_aside=None
    if tail_bytes:
        # Records written after the checkpoint belong to calls the continue
        # delivers again. Keep them in a separate file, then cut the spool at
        # the checkpoint; a retry after an interruption finds the same state.
        spool=data/'spool'/f'{ident}.ndjson';folder=data/'spool'/'set-aside';folder.mkdir(parents=True,exist_ok=True)
        set_aside=folder/f"{ident}.after-checkpoint-{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}.ndjson"
        with spool.open('r+b') as stream:
            stream.seek(p['spool_bytes']);set_aside.write_bytes(stream.read())
            stream.seek(p['spool_bytes']);stream.truncate();stream.flush();os.fsync(stream.fileno())
        _SPOOL_CHECKS.clear()
        if spool_mismatch(data,ident,p): raise ValueError('The item records could not be cut at the checkpoint; nothing was accepted.')
    p.update(resume_accepted=True,resume_accepted_by='player',resume_accepted_at=iso(now_utc()),
             resume_basis=dict(calls_done=p['calls_done'],items=p['items'],spool_bytes=p.get('spool_bytes'),
                               set_aside=str(set_aside) if set_aside else None,set_aside_records=tail_records if set_aside else 0))
    write_json(path,p)
    return p


def settle_partial(data, ident):
    """The player keeps what an interrupted claim delivered and gives up the rest.

    Only for a claim that needs review (not resumable, not a completed save). No
    reward is generated or repeated: the partial result is recorded as such, the
    armed clock is freed, and the checkpoint, spool and plan stay untouched for the
    recovery report. Whether the game saved the delivered XP is not confirmed.
    """
    from afk import write_json, iso, now_utc, save_state, settle_in_state
    review=inspect(data,ident)
    if review['status']!='needs_review' and not (review['status']=='paused' and review.get('accepted')):
        raise ValueError('Only a claim that needs review can be closed as partial.')
    state,armed=armed_claim(data,ident)
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
    settle_in_state(state,ident,dict(expedition_id=ident,credited_hours=float(plan.get('hours',0))*float(plan.get('scale',1))*fraction,
                                     at=iso(now_utc()),result=result,partial=True))
    save_state(state,data/'state.json')
    return result


def settle(data, ident):
    from afk import write_json, iso, now_utc, save_state, settle_in_state
    review=inspect(data,ident)
    if not review['recoverable']: raise ValueError('Recovery refused: '+' '.join(review['reasons']))
    state,armed=armed_claim(data,ident)
    p=read(data/'sessions'/f'{ident}.progress.json')
    previous=read(data/'sessions'/f'{ident}.result.json') or {}
    ingest='done' if previous.get('stages',{}).get('ingest')=='done' else 'pending'
    result=dict(p,rewards_saved=True,success=ingest=='done',stages=dict(replay='done',save='done',ingest=ingest),
                reconciled_at=iso(now_utc()),recovery_method='native-checkpoint-and-spool')
    # Persist the result first. An interruption before settlement is safe to retry.
    write_json(data/'sessions'/f'{ident}.result.json',result)
    plan=read(data/'plans'/f'{ident}.json')
    settle_in_state(state,ident,dict(expedition_id=ident,credited_hours=float(plan.get('hours',0))*float(plan.get('scale',1)),at=iso(now_utc()),result=result))
    save_state(state,data/'state.json')
    if plan.get('siege_claim'):
        import siege
        from afk import hero_key
        siege.record_claim(data,hero_key(plan.get('character')) or '?',plan['zones'][0]['room'],plan['siege_claim'])
    return result
