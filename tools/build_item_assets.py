"""Reuse the sibling Item Editor's existing display names and PNG icons.
No game extraction and no item generation. Run before building the package.
"""
import hashlib,json,shutil
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    editor=root.parent/'hero-siege-item-editor'
    source=editor/'hs_full_catalog.json'
    catalog=json.loads(source.read_text(encoding='utf-8'))
    destination=root/'web/assets/items';destination.mkdir(parents=True,exist_ok=True)
    result={}
    for item in catalog:
        key=item.get('key');sprite=item.get('spr')
        if not key or type(sprite) is not int:continue
        icon=editor/'item_icons'/f'{sprite}.png'
        if not icon.is_file():continue
        shutil.copy2(icon,destination/icon.name)
        result[key]=dict(name=item['name'],icon='/assets/items/'+icon.name)
    (root/'web/items.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    (root/'web/assets/items/SOURCE.json').write_text(json.dumps(dict(source='hero-siege-item-editor/hs_full_catalog.json',sha256=hashlib.sha256(source.read_bytes()).hexdigest(),entries=len(result)),indent=2),encoding='utf-8')
    print(f'{len(result)} display entries; {len(list(destination.glob("*.png")))} existing item icons.')


if __name__=='__main__':main()
