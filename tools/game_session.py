"""Prepare a local character in town through game IPC, without mouse/keyboard input.

Windows/Python standard library. Requires plugin 0.2.0-test-session or later.
This is a developer test helper, not an expedition or reward command.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
SDK_ENV='HS_GAME_SDK'


class SdkMissing(ImportError):pass


def sdk_folder(environ=os.environ,root=ROOT):
    """The folder that holds the hs_game_sdk package, or None to import an installed one.

    HS_GAME_SDK (an hs-game-sdk checkout or its python folder) comes first, then the
    hs-game-sdk checkout beside this repository (the hub layout and the release zip).
    Without either, an installed package or PYTHONPATH has to provide hs_game_sdk.
    """
    configured=environ.get(SDK_ENV)
    if configured:
        base=Path(configured).resolve()
        for folder in (base,base/'python'):
            if (folder/'hs_game_sdk/__init__.py').is_file():return folder
        raise SdkMissing(f'{SDK_ENV}={configured} holds no hs_game_sdk package; '
                         'point it at an hs-game-sdk checkout or its python folder')
    sibling=root.parent/'hs-game-sdk/python'
    return sibling if (sibling/'hs_game_sdk/__init__.py').is_file() else None


def load_sdk(environ=os.environ,root=ROOT):
    folder=sdk_folder(environ,root)
    if folder is not None and sys.path[:1]!=[str(folder)]:sys.path.insert(0,str(folder))
    try:import hs_game_sdk
    except ModuleNotFoundError as error:
        if error.name!='hs_game_sdk':raise
        raise SdkMissing(f'hs_game_sdk not found. Set {SDK_ENV} to an hs-game-sdk checkout (or its python folder), '
                         f'keep the checkout beside this repository ({root.parent/"hs-game-sdk"}), '
                         'or provide hs_game_sdk as an installed package or on PYTHONPATH') from None
    return hs_game_sdk


try:load_sdk()
except SdkMissing as error:
    if __name__=='__main__':raise SystemExit(str(error)) from None
    raise
from hs_game_sdk import GameObject, GameScript, GameRoom
from afk import Ipc, DATA, CONFIG

BUILD='exe-281751552-pe6aaa6779-111b8000'
EXE_SHA256='835fae99ff4de7b7b7c45e97b76f97d57379cafd715302e831d24f32100b1156'
MAIN=GameRoom.Main_Menu_rm.name
CHOOSE=GameRoom.Chose_rm.name
LOADING={GameRoom.Game_Start_rm.name,GameRoom.Init_rm.name}
TOWNS={r.name for r in GameRoom if r.name.startswith('Town_') and r.name.endswith('_rm')}
TRAVEL_ROOMS=TOWNS | {r.name for r in GameRoom if re.fullmatch(r'Act_\d{2}_\d{2}',r.name)}


class SessionError(RuntimeError):pass


def require(condition,message):
    if not condition:raise SessionError(message)


def validate_plugin_directory(bin_dir):
    """Aurie also loads backup files whose final extension remains .dll."""
    folder=Path(bin_dir)/'mods/aurie'
    names=sorted(p.name.lower() for p in folder.glob('*') if p.is_file()
                 and p.name.lower().startswith('hsafkexpeditionplugin') and p.suffix.lower()=='.dll')
    require(names==['hsafkexpeditionplugin.dll'],
            'Exactly one AFK plugin DLL required in mods/aurie; move backups outside the loader directory')


def validate_state(s,pid,request):
    require(isinstance(s,dict) and s.get('document_type')=='afk.session-state'
            and type(s.get('schema')) is int and s['schema']==1,'Unsupported session state')
    require(type(s.get('pid')) is int and s['pid']==pid,'State belongs to another process')
    require(s.get('request_id')==request,'Stale state; updated session plugin required')
    require(s.get('game_build')==BUILD,'Unverified game build')


def matches(character,slot,name,class_id):
    return (isinstance(character,dict) and type(character.get('slot')) is int
            and character['slot']==slot and character.get('name')==name
            and type(character.get('class')) is int and character['class']==class_id)


def callback(obj,ordinal,script):
    return f'afk callon {obj.name} {ordinal} {script.name.removeprefix("gml_Script_")}'


def next_action(s,*,slot,name,class_id):
    require(s.get('online') is False,'Offline mode is not confirmed')
    require(s.get('replay_running') is False,'An expedition replay is active or unknown')
    for key in ('player_count','menu_count','choose_count','character_menu_count'):
        require(type(s.get(key)) is int and 0<=s[key]<=1,'Ambiguous '+key)
    room=s.get('room')
    if room in LOADING:return 'wait',None
    if s['player_count']==1:
        if not (s.get('character') or {}).get('name') and room in TOWNS:return 'wait',None
        require(matches(s.get('character'),slot,name,class_id),'Another character is loaded')
        require(room in TOWNS,'Character is outside town; no automatic travel performed')
        return 'ready',None
    if room in TOWNS:return 'wait',None
    require(s['menu_count']==1,'Menu controller unavailable')
    if room==MAIN:
        return 'local',callback(GameObject.Menu_Controller_obj,0,GameScript.gml_Script_UiAMainMenuLocal)
    require(room==CHOOSE,'Unexpected room: '+str(room))
    if s['character_menu_count']==1:
        require(s.get('selected_slot')==slot and matches(s.get('selected_character'),slot,name,class_id),
                'Selected character does not match; play refused')
        return 'play',callback(GameObject.UI_Character_obj,0,GameScript.gml_Script_UiACharacterPlay)
    require(s['choose_count']==1,'Character selection is not ready')
    choices=[c for c in s.get('choices',[]) if c.get('slot')==slot]
    require(len(choices)==1,'Target slot is unavailable or ambiguous on this page')
    choice=choices[0]
    require(matches(choice,slot,name,class_id),'Slot identity does not match')
    require(choice.get('enabled') is True,'Character button is disabled or still loading')
    ordinal=choice.get('ordinal')
    require(type(ordinal) is int and 0<=ordinal<128,'Invalid character button ordinal')
    return 'choose',callback(GameObject.Choose_Parent_obj,ordinal,GameScript.gml_Script_UiAChooseSaveSlot)


def travel_target(room):
    require(isinstance(room,str) and room in TRAVEL_ROOMS,'Travel supports named normal act/town rooms only')
    return GameRoom[room].name


def travel_arrived(s,room,*,slot,name,class_id):
    destination=travel_target(room)
    require(s.get('online') is False,'Offline mode is not confirmed')
    require(s.get('replay_running') is False,'An expedition replay is active or unknown')
    require(type(s.get('player_count')) is int and 0<=s['player_count']<=1,'Ambiguous player count')
    require(s.get('room') in TRAVEL_ROOMS | LOADING,'Unexpected room during travel')
    if s['player_count']==0 or s['room'] in LOADING:return False
    character=s.get('character')
    if isinstance(character,dict) and not character.get('name'):return False
    require(matches(character,slot,name,class_id),'Another character is loaded')
    return s['room']==destination


def travel_action(s,room,*,slot,name,class_id):
    destination=travel_target(room)
    travel_arrived(s,destination,slot=slot,name=name,class_id=class_id)
    require(s.get('room') in TRAVEL_ROOMS and s['player_count']==1
            and matches(s.get('character'),slot,name,class_id),'Character is not ready for travel')
    return None if s['room']==destination else 'afk goto '+destination


def powershell(code):
    encoded=base64.b64encode(code.encode('utf-16-le')).decode('ascii')
    host=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(host),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        capture_output=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
    require(result.returncode==0,result.stderr.decode('utf-8',errors='replace').strip() or 'Process operation failed')
    return result.stdout.decode('utf-8-sig').strip()


def running(exe):
    quoted="'"+str(exe).replace("'","''")+"'"
    raw=powershell("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
        "$rows=@(Get-Process -Name Hero_Siege -ErrorAction SilentlyContinue | "
        f"Where-Object {{$_.Path -eq {quoted}}} | ForEach-Object {{[int]$_.Id}}); "
        "ConvertTo-Json -InputObject $rows -Compress")
    pids=json.loads(raw)
    require(len(pids)<=1,'Multiple matching games are running')
    return pids[0] if pids else None


SW_SHOWMINNOACTIVE=7
# A child inherits its parent's error mode. Git Bash runs with SEM_NOGPFAULTERRORBOX,
# so a game started from it would crash (e.g. an exit-time abort) with no WER report
# and no dump. The game gets the system default instead.
CREATE_DEFAULT_ERROR_MODE=getattr(subprocess,'CREATE_DEFAULT_ERROR_MODE',0x04000000)


def launch(command,cwd):
    """Start a program minimized, without activation and with the default error mode."""
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=SW_SHOWMINNOACTIVE  # Request a background launch.
    return subprocess.Popen(command,cwd=cwd,startupinfo=startup,creationflags=CREATE_DEFAULT_ERROR_MODE)


def close_window(pid,exe):
    """Close the process normally through its main window; its exit code, or None if unreadable.

    Never forces the process. .NET reads the exit code of a process it did not start
    only through a handle opened before the exit, so the handle is taken first; without
    it PowerShell yields null (never a made-up 0).
    """
    quoted="'"+str(exe).replace("'","''")+"'"
    raw=powershell("$ErrorActionPreference='Stop'; "
        f"$game=Get-Process -Id {int(pid)}; if($game.Path -ne {quoted}){{throw 'Process changed'}}; "
        "$held=$null -ne $game.Handle; "
        "if(-not $game.CloseMainWindow()){throw 'Normal close not accepted'}; "
        "if(-not $game.WaitForExit(20000)){throw 'Normal close timed out; not forced'}; "
        "$code=$null; if($held){$code=$game.ExitCode}; "
        "ConvertTo-Json -Compress -InputObject @{exit_code=$code}")
    try:code=json.loads(raw.splitlines()[-1])['exit_code']
    except (IndexError,KeyError,TypeError,ValueError):return None
    return code&0xFFFFFFFF if type(code) is int else None  # .NET reports a signed Int32.


# NTSTATUS exit codes of a crashed process, named in close results and receipts.
EXIT_STATUS={0xC0000005:'STATUS_ACCESS_VIOLATION',0xC00000FD:'STATUS_STACK_OVERFLOW',
             0xC0000374:'STATUS_HEAP_CORRUPTION',
             0xC0000409:'STATUS_STACK_BUFFER_OVERRUN: a fast fail, e.g. abort() in ucrtbase',
             0xE06D7363:'unhandled C++ exception'}


def exit_fields(code):
    """A close result's exit code fields; a nonzero code is flagged, never hidden."""
    if code is None:
        return dict(exit_code=None,exit_code_hex=None,clean_exit=None,exit_status='exit code unavailable')
    fields=dict(exit_code=code,exit_code_hex=f'0x{code:08X}',clean_exit=code==0)
    if code:fields['exit_status']=EXIT_STATUS.get(code,'nonzero exit code')
    return fields


