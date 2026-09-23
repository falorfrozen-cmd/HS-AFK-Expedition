import copy
import sys
import unittest
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from game_session import Session, next_action, SessionError, validate_state, travel_action, travel_arrived, travel_target,validate_plugin_directory


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


if __name__=='__main__':unittest.main()
