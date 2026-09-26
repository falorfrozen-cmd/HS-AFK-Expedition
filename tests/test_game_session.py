import contextlib
import copy
import ctypes
import hashlib
import importlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import time
import unittest
import tempfile
import json
from pathlib import Path
from unittest import mock

TESTS=Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / 'tools'))
import game_session
from game_session import Session, next_action, SessionError, validate_state, travel_action, travel_arrived, travel_target,validate_plugin_directory
from game_session import SdkMissing, close_window, exit_fields, launch, load_sdk, main, sdk_folder

PYTHONW=Path(getattr(sys,'_base_executable',sys.executable)).with_name('pythonw.exe')
SEM_NOGPFAULTERRORBOX=0x0002
windows_processes=unittest.skipUnless(os.name=='nt' and PYTHONW.is_file(),'needs Windows and pythonw.exe')


def state(room='Main_Menu_rm'):
    return dict(document_type='afk.session-state', schema=1, request_id='request1',
                pid=123, game_build='exe-281751552-pe6aaa6779-111b8000',
                room=room, online=False, replay_running=False, player_count=0,
                menu_count=1, choose_count=0, character_menu_count=0,
                selected_slot=0, character={}, selected_character={}, choices=[])


class SessionFlowTests(unittest.TestCase):
    def test_request_owned_response_survives_another_panel_poll(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Session.__new__(Session);session.data=Path(temp);session.events=[]
            folder=session.data/'models';folder.mkdir()
            def send(command):
                token=command.split()[3]
                self.assertTrue(command.endswith(' isolated'))
                ours=state();ours['request_id']=token
                (folder/f'session-state-{token}.json').write_text(json.dumps(ours))
                other=state();other['request_id']='another-panel'
                (folder/'session-state.json').write_text(json.dumps(other))
            session.send=send
            self.assertEqual(session.state(123)['pid'],123)
            self.assertEqual(list(folder.glob('session-state-*.json')),[])

    def test_plugin_backups_cannot_load_as_second_observer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'mods/aurie';folder.mkdir(parents=True)
            with self.assertRaises(SessionError):validate_plugin_directory(root)
            (folder/'HSAfkExpeditionPlugin.dll').touch();(folder/'BloodPactPlugin.dll').touch()
            (root/'HSAfkExpeditionPlugin.backup.dll').touch()
            validate_plugin_directory(root)
            (folder/'HSAfkExpeditionPlugin.old.dll').touch()
            with self.assertRaises(SessionError):validate_plugin_directory(root)

    def decide(self, s):
        return next_action(s, slot=2, name='Suh', class_id=8)

    def test_main_menu_uses_game_local_action(self):
        self.assertEqual(self.decide(state()),
                         ('local', 'afk callon Menu_Controller_obj 0 UiAMainMenuLocal'))

    def test_selects_reported_instance_not_slot_as_ordinal(self):
        s=state('Chose_rm');s['choose_count']=1
        s['choices']=[dict(ordinal=7, slot=2, name='Suh', **{'class':8}, enabled=True)]
        self.assertEqual(self.decide(s),
                         ('choose', 'afk callon Choose_Parent_obj 7 UiAChooseSaveSlot'))

    def test_play_requires_exact_selected_identity(self):
        s=state('Chose_rm');s.update(character_menu_count=1, selected_slot=2,
                                    selected_character={'slot':2,'name':'Suh','class':8})
        self.assertEqual(self.decide(s),
                         ('play', 'afk callon UI_Character_obj 0 UiACharacterPlay'))
        s['selected_character']['name']='Another'
        with self.assertRaises(SessionError):self.decide(s)

    def test_town_ready_is_idempotent(self):
        s=state('Town_03_rm');s.update(player_count=1, character={
            'identity_version':2,'slot':2,'name':'Suh','class':8})
        self.assertEqual(self.decide(s),('ready',None))
        s['room']='Act_03_04'
        with self.assertRaises(SessionError):self.decide(s)

    def test_refuses_ambiguous_missing_disabled_or_mismatched_selection(self):
        s=state('Chose_rm');s['choose_count']=1
        good=dict(ordinal=2,slot=2,name='Suh',**{'class':8},enabled=True)
        for choices in ([],[good,good],[dict(good,name='Other')],[dict(good,enabled=False)]):
            with self.subTest(choices=choices):
                s['choices']=choices
                with self.assertRaises(SessionError):self.decide(s)

    def test_rejects_online_unknown_replay_and_wrong_character(self):
        for field,value in [('online',True),('online',None),('replay_running',True),('player_count',2)]:
            s=state();s[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(SessionError):self.decide(s)
        s=state('Town_03_rm');s.update(player_count=1,character={'slot':1,'name':'Suh','class':8})
        with self.assertRaises(SessionError):self.decide(s)

    def test_loading_only_waits(self):
        self.assertEqual(self.decide(state('Game_Start_rm')),('wait',None))
        with self.assertRaises(SessionError):self.decide(state('PVP_Arena_1_rm'))

    def test_rejects_stale_or_foreign_process_state(self):
        s=state();validate_state(s,123,'request1')
        for field,value in [('pid',124),('request_id','old'),('schema',2),('game_build','unknown')]:
            bad=copy.deepcopy(s);bad[field]=value
            with self.subTest(field=field),self.assertRaises(SessionError):validate_state(bad,123,'request1')


class TravelTests(unittest.TestCase):
    def setUp(self):
        self.state=state('Town_03_rm')
        self.state.update(player_count=1,character={'slot':2,'name':'Suh','class':8})
        self.target=dict(slot=2,name='Suh',class_id=8)

    def test_town_to_act_and_back_use_existing_game_transition(self):
        self.assertEqual(travel_action(self.state,'Act_01_01',**self.target),'afk goto Act_01_01')
        self.state['room']='Act_03_04'
        self.assertEqual(travel_action(self.state,'Town_03_rm',**self.target),'afk goto Town_03_rm')

    def test_same_room_needs_no_transition(self):
        self.assertIsNone(travel_action(self.state,'Town_03_rm',**self.target))

    def test_only_named_normal_act_and_town_destinations(self):
        for room in ('Main_Menu_rm','Init_rm','Game_Start_rm','Chose_rm','PVP_Arena_1_rm',
                     'Dev_1_rm','Act_99_99','Town_99_rm','Act_01_01\naf k status',''):
            with self.subTest(room=room),self.assertRaises(SessionError):travel_target(room)

    def test_travel_requires_correct_offline_player_and_inactive_replay(self):
        for field,value in [('online',True),('online',None),('replay_running',True),
                            ('player_count',0),('player_count',2),('room','Main_Menu_rm'),
                            ('character',{'slot':1,'name':'Other','class':8})]:
            s=copy.deepcopy(self.state);s[field]=value
            with self.subTest(field=field),self.assertRaises(SessionError):
                travel_action(s,'Act_01_01',**self.target)

    def test_arrival_waits_for_room_and_player_then_confirms_exact_identity(self):
        self.assertFalse(travel_arrived(self.state,'Act_01_01',**self.target))
        self.state.update(room='Act_01_01',player_count=0,character={})
        self.assertFalse(travel_arrived(self.state,'Act_01_01',**self.target))
        self.state.update(player_count=1,character={'slot':2,'name':'Suh','class':8})
        self.assertTrue(travel_arrived(self.state,'Act_01_01',**self.target))
        self.state['character']['name']='Another'
        with self.assertRaises(SessionError):travel_arrived(self.state,'Act_01_01',**self.target)


class LaunchTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows STARTUPINFO')
    def test_launch_is_minimized_unactivated_with_the_default_error_mode(self):
        with mock.patch.object(game_session.subprocess,'Popen') as popen:launch(['Hero_Siege.exe'],'bin')
        (command,),options=popen.call_args
        self.assertEqual((command,options['cwd']),(['Hero_Siege.exe'],'bin'))
        self.assertEqual(options['creationflags']&0x04000000,0x04000000)  # CREATE_DEFAULT_ERROR_MODE
        self.assertTrue(options['startupinfo'].dwFlags&subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(options['startupinfo'].wShowWindow,7)  # SW_SHOWMINNOACTIVE

    def test_prepare_starts_a_closed_game_through_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            game=Path(temp);(game/'mods/aurie').mkdir(parents=True)
            (game/'mods/aurie/HSAfkExpeditionPlugin.dll').touch();(game/'Hero_Siege.exe').write_bytes(b'stand-in')
            session=Session(game,data=game/'data')
            exited=mock.Mock(pid=321);exited.poll.return_value=1
            with mock.patch.object(game_session,'EXE_SHA256',hashlib.sha256(b'stand-in').hexdigest()), \
                 mock.patch.object(game_session,'running',return_value=None), \
                 mock.patch.object(game_session,'launch',return_value=exited) as start, \
                 self.assertRaisesRegex(SessionError,'exited during startup'):
                session.prepare(2,'Suh',8,5)
            start.assert_called_once_with([str(session.exe)],session.bin)
            self.assertIn(dict(launched_pid=321,default_error_mode=True),session.events)

    @windows_processes
    def test_the_game_does_not_inherit_an_error_mode_that_hides_crashes(self):
        # Git Bash runs with SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX (0x3). A game that
        # inherits it crashes with no WER report or dump, e.g. at an exit-time abort.
        kernel32=ctypes.WinDLL('kernel32')
        report='import ctypes,sys;open(sys.argv[1],"w").write(str(ctypes.WinDLL("kernel32").GetErrorMode()))'
        with tempfile.TemporaryDirectory() as temp:
            def child_mode(start,name):
                out=Path(temp)/name
                self.assertEqual(start([str(PYTHONW),'-c',report,str(out)]).wait(timeout=60),0)
                return int(out.read_text())
            previous=kernel32.SetErrorMode(0x0003)
            try:
                inherited=child_mode(subprocess.Popen,'inherited.txt')
                launched=child_mode(lambda command:launch(command,temp),'launched.txt')
            finally:kernel32.SetErrorMode(previous)
        self.assertTrue(inherited&SEM_NOGPFAULTERRORBOX,'control: a plain child keeps the mode')
        self.assertFalse(launched&SEM_NOGPFAULTERRORBOX)


class CloseTests(unittest.TestCase):
    def test_nonzero_exit_codes_are_flagged_with_hex_and_name(self):
        self.assertEqual(exit_fields(0),dict(exit_code=0,exit_code_hex='0x00000000',clean_exit=True))
        abort=exit_fields(0xC0000409)
        self.assertEqual((abort['exit_code'],abort['exit_code_hex'],abort['clean_exit']),(3221226505,'0xC0000409',False))
        self.assertIn('STATUS_STACK_BUFFER_OVERRUN',abort['exit_status'])
        self.assertEqual(exit_fields(1)['exit_status'],'nonzero exit code')
        self.assertEqual(exit_fields(None),dict(exit_code=None,exit_code_hex=None,clean_exit=None,
                                                exit_status='exit code unavailable'))

    def test_close_script_takes_the_handle_first_and_never_forces(self):
        scripts=[]
        def powershell(code):scripts.append(code);return '{"exit_code":-1073740791}'
        with mock.patch.object(game_session,'powershell',powershell):
            self.assertEqual(close_window(123,'C:/Games/Hero_Siege.exe'),0xC0000409)
        script=scripts[0]
        steps=[script.index(step) for step in ('$game.Handle','CloseMainWindow()','WaitForExit(20000)','$game.ExitCode')]
        self.assertEqual(steps,sorted(steps))
        for forced in ('Kill','Stop-Process','taskkill','Terminate'):self.assertNotIn(forced,script)

    def test_unreadable_exit_code_stays_unknown(self):
        for raw in ('{"exit_code":null}','','not json','[]','{"exit_code":true}'):
            with self.subTest(raw=raw),mock.patch.object(game_session,'powershell',return_value=raw):
                self.assertIsNone(close_window(123,'Hero_Siege.exe'))

    def test_close_result_and_receipt_keep_the_exit_code(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Session(Path(temp),data=Path(temp)/'data')
            with mock.patch.object(game_session,'running',side_effect=[77,None]), \
                 mock.patch.object(Session,'state',return_value=dict(replay_running=False)), \
                 mock.patch.object(game_session,'close_window',return_value=0xC0000409) as close:
                result=session.close()
            close.assert_called_once_with(77,session.exe)
            record=json.loads(session.save_receipt(result).read_text(encoding='utf-8'))
        self.assertEqual(record['result'],dict(closed=True,pid=77,forced=False,exit_code=3221226505,exit_code_hex='0xC0000409',
                                               clean_exit=False,exit_status=exit_fields(0xC0000409)['exit_status']))

    def test_a_game_that_is_already_closed_has_no_exit_code(self):
        session=Session.__new__(Session);session.exe=Path('Hero_Siege.exe')
        with mock.patch.object(game_session,'running',return_value=None):
            self.assertEqual(session.close(),dict(closed=True,already_closed=True,exit_code=None))

    @unittest.skipUnless(os.name=='nt','the helper requires Windows')
    def test_cli_warns_about_a_nonzero_exit_and_still_succeeds(self):
        for code,warning in ((0xC0000409,'warning: the game exited with 0xC0000409 (STATUS_STACK_BUFFER_OVERRUN'),(0,'')):
            out,err=io.StringIO(),io.StringIO()
            with self.subTest(code=hex(code)),tempfile.TemporaryDirectory() as temp, \
                 mock.patch.object(game_session,'CONFIG',Path(temp)/'config.json'), \
                 mock.patch.object(Session,'close',return_value=dict(closed=True,pid=77,forced=False,**exit_fields(code))), \
                 mock.patch.object(Session,'save_receipt',return_value=Path(temp)/'receipt.json'), \
                 contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                self.assertEqual(main(['close','--game-bin',temp]),0)
                self.assertEqual(json.loads(out.getvalue())['result']['exit_code'],code)
                self.assertTrue(err.getvalue().startswith(warning),err.getvalue())
                if not warning:self.assertEqual(err.getvalue(),'')


@windows_processes
class CloseWindowProcessTests(unittest.TestCase):
    """close_window against a real process: tests/close_stand_in.py, never the game."""

    def start_stand_in(self,temp,code):
        info=Path(temp)/f'stand-in-{code:08x}.json'
        # A short-lived parent starts the stand-in, so no handle of this test keeps the
        # exited process readable: close_window's own handle is the only one.
        subprocess.run([sys.executable,'-c','import subprocess,sys;subprocess.Popen(sys.argv[1:])',
                        str(PYTHONW),str(TESTS/'close_stand_in.py'),str(info),hex(code)],
                       check=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+60
        while not info.exists():
            self.assertLess(time.monotonic(),deadline,'the stand-in window never appeared')
            time.sleep(.05)
        stand_in=json.loads(info.read_text(encoding='utf-8'))
        self.assertNotIn('error',stand_in,stand_in.get('error'))
        return stand_in

    def test_exit_code_of_a_process_this_test_never_held(self):
        with tempfile.TemporaryDirectory() as temp:
            for code in (0xC0000409,0):
                with self.subTest(code=hex(code)):
                    stand_in=self.start_stand_in(temp,code)
                    self.assertEqual(close_window(stand_in['pid'],stand_in['image']),code)


@contextlib.contextmanager
def sdk_hidden(*installed):
    """Imports cannot see the real hs_game_sdk; `installed` folders act as site-packages."""
    saved={name:module for name,module in sys.modules.items() if name.split('.')[0]=='hs_game_sdk'}
    path=[p for p in sys.path if not (Path(p or '.')/'hs_game_sdk').exists()]+[str(p) for p in installed]
    with mock.patch.object(sys,'path',path):
        for name in saved:del sys.modules[name]
        importlib.invalidate_caches()
        try:yield
        finally:
            for name in [name for name in sys.modules if name.split('.')[0]=='hs_game_sdk']:del sys.modules[name]
            sys.modules.update(saved)


class SdkLookupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.temp=Path(temp.name);self.repo=self.temp/'hub/HS-AFK-Expedition'

    def package(self,folder,mark):
        (folder/'hs_game_sdk').mkdir(parents=True)
        (folder/'hs_game_sdk/__init__.py').write_text(f'MARK={mark!r}\n',encoding='utf-8')
        return folder

    def test_environment_first_then_the_sibling_checkout_then_an_installed_package(self):
        configured=self.package(self.temp/'elsewhere/hs-game-sdk/python','environment').parent
        self.package(self.temp/'hub/hs-game-sdk/python','sibling')
        installed=self.package(self.temp/'site-packages','installed')
        with sdk_hidden(installed):self.assertEqual(load_sdk({'HS_GAME_SDK':str(configured)},self.repo).MARK,'environment')
        with sdk_hidden(installed):self.assertEqual(load_sdk({},self.repo).MARK,'sibling')
        shutil.rmtree(self.temp/'hub/hs-game-sdk')
        with sdk_hidden(installed):self.assertEqual(load_sdk({},self.repo).MARK,'installed')

    def test_environment_accepts_the_checkout_or_its_python_folder(self):
        python=self.package(self.temp/'hs-game-sdk/python','environment')
        for configured in (python.parent,python):
            with self.subTest(configured=configured.name):
                self.assertEqual(sdk_folder({'HS_GAME_SDK':str(configured)},self.repo),python.resolve())
        with contextlib.chdir(self.temp):  # A relative setting never reaches sys.path as relative.
            self.assertEqual(sdk_folder({'HS_GAME_SDK':'hs-game-sdk'},self.repo),python.resolve())

    def test_environment_without_the_package_is_refused_not_skipped(self):
        self.package(self.temp/'hub/hs-game-sdk/python','sibling')
        with self.assertRaisesRegex(SdkMissing,'HS_GAME_SDK=.*point it at an hs-game-sdk checkout'):
            sdk_folder({'HS_GAME_SDK':str(self.temp/'empty')},self.repo)

    def test_missing_sdk_names_every_way_to_provide_it(self):
        with sdk_hidden():
            if importlib.util.find_spec('hs_game_sdk'):self.skipTest('an installed hs_game_sdk is importable')
            with self.assertRaises(SdkMissing) as caught:load_sdk({},self.repo)
        self.assertIsInstance(caught.exception,ImportError)
        for part in ('HS_GAME_SDK','beside this repository','installed package','PYTHONPATH'):
            self.assertIn(part,str(caught.exception))

    def test_cli_reports_a_missing_sdk_in_one_line(self):
        tools=self.repo/'tools';tools.mkdir(parents=True)
        shutil.copy2(TESTS.parent/'tools/game_session.py',tools)
        env={k:v for k,v in os.environ.items() if k!='HS_GAME_SDK'}
        # -E ignores PYTHONPATH and -s the user site; a system-wide install is still found.
        probe=subprocess.run([sys.executable,'-E','-s','-c','import importlib.util,sys;sys.exit(bool(importlib.util.find_spec("hs_game_sdk")))'],
                             env=env,cwd=self.temp,timeout=60)
        if probe.returncode:self.skipTest('an installed hs_game_sdk is importable')
        run=subprocess.run([sys.executable,'-B','-E','-s',str(tools/'game_session.py'),'status'],
                           capture_output=True,text=True,env=env,cwd=self.temp,timeout=60)
        self.assertEqual(run.returncode,1)
        self.assertTrue(run.stderr.startswith('hs_game_sdk not found.'),run.stderr)
        self.assertNotIn('Traceback',run.stderr)


if __name__=='__main__':unittest.main()
