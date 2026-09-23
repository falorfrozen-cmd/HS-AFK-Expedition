"""Which generated items a Vault transfer sends to Infinite Vault.

The filter only chooses what is transferred. Every item stays in its spool,
so a later transfer with a wider filter adds the items left out now, and the
Vault skips what it already holds. Nothing here creates, changes or sells an
item. Standard library only.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from item_labels import rarity_name

SCHEMA = 1
# Gear groups in the order transfers send them, so the best drops fill the
# first stashes. ``Other`` is every native tier without a confirmed name.
GEAR_RARITIES = ('Unholy', 'Angelic', 'Heroic', 'Set', 'Satanic', 'Other')
# Native item classes whose Vault records are stacks (keys, boss parts and
# tarot, materials, runes/gems/orbs). Mirrors the Item Editor's STACKABLE_CLS.
STACKABLE_TYPES = frozenset({12, 13, 14, 15})


def normalize(value=None) -> dict:
    """Validated settings; the defaults transfer what the game's filter shows."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {'schema', 'respect_game_filter', 'rarities', 'keep_stackables'}:
        raise ValueError('Unknown Vault transfer setting')
    if value.get('schema', SCHEMA) != SCHEMA or type(value.get('schema', SCHEMA)) is not int:
        raise ValueError('Unsupported Vault transfer settings version')
    rarities = value.get('rarities', {})
    if not isinstance(rarities, dict) or set(rarities) - set(GEAR_RARITIES):
        raise ValueError('Unknown rarity in the Vault transfer filter')
    result = {'schema': SCHEMA}
    for key in ('respect_game_filter', 'keep_stackables'):
        flag = value.get(key, True)
        if type(flag) is not bool:
            raise ValueError(f'{key} must be true or false')
        result[key] = flag
    result['rarities'] = {}
    for rarity in GEAR_RARITIES:
        flag = rarities.get(rarity, True)
        if type(flag) is not bool:
            raise ValueError(f'{rarity} must be true or false')
        result['rarities'][rarity] = flag
    return result


def load(path: Path) -> dict:
    if not path.exists():
        return normalize()
    try:
        return normalize(json.loads(path.read_bytes()))
    except (OSError, ValueError) as error:
        raise ValueError('The saved Vault transfer filter is invalid; choose and save it again') from error


def _native_type(record: dict):
    item = record.get('item') if isinstance(record.get('item'), dict) else {}
    value = item.get('itemType', record.get('type'))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value) if float(value).is_integer() else None


def is_stackable(record: dict) -> bool:
    return _native_type(record) in STACKABLE_TYPES


def rarity_group(record: dict) -> str:
    item = record.get('item') if isinstance(record.get('item'), dict) else {}
    info = item.get('itemInfoStruct') if isinstance(item.get('itemInfoStruct'), dict) else {}
    label = rarity_name(info.get('27'))
    return label if label in GEAR_RARITIES else 'Other'


def decide(record: dict, settings: dict):
    """None to transfer the record, otherwise why it stays in the spool."""
    if settings['respect_game_filter'] and record.get('filter_visible') is False:
        return 'game_filter'
    if is_stackable(record):
        return None if settings['keep_stackables'] else 'stackables'
    return None if settings['rarities'][rarity_group(record)] else 'rarity'


def transfer_order(record: dict) -> tuple:
    """Best gear first, stackables last; spool order inside a group."""
    if is_stackable(record):
        return (len(GEAR_RARITIES) + 1, 0)
    return (GEAR_RARITIES.index(rarity_group(record)), 0)


def describe(settings: dict) -> str:
    kept = [r for r in GEAR_RARITIES if settings['rarities'][r]]
    parts = ['all gear rarities' if len(kept) == len(GEAR_RARITIES) else ('gear: ' + ', '.join(kept) if kept else 'no gear')]
    parts.append('keys and materials' if settings['keep_stackables'] else 'no keys or materials')
    if settings['respect_game_filter']:
        parts.append("the game's loot filter applies")
    return '; '.join(parts)
