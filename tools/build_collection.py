"""The collection log's catalog: every collectible unique and set item.

Reads the sibling Item Editor's catalog (the same file ``build_item_assets.py``
takes the display names and icons from) and writes ``web/collection.json``.
Journal items only; developer placeholders are left out. No game extraction
and no item generation. Run before building the package.
"""
import hashlib
import json
from pathlib import Path

RARITIES = ('Unholy', 'Angelic', 'Heroic', 'Set', 'Satanic')


def entries(catalog, icons):
    out = []
    for item in catalog:
        if item.get('kind') != 'unique' or not item.get('journal') or item.get('noUnique'):
            continue
        name = item.get('name') or ''
        if not name or name == 'deprecated' or name.startswith('Dev '):
            continue
        rarity = 'Set' if item.get('setName') else item.get('rar')
        if rarity not in RARITIES:
            continue
        out.append(dict(key=item['key'], name=name, rarity=rarity, set=item.get('setName'), tier=item.get('tier'),
                        level=item.get('lvl'), type=item.get('cls'), icon=(icons.get(item['key']) or {}).get('icon')))
    names = [e['name'] for e in out]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise SystemExit('collectible names must be unique to match native records: ' + ', '.join(duplicates))
    order = {r: i for i, r in enumerate(RARITIES)}
    return sorted(out, key=lambda e: (order[e['rarity']], e['set'] or '', e['name']))


def main():
    root = Path(__file__).resolve().parents[1]
    source = root.parent / 'hero-siege-item-editor' / 'hs_full_catalog.json'
    catalog = json.loads(source.read_text(encoding='utf-8'))
    icons = json.loads((root / 'web' / 'items.json').read_text(encoding='utf-8'))
    result = dict(schema=1, source='hero-siege-item-editor/hs_full_catalog.json',
                  sha256=hashlib.sha256(source.read_bytes()).hexdigest(), items=entries(catalog, icons))
    (root / 'web' / 'collection.json').write_bytes(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    print(f"{len(result['items'])} collectible items written to web/collection.json")


if __name__ == '__main__':
    main()
