"""Freeze a farm forecast before a separate capture, then compare it read-only.

prepare --profile PATH --output PATH
evaluate --reference PATH --session PATH --output PATH
No item generation, reward claim, save edits or profile replacement.
"""
import argparse
import hashlib
from pathlib import Path
import afk


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(profile, path):
    source = Path(profile['session'])
    if profile.get('rate_basis') != 'farm-clock' or not profile.get('farm_context'):
        raise ValueError('A current region-clock profile is required.')
    rows = afk.read_ndjson(source)
    if not any(r.get('kind') == 'session_stop' for r in rows):
        raise ValueError('Finish the reference recording first.')
    source_hash = digest(source)
    if profile.get('session_sha256') and profile['session_sha256'] != source_hash:
        raise ValueError('The reference recording changed after its profile was built.')
    reference = dict(document_type='afk.validation-reference',schema=1,created_at=afk.iso(afk.now_utc()),
                     profile=profile,source_sha256=source_hash,
                     criteria=dict(min_seconds=600,min_kills=200,min_coverage=.95,max_relative_error=.20),
                     note='Acceptance targets are engineering choices, not claimed accuracy.')
    if path.exists(): raise ValueError('Reference already exists. It must remain frozen.')
    afk.write_json(path, reference)
    return reference


def compare(reference, measured, rows, source_digest):
    if reference.get('document_type') != 'afk.validation-reference': raise ValueError('Invalid reference.')
    p = reference['profile']
    if reference['source_sha256'] == source_digest: raise ValueError('The validation must use a separate recording.')
    start = next((r.get('t') for r in rows if r.get('kind')=='session_start'), None)
    if not start or afk.parse_iso(start) <= afk.parse_iso(reference['created_at']):
        raise ValueError('Start the validation recording after the forecast has been frozen.')
    if not any(r.get('kind')=='session_stop' for r in rows): raise ValueError('Finish the validation recording first.')
    for key in ('room','game_build','rate_basis','forgepact'):
        if p.get(key) != measured.get(key): raise ValueError(f'Validation {key} differs from the reference.')
    if afk.character_key(p.get('character')) != afk.character_key(measured.get('character')):
        raise ValueError('Validation belongs to another character.')
    if (p.get('farm_context') or {}).get('hash') != (measured.get('farm_context') or {}).get('hash'):
        raise ValueError('Validation loadout differs from the reference.')
    c = reference['criteria']
    issues = []
    for label, profile in (('Reference',p), ('Validation',measured)):
        if profile['basis_seconds'] < c['min_seconds']: issues.append(label+' is shorter than 10 minutes.')
        if profile['kills'] < c['min_kills']: issues.append(label+' contains fewer than 200 kills.')
        if profile['coverage'] < c['min_coverage']: issues.append(label+' has incomplete packet coverage.')
    metrics = {}
    for key in ('kills_per_min','exp_per_min','breaks_per_min'):
        predicted = p[key] * measured['basis_seconds']/60
        observed = measured[key] * measured['basis_seconds']/60
        error = abs(predicted-observed)/observed if observed>0 else (0 if predicted==0 else None)
        metrics[key] = dict(predicted=predicted,observed=observed,relative_error=error,
                            within_target=(error is not None and error<=c['max_relative_error']))
    main = [metrics[k]['within_target'] for k in ('kills_per_min','exp_per_min')]
    return dict(document_type='afk.validation-result',schema=1,at=afk.iso(afk.now_utc()),
                status='insufficient' if issues else ('within_target' if all(main) else 'outside_target'),
                reference_sha256=reference['source_sha256'],validation_sha256=source_digest,
                character=p['character'],room=p['room'],farm_context_hash=p['farm_context']['hash'],
                issues=issues,criteria=c,metrics=metrics,quality=measured.get('quality'),
                unmeasured=['Live gold per kill','Rare drop probability','Event and boss reward parity'],
                note='One independent session comparison; not a universal accuracy guarantee.')


def evaluate(reference, session):
    profiles = afk.build_profile(session, reference['profile']['room'], False)
    if not profiles: raise ValueError('No matching region measurement.')
    return compare(reference, profiles[0], afk.read_ndjson(session), digest(session))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='action',required=True)
    a=subs.add_parser('prepare');a.add_argument('--profile',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    a=subs.add_parser('evaluate');a.add_argument('--reference',type=Path,required=True);a.add_argument('--session',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.action=='prepare': freeze(afk.read_json(args.profile),args.output)
    else: afk.write_json(args.output,evaluate(afk.read_json(args.reference),args.session))
    print(args.output)


if __name__=='__main__': main()
