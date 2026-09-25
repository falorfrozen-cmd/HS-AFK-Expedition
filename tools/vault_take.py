"""Keys, materials and the town's other goods from the Vault to the camp (0.8, 0.9).

The camp's key rack (a golden chest takes a Basic Key, a crystal chest a
Crystal Key) and its stock (the Jeweler's materials, and from 0.9 every good
of the town: ores, materials, dusts, rare consumables, keys, shards, tarot
cards, runes, gems, jewels and orbs, see goods.py) are filled from the Item
Editor's Infinite Vault, category AFK Materials. The editor (2.16.1 or newer)
carries a take out at most once per request id (POST /api/vault/afk-take): a
repeated id answers with the recorded take, and a cancelled id never takes
anything. The panel keeps one receipt per request in workers.json
(``vault_takes``) and settles an unclear answer by cancelling the request: the
editor then reports the take it made, which reaches the camp once, or makes
sure the request never takes anything. Nothing is ever taken twice or lost on
the way. AFK FARM never writes the Vault database itself.

Standard library only.
"""
from __future__ import annotations

import re
import urllib.error

import goods
import ingest_spool

ROUTE = '/api/vault/afk-take'
RACK = {'12:0': 'Basic Key', '12:1': 'Crystal Key'}   # the adventurer's key rack
MAX_TAKE = 100_000
MAX_KINDS = 32                     # the editor takes at most 32 kinds in one request
OLD_EDITOR = 'Update the Item Editor to 2.16.1 or newer to take keys and materials from the Vault.'
_KIND = re.compile(r'(\d{1,3}):(\d{1,3})')


class OldEditor(Exception):
    """The running Item Editor has no camp route, so nothing can have been taken."""


def goes_to(key: str) -> str | None:
    """'rack' for a Basic or Crystal Key, 'stock' for any other town good, None otherwise."""
    if key in RACK:
        return 'rack'
    return 'stock' if _KIND.fullmatch(str(key)) and goods.known(key) else None


def name(key: str) -> str:
    return RACK.get(key) or goods.name(key)


def describe(items: dict) -> str:
    return ', '.join(f'{n:,} {name(k)}' for k, n in sorted(items.items()))


def clean_items(raw) -> dict[str, int]:
    """``{"type:id": count}`` of rack keys and jeweler materials, whole counts from 1 to MAX_TAKE."""
    if not isinstance(raw, dict) or not raw:
        raise ValueError('Choose the keys or materials to take from the Vault.')
    if len(raw) > MAX_KINDS:
        raise ValueError(f'Take at most {MAX_KINDS} kinds of goods at once.')
    items = {}
    for key, count in raw.items():
        if goes_to(key) is None:
            raise ValueError("Only the town's goods come to the camp: keys, materials, shards, runes, gems and the like.")
        if type(count) is not int or not 1 <= count <= MAX_TAKE:
            raise ValueError(f'Take a whole number of each, from 1 to {MAX_TAKE:,}.')
        items[str(key)] = count
    return items


def call(base: str, body: dict, timeout: float = 30.0) -> dict:
    """One request to the editor's camp route. OldEditor: the route is missing (nothing taken).
    OSError: no clear answer (the request may or may not have arrived)."""
    try:
        reply = ingest_spool.post_json(base, ROUTE, body, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise OldEditor(OLD_EDITOR) from error
        raise
    if not isinstance(reply, dict):
        raise ValueError('unexpected reply from the Item Editor')
    return reply


def stock(base: str) -> dict:
    return call(base, dict(action='stock'), timeout=10.0)


def take(base: str, request: str, items: dict[str, int], purpose: str = 'AFK FARM camp') -> dict:
    rows = [dict(cls=int(k.split(':')[0]), base=int(k.split(':')[1]), count=int(n)) for k, n in sorted(items.items())]
    return call(base, dict(action='take', requestId=request, items=rows, purpose=purpose))


def cancel(base: str, request: str) -> dict:
    return call(base, dict(action='cancel', requestId=request))


def taken(reply: dict) -> dict[str, int]:
    """``{"type:id": count}`` of a done reply."""
    out: dict[str, int] = {}
    for row in reply.get('taken') or []:
        key = f"{int(row['cls'])}:{int(row['base'])}"
        out[key] = out.get(key, 0) + int(row['count'])
    return out
