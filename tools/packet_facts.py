"""The recorded packets' small facts, cached on disk (0.9).

The bestiary and the workers' loot pools each read every packet file whenever
one changed. A packet carries a snapshot of hundreds of monster variables, so
6,471 files took 24 seconds to read (MEASURED 2026-09-25 on the player's data)
and a siege page stalled on it. packet-facts.json keeps what those readers use
from each file, with the file's size and modification time. Only new or changed
files are read again, and a file that is gone drops out.

It is a derived cache: deleting it only makes the next read slow.

Standard library only.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import afk

FILE = 'packet-facts.json'
SCHEMA = 1
_LOCK = threading.Lock()
_MEMO: dict = {}
RANKS = (1, 2, 3, 4)
MAX_AFFIX = 40                 # runtime affix slots from 40 up are flags, not affixes
ORIGINS = {9: 'abyss', 10: 'abyss', 4: 'unholy', 1: 'pillar', 3: 'special'}


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _flag(value) -> bool:
    return value is True or (isinstance(value, (int, float)) and not isinstance(value, bool) and value not in (0, 0.0) and value < 1000)


def _first_number(args):
    """The recorded first argument (a number, possibly wrapped by the capture)."""
    if not args:
        return None
    a = args[0]
    if isinstance(a, dict):
        a = a.get('value', a.get('number'))
    try:
        value = float(a)
    except (TypeError, ValueError):
        return None
    return int(value) if value.is_integer() else None


def read(path) -> dict | None:
    """One packet file's facts (None when it cannot be read)."""
    d = afk.read_json(path)
    if not isinstance(d, dict):
        return None
    prot = d.get('protected') if isinstance(d.get('protected'), dict) else {}
    entry = afk.packet_entry(d)
    obj = str(d.get('self_object') or '')
    out = dict(hash=d.get('packet_hash') or Path(path).stem, room=d.get('room'),
               build=d.get('game_build_id'), object=obj, monster_key=str(d.get('monster_key') or ''), rank=d.get('rank'),
               script=str(d.get('script') or ''), protected=bool(prot), complete=bool(entry.get('complete')), exp=entry.get('exp'),
               first=_first_number(d.get('args') or []), monster=None)
    if (out['monster_key'] and out['rank'] in RANKS and out['script'].endswith('DropItem') and 'Chest' not in obj
            and not obj.startswith('Goblin_') and prot and out['complete']):
        snap = d.get('self_snapshot') if isinstance(d.get('self_snapshot'), dict) else {}
        hp = _num(prot.get('max_hp')) or _num(prot.get('maxHpUnscaled'))
        if hp and hp > 0:
            affixes = sorted({int(a) for a in (snap.get('affixList') or []) if _num(a) is not None and 0 <= a < MAX_AFFIX})
            special = snap.get('specialType')
            origin = ORIGINS.get(int(special), 'special') if _num(special) and special > 0 else 'ordinary'
            out['monster'] = dict(name=str(snap.get('name') or out['monster_key']), hp=hp, damage=_num(prot.get('damage')),
                                  speed=_num(snap.get('moveSpeed')) or 0.0, ranged=_flag(snap.get('isRanged')),
                                  fire=_flag(snap.get('fireImmune')), cold=_flag(snap.get('coldImmune')),
                                  poison=_flag(snap.get('poisonImmune')), affixes=affixes, origin=origin)
    return out


def facts() -> tuple[dict, int]:
    """(hash -> facts for every readable packet, a version that changes whenever they do)."""
    folder = afk.PACKETS
    cache_path = afk.DATA / FILE
    with _LOCK:
        memo = _MEMO.get(str(folder))
        if memo is None:
            stored = afk.read_json(cache_path, {}) or {}
            ok = isinstance(stored, dict) and stored.get('schema') == SCHEMA and stored.get('folder') == str(folder)
            memo = dict(files=dict(stored.get('files') or {}) if ok else {}, version=0)
        try:
            listing = [e for e in os.scandir(folder) if e.name.endswith('.json') and e.is_file()]
        except FileNotFoundError:
            listing = []
        files, changed = {}, False
        for e in listing:
            st = e.stat()
            known = memo['files'].get(e.name)
            if known and known.get('size') == st.st_size and known.get('mtime') == st.st_mtime_ns:
                files[e.name] = known
            else:
                files[e.name] = dict(size=st.st_size, mtime=st.st_mtime_ns, facts=read(Path(e.path)))
                changed = True
        if len(files) != len(memo['files']):
            changed = True
        if changed or memo['version'] == 0:
            memo['version'] += 1
        if changed:
            try:
                afk.write_json(cache_path, dict(schema=SCHEMA, folder=str(folder), files=files))
            except OSError:
                pass            # a cache that could not be written is read again next time
        memo['files'] = files
        memo['by_hash'] = {f['facts']['hash']: f['facts'] for f in files.values() if f.get('facts')} if changed or 'by_hash' not in memo \
            else memo['by_hash']
        _MEMO[str(folder)] = memo
        return memo['by_hash'], memo['version']


def clear() -> None:
    """Forget the in-memory copy (tests; the file on disk stays valid)."""
    with _LOCK:
        _MEMO.clear()
