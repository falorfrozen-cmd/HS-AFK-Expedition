"""Read-only setup, support and reward presentation for the local panel."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import afk
import loot_filter
from item_labels import rarity_name

SUPPORT = [
    dict(name='Regular Act farming',status='Available',detail='One region; measured pace, recorded source mix and native drop calls.'),
    dict(name='Gear and talents',status='Checked',detail='Loadout fingerprints must match. This is not a DPS simulator.'),
    dict(name='Temporary buffs and skill mechanics',status='Measured average',detail='Their effect on farming pace is measured; future buff cycles are not simulated.'),
    dict(name='Native loot, XP and gold',status='Available',detail='Native drops and reward calls. Independent parity testing is still required.'),
    dict(name='Keys and relic gates',status='Unverified',detail='The native drop path is used; regional and unlock-dependent parity has not been established.'),
    dict(name='ForgePact kill-trigger rewards',status='Unsupported',detail='Signature and extra Angelic/Unholy rolls happen during a full enemy death and are not included here.'),
    dict(name='Event and guaranteed boss rewards',status='Unsupported',detail='Tower, Wormhole and battlefield completion rewards are not replayed. Special monster packets are blocked.'),
    dict(name='Tracker and kill statistics',status='Unsupported',detail='Reward calls do not represent a complete enemy death event.'),
    dict(name='Interrupted delivery',status='Limited recovery',detail='Pause, closing the game window and leaving the region with the expedition hero save and continue later. Completed native saves with matching records can be reconciled. A crash or other interruptions remain held for review.'),
]


def support_warnings(settings):
    messages=[]
    if isinstance(settings,dict):
        n=settings.get('angelic_items',1)
        if type(n) in (int,float) and n>1:
            messages.append('ForgePact extra Angelic/Unholy rolls are enabled but are not included in AFK rewards.')
    return messages


class Presentation:
    def __init__(self,root):
        self.root=Path(root);self.cache={}
        self.catalog=afk.read_json(self.root/'web/items.json',{}) or {}
        candidates={}
        for entry in self.catalog.values():candidates.setdefault(entry['name'],[]).append(entry)
        self.names={name:entries[0] for name,entries in candidates.items() if all(e==entries[0] for e in entries)}

    def setup(self,game_bin):
        if not game_bin: return dict(configured=False,exe=False,aurie=False,yytk=False,plugin=False,checks=[])
        b=Path(game_bin)
        paths=[b/'Hero_Siege.exe',b/'AurieCore.dll',b/'mods/aurie/YYToolkit.dll',b/'mods/aurie/HSAfkExpeditionPlugin.dll',self.root/'plugin_build/HSAfkExpeditionPlugin.dll']
        stamp=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) if p.is_file() else (str(p),None) for p in paths)
        key=('setup',stamp)
        if key in self.cache:return self.cache[key]
        from game_session import EXE_SHA256
        hashes={}
        for p in (paths[0],paths[3],paths[4]):
            if p.is_file():
                with p.open('rb') as stream:hashes[p]=hashlib.file_digest(stream,'sha256').hexdigest()
        verified=hashes.get(paths[0])==EXE_SHA256
        current=paths[3] in hashes and hashes.get(paths[3])==hashes.get(paths[4])
        checks=[dict(name='Game executable',ok=paths[0].is_file(),detail=str(paths[0])),
                dict(name='Verified game build',ok=verified,detail='Automatic setup is available for the tested executable only.'),
                dict(name='Aurie loader',ok=paths[1].is_file(),detail='AurieCore.dll must be installed beside the game.'),
                dict(name='YYToolkit',ok=paths[2].is_file(),detail='YYToolkit.dll must be installed in mods/aurie.'),
                dict(name='Current AFK plugin',ok=current,detail='Installed DLL must match this AFK FARM package.')]
        result=dict(configured=True,exe=paths[0].is_file(),aurie=paths[1].is_file(),yytk=paths[2].is_file(),plugin=paths[3].is_file(),verified_build=verified,plugin_current=current,checks=checks)
        self.cache={k:v for k,v in self.cache.items() if k[0]!='setup'};self.cache[key]=result
        return result

    def loot(self,data,ident):
        path=Path(data)/'spool'/f'{ident}.ndjson'
        if not path.is_file():return dict(items=[],total=0,filtered=0,unreadable=0,rarities={},truncated=False)
        stamp=(path.stat().st_mtime_ns,path.stat().st_size)
        key=('loot',ident)
        cached=self.cache.get(key)
        if cached and cached[0]==stamp:return cached[1]
        rows=[];unreadable=0;rarities=Counter();visible=Counter();filtered=0;stackables=0;best={}
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
            if not line.strip():continue
            try:r=json.loads(line)
            except ValueError:unreadable+=1;continue
            if not isinstance(r,dict) or r.get('kind')!='item':continue
            item=r.get('item') or {};info=item.get('itemInfoStruct') or {}
            name=r.get('name') or info.get('28') or 'Unknown item'
            # Native spools use display names; old fixtures may carry catalog keys.
            # Ambiguous names deliberately retain the fallback icon.
            display=self.catalog.get(name) or self.names.get(name,{})
            rarity=info.get('27');label=rarity_name(rarity)
            hidden=r.get('filter_visible') is False
            filtered+=hidden;rarities[label]+=1
            stackable=loot_filter.is_stackable(r);stackables+=stackable
            if not hidden and not stackable:
                # Summary: gear the game's filter shows, by rarity group, rarest first.
                group=loot_filter.rarity_group(r);visible[group]+=1;drop=(group,display.get('name',name))
                entry=best.setdefault(drop,dict(name=drop[1],rarity=label,group=group,icon=display.get('icon'),count=0))
                entry['count']+=1
            rows.append(dict(seq=r.get('seq'),name=display.get('name',name),rarity=label,rarity_code=rarity,
                             icon=display.get('icon'),filtered=hidden,type=r.get('type')))
        order={g:i for i,g in enumerate(loot_filter.GEAR_RARITIES)}
        top=sorted(best.values(),key=lambda e:(order.get(e['group'],len(order)),-e['count'],e['name']))[:12]
        result=dict(items=rows[:500],total=len(rows),filtered=filtered,unreadable=unreadable,rarities=dict(rarities),
                    visible_rarities=dict(visible),stackables=stackables,best=top,truncated=len(rows)>500)
        self.cache[key]=(stamp,result)
        return result
