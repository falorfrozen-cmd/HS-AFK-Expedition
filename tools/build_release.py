"""Build the portable package from the installed CPython stdlib and local MSVC.
No pip, downloads or game/save files. Run after the plugin and launcher builds.
"""
from pathlib import Path
import hashlib,json,re,shutil,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
# The package is named after the panel version, so the two cannot drift apart.
VERSION=re.search(r"^VERSION='([^']+)'",(ROOT/'tools/panel.py').read_text(encoding='utf-8'),re.M).group(1)
OUT=ROOT/f'dist/AFK-FARM-{VERSION}'

def copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)

def main():
    if sys.version_info[:2]!=(3,13):raise SystemExit('Build with Python 3.13')
    base=Path(sys.base_prefix);runtime=OUT/'runtime';runtime.mkdir(parents=True,exist_ok=True)
    for name in ('python.exe','python313.dll','vcruntime140.dll','vcruntime140_1.dll'):
        copy(base/name,runtime/name)
    copy(base/'LICENSE.txt',runtime/'PYTHON-LICENSE.txt')
    for p in (base/'DLLs').iterdir():
        if p.suffix.lower() in ('.pyd','.dll') and not p.name.startswith(('_tkinter','tcl','tk')):copy(p,runtime/'DLLs'/p.name)
    with zipfile.ZipFile(runtime/'python313.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in (base/'Lib').rglob('*.py'):
            relative=p.relative_to(base/'Lib')
            if any(part in ('site-packages','__pycache__','test','tests','idlelib','tkinter','ensurepip','turtledemo') for part in relative.parts):continue
            z.write(p,relative.as_posix())
    (runtime/'python313._pth').write_text('python313.zip\nDLLs\n.\n../app/tools\n../hs-game-sdk/python\n',encoding='utf-8')
    for p in (ROOT/'tools').glob('*.py'):
        if p.name not in ('build_release.py','build_item_assets.py'):copy(p,OUT/'app/tools'/p.name)
    shutil.copytree(ROOT/'web',OUT/'app/web',dirs_exist_ok=True)
    for p in (ROOT.parent/'hs-game-sdk/python/hs_game_sdk').rglob('*.py'):
        copy(p,OUT/'hs-game-sdk/python/hs_game_sdk'/p.relative_to(ROOT.parent/'hs-game-sdk/python/hs_game_sdk'))
    copy(ROOT/'plugin_build/HSAfkExpeditionPlugin.dll',OUT/'app/plugin_build/HSAfkExpeditionPlugin.dll')
    copy(ROOT/'launcher/AFK FARM.exe',OUT/'AFK FARM.exe')
    (OUT/'BASLA.md').unlink(missing_ok=True)  # Retire the previous generated guide.
    copy(ROOT/'docs/INDEPENDENT_REWARDS.md',OUT/'INDEPENDENT_REWARDS.md');copy(ROOT/'docs/PLAYER_GUIDE.md',OUT/'START_HERE.md');copy(ROOT/'LICENSE',OUT/'LICENSE')
    manifest=[]
    for p in sorted(OUT.rglob('*')):
        # Extracting a previous ZIP inside the output folder creates a nested
        # package. Keep that user's copy, but never ship it inside the new ZIP.
        if p.relative_to(OUT).parts[0]==OUT.name:continue
        if p.is_file() and p.name!='MANIFEST.json':
            with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
            manifest.append(dict(path=p.relative_to(OUT).as_posix(),bytes=p.stat().st_size,sha256=digest))
    (OUT/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    zipped=str(OUT)+'.zip'
    with zipfile.ZipFile(zipped,'w',zipfile.ZIP_DEFLATED) as archive:
        for entry in manifest:
            archive.write(OUT/entry['path'],OUT.name+'/'+entry['path'])
        archive.write(OUT/'MANIFEST.json',OUT.name+'/MANIFEST.json')
    print(json.dumps(dict(folder=str(OUT),zip=zipped,files=len(manifest),bytes=sum(p['bytes'] for p in manifest))))

if __name__=='__main__':main()