class Session:
    def __init__(self,bin_dir,data=DATA):
        self.bin=bin_dir.resolve();self.data=data
        self.exe=self.bin/'Hero_Siege.exe'
        self.ipc=Ipc(self.bin);self.events=[]
        self.started_at=time.time()

    def send(self,command):
        reply=self.ipc.send(command,timeout=20)
        self.events.append(dict(command=command,reply=reply))
        require(reply is not None,'IPC timed out; no command retry or overwrite performed')
        return reply  # Raw diagnostics only; decisions read the JSON files.

    def state(self,pid):
        token=uuid.uuid4().hex
        path=self.data/'models'/f'session-state-{token}.json'
        self.send('afk session state '+token+' isolated')
        try:
            # Request-owned response survives concurrent old/new panel polling.
            # Old plugins still use the common path and must pass the same ID check.
            s=json.loads((path if path.exists() else self.data/'models/session-state.json').read_bytes())
        except (OSError,ValueError) as error:raise SessionError('No valid session JSON; install the session plugin') from error
        finally:path.unlink(missing_ok=True)
        validate_state(s,pid,token)
        self.events.append(dict(state=s))
        return s

    def ready_snapshot(self,pid,slot,name,class_id,state,expected_room=None):
        snapshot=self.state(pid)
        allowed_rooms={expected_room} if expected_room is not None else TOWNS
        require(matches(snapshot['character'],slot,name,class_id) and snapshot['room'] in allowed_rooms,
                'Final character/room verification failed')
        require(snapshot['room']==state['room'] and snapshot['online'] is False
                and snapshot['replay_running'] is False,'Context changed during readiness check')
        return dict(ready=True,pid=pid,character=snapshot['character'],room=snapshot['room'],session_state=snapshot)

    def prepare(self,slot,name,class_id,timeout):
        require(self.exe.is_file(),'Game executable not found')
        validate_plugin_directory(self.bin)
        require(hashlib.sha256(self.exe.read_bytes()).hexdigest()==EXE_SHA256,'Executable changed; verify build before automatic setup')
        pid=running(self.exe)
        if pid is None:
            proc=launch([str(self.exe)],self.bin)
            pid=proc.pid
            self.events.append(dict(launched_pid=pid,default_error_mode=True))
            deadline=time.monotonic()+min(timeout,60)
            while time.monotonic()<deadline:
                require(proc.poll() is None,'Game exited during startup')
                try:
                    build=json.loads((self.data/'build.json').read_bytes())
                    # Wait for this launch's own rewritten build receipt.
                    if (self.data/'build.json').stat().st_mtime>=self.started_at:break
                except (OSError,ValueError):pass
                time.sleep(.5)
            else:raise SessionError('Plugin startup timed out')
        deadline=time.monotonic()+timeout
        issued=set();previous_ready=False
        while time.monotonic()<deadline:
            s=self.state(pid)
            action,command=next_action(s,slot=slot,name=name,class_id=class_id)
            if action=='ready':
                if previous_ready:return self.ready_snapshot(pid,slot,name,class_id,s)
                previous_ready=True
            else:previous_ready=False
            if command and action not in issued:
                self.send(command);issued.add(action)
            time.sleep(.5)
        raise SessionError('Preparation timed out; inspect saved state/diagnostics before retrying')

    def travel(self,room,slot,name,class_id,timeout):
        destination=travel_target(room)
        require(self.exe.is_file(),'Game executable not found')
        require(hashlib.sha256(self.exe.read_bytes()).hexdigest()==EXE_SHA256,'Executable changed; verify build before automatic travel')
        pid=running(self.exe)
        require(pid is not None,'Game is closed; run prepare first')
        before=self.state(pid)
        command=travel_action(before,destination,slot=slot,name=name,class_id=class_id)
        if command:self.send(command)
        deadline=time.monotonic()+timeout
        previous_ready=False
        while time.monotonic()<deadline:
            s=self.state(pid)
            ready=travel_arrived(s,destination,slot=slot,name=name,class_id=class_id)
            if ready and previous_ready:
                result=self.ready_snapshot(pid,slot,name,class_id,s,expected_room=destination)
                result.update(from_room=before['room'],travel_requested=bool(command))
                return result
            previous_ready=ready
            time.sleep(.5)
        raise SessionError('Travel timed out; command was not retried; inspect diagnostics before continuing')

    def close(self):
        pid=running(self.exe)
        if pid is None:return dict(closed=True,already_closed=True,exit_code=None)
        s=self.state(pid)
        require(s.get('replay_running') is False,'Active replay: close refused')
        code=close_window(pid,self.exe)
        require(running(self.exe) is None,'Game closure not confirmed')
        return dict(closed=True,pid=pid,forced=False,**exit_fields(code))

    def save_receipt(self,result):
        folder=self.data/'models/test-sessions';folder.mkdir(parents=True,exist_ok=True)
        record=dict(at=datetime.now(timezone.utc).isoformat(),game_bin=str(self.bin),result=result,events=self.events)
        path=folder/(datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.json')
        path.write_text(json.dumps(record,indent=2,allow_nan=False),encoding='utf-8')
        return path


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','status','close','travel'))
    p.add_argument('--room',help='SDK room name for travel, e.g. Act_01_01 or Town_03_rm')
    p.add_argument('--game-bin',type=Path)
    p.add_argument('--slot',type=int,default=2)
    p.add_argument('--name',default='Suh')
    p.add_argument('--class-id',type=int,default=8)
    p.add_argument('--timeout',type=float,default=120)
    args=p.parse_args(argv)
    try:
        require(os.name=='nt','This helper requires Windows')
        require(0<=args.slot<128 and args.name and args.class_id>=0 and 1<=args.timeout<=300,'Invalid target or timeout')
        config=json.loads(CONFIG.read_bytes()) if CONFIG.exists() else {}
        bin_dir=args.game_bin or Path(config.get('game_bin') or os.environ.get('HS_GAME_BIN') or '')
        session=Session(bin_dir)
        try:
            if args.action=='prepare':result=session.prepare(args.slot,args.name,args.class_id,args.timeout)
            elif args.action=='travel':result=session.travel(args.room,args.slot,args.name,args.class_id,args.timeout)
            elif args.action=='close':result=session.close()
            else:
                pid=running(session.exe);result=session.state(pid) if pid else dict(running=False)
        except Exception as error:
            receipt=session.save_receipt(dict(error=str(error)))
            raise SessionError(f'{error}; diagnostics: {receipt}') from error
        receipt=session.save_receipt(result)
        print(json.dumps(dict(result=result,receipt=str(receipt)),ensure_ascii=True))
        if result.get('clean_exit') is False:
            # The close itself worked, so the command still succeeds; the crash is the finding.
            print(f"warning: the game exited with {result['exit_code_hex']} ({result['exit_status']})",file=sys.stderr)
        return 0
    except (SessionError,OSError,ValueError) as error:
        print(str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
