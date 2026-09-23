"""Collection log, wishlist and share summaries (0.7.0).

Everything here reads the item records the game itself built during delivered
expeditions (the spool files) and the catalog in ``web/collection.json``. It
never creates, changes or moves an item. Standard library only.

A record counts toward the collection when its rarity is Set or above and its
native display name is a collectible's name (names are unique in the catalog;
``build_collection.py`` refuses duplicates).
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
COLLECTIBLE_RARITIES = {4, 6, 7, 9, 10}      # itemInfoStruct "27": Set, Satanic, Angelic, Heroic, Unholy
WISHLIST_LIMIT = 200
_NAME = re.compile(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Catalog:
    """The collectible items, by key and by native display name."""
    def __init__(self, root):
        data = _read(Path(root) / 'web' / 'collection.json', {}) or {}
        self.items = [e for e in data.get('items', []) if isinstance(e, dict) and e.get('key') and e.get('name')]
        self.by_key = {e['key']: e for e in self.items}
        self.by_name = {e['name']: e for e in self.items}


def _rarity(record):
    info = ((record.get('item') or {}).get('itemInfoStruct') or {}) if isinstance(record.get('item'), dict) else {}
    value = info.get('27')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def scan_spool(path, catalog: Catalog) -> dict:
    """Collectibles in one spool: key -> {count, first_seq, first_t}.

    Only lines whose top-level name is a collectible's are parsed; the other
    (by far most) records are skipped by a text match first.
    """
    found = {}
    try:
        stream = Path(path).open(encoding='utf-8', errors='replace')
    except OSError:
        return found
    with stream:
        for line in stream:
            match = _NAME.search(line)
            if not match:
                continue
            try:
                name = json.loads('"' + match.group(1) + '"')
            except ValueError:
                continue
            entry = catalog.by_name.get(name)
            if not entry:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get('kind') != 'item' or _rarity(record) not in COLLECTIBLE_RARITIES:
                continue
            slot = found.setdefault(entry['key'], dict(count=0, first_seq=record.get('seq'), first_t=record.get('t')))
            slot['count'] += 1
    return found


class Collection:
    """Per-expedition finds (cached by spool size and time) and the account-wide log."""
    def __init__(self, data, root):
        self.data = Path(data)
        self.catalog = Catalog(root)
        self.cache_path = self.data / 'collection-cache.json'
        self._memory = None

    def _cache(self):
        if self._memory is None:
            cache = _read(self.cache_path, {}) or {}
            self._memory = cache if isinstance(cache, dict) and cache.get('schema') == SCHEMA else dict(schema=SCHEMA, spools={})
        return self._memory

    def finds(self, ident):
        """Collectibles one delivered claim (or worker haul) brought: key -> {count, ...}."""
        path = self.data / 'spool' / f'{ident}.ndjson'
        try:
            stat = path.stat()
        except OSError:
            return {}
        cache = self._cache()
        stamp = [stat.st_mtime_ns, stat.st_size]
        entry = cache['spools'].get(ident)
        if not entry or entry.get('stamp') != stamp:
            entry = dict(stamp=stamp, found=scan_spool(path, self.catalog))
            cache['spools'][ident] = entry
            _write(self.cache_path, cache)
        return entry['found']

    def log(self, results):
        """The collection over delivered claims, oldest first.

        ``results`` are (claim id, result, plan) of settled claims, as the panel
        lists them. Returns (key -> account entry, claim id -> keys first found there).
        """
        ordered = sorted(results, key=lambda r: (str((r[1] or {}).get('updated') or (r[1] or {}).get('settled_at') or ''), r[0]))
        found, firsts = {}, {}
        for ident, result, plan in ordered:
            hero = (plan.get('character') or {}).get('name')
            room = (plan.get('zones') or [{}])[0].get('room')
            for key, hit in sorted(self.finds(ident).items(), key=lambda kv: kv[1].get('first_seq') or 0):
                slot = found.get(key)
                if not slot:
                    slot = found[key] = dict(count=0, first_claim=ident, first_hero=hero, first_room=room,
                                             first_at=hit.get('first_t') or (result or {}).get('updated'), heroes=[])
                    firsts.setdefault(ident, []).append(key)
                slot['count'] += int(hit.get('count') or 0)
                if hero and hero not in slot['heroes']:
                    slot['heroes'].append(hero)
        return found, firsts

    def view(self, results, wishlist=None):
        """What the collection page shows: totals, sets, every collectible."""
        found, _ = self.log(results)
        wished = {w['key'] for w in (wishlist or {}).get('items', [])}
        by_rarity, sets, items = {}, {}, []
        for e in self.catalog.items:
            hit = found.get(e['key'])
            row = dict(e, found=bool(hit), count=(hit or {}).get('count', 0), first_at=(hit or {}).get('first_at'),
                       first_hero=(hit or {}).get('first_hero'), first_room=(hit or {}).get('first_room'), wished=e['key'] in wished)
            items.append(row)
            tally = by_rarity.setdefault(e['rarity'], dict(total=0, found=0))
            tally['total'] += 1; tally['found'] += bool(hit)
            if e.get('set'):
                s = sets.setdefault(e['set'], dict(name=e['set'], pieces=[], found=0))
                s['pieces'].append(e['key']); s['found'] += bool(hit)
        complete = sum(1 for s in sets.values() if s['found'] == len(s['pieces']))
        return dict(schema=SCHEMA, total=len(items), found=sum(1 for i in items if i['found']), by_rarity=by_rarity,
                    sets=sorted(sets.values(), key=lambda s: s['name']), sets_complete=complete, items=items)


# ------------------------------------------------------------------ wishlist
def load_wishlist(data):
    value = _read(Path(data) / 'wishlist.json', {}) or {}
    items = value.get('items') if isinstance(value, dict) and isinstance(value.get('items'), list) else []
    clean, seen = [], set()
    for w in items:
        if isinstance(w, dict) and isinstance(w.get('key'), str) and w['key'] not in seen:
            seen.add(w['key']); clean.append(dict(key=w['key'], added_at=w.get('added_at')))
    return dict(schema=SCHEMA, items=clean[:WISHLIST_LIMIT])


def wishlist_add(data, catalog: Catalog, key):
    if not isinstance(key, str) or key not in catalog.by_key:
        raise ValueError('Choose a unique or set item from the collection.')
    wishlist = load_wishlist(data)
    if any(w['key'] == key for w in wishlist['items']):
        return wishlist
    if len(wishlist['items']) >= WISHLIST_LIMIT:
        raise ValueError(f'The wishlist holds up to {WISHLIST_LIMIT} items.')
    wishlist['items'].append(dict(key=key, added_at=_now()))
    _write(Path(data) / 'wishlist.json', wishlist)
    return wishlist


def wishlist_remove(data, key):
    wishlist = load_wishlist(data)
    wishlist['items'] = [w for w in wishlist['items'] if w['key'] != key]
    _write(Path(data) / 'wishlist.json', wishlist)
    return wishlist


def wishlist_hits(collection: Collection, ident, wishlist):
    """Wishlist items one delivered claim brought, with how many of each."""
    finds = collection.finds(ident)
    catalog = collection.catalog.by_key
    return [dict(key=w['key'], name=catalog[w['key']]['name'], rarity=catalog[w['key']]['rarity'],
                 icon=catalog[w['key']].get('icon'), count=finds[w['key']]['count'])
            for w in wishlist.get('items', []) if w['key'] in finds and w['key'] in catalog]


def notify_hits_once(data, ident, hits, toast, hero=None, region=None):
    """A Windows notification for a claim's wishlist drops, never twice for one claim."""
    if not hits:
        return False
    path = Path(data) / 'wishlist-notified.json'
    done = _read(path, {}) or {}
    done = done if isinstance(done, dict) else {}
    if ident in done:
        return False
    names = ', '.join(h['name'] for h in hits[:3]) + (f' and {len(hits) - 3} more' if len(hits) > 3 else '')
    where = ' in ' + region if region else ''
    toast(f'AFK FARM: wishlist drop!', f"{hero or 'Your hero'} found {names}{where}.")
    done[ident] = [h['key'] for h in hits]
    if len(done) > 500:
        done = dict(list(done.items())[-500:])
    _write(path, done)
    return True
