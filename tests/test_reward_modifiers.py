import copy
import sys
import unittest
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import reward_modifiers as rewards


class Rewards(unittest.TestCase):
    def test_defaults_and_invalid_values(self):
        self.assertTrue(all(rewards.normalize()[key] == 1 for key in rewards.KEYS))
        for value in (True, 0, -1, 101, float('inf'), float('nan'), '40'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rewards.normalize({'magic_find': value})
        for key in ('mining_ore', 'angelic_items', 'unknown'):
            with self.assertRaises(ValueError): rewards.normalize({key: 2})

    def test_reward_changes_preserve_pace_but_combat_changes_do_not(self):
        before = {'hash': 'old', 'inputs': {'character': {'slot': 1}, 'forgepact': {'stats': {'exp': 4, 'magicfind': 40, 'movespeed': 2}, 'keys': {'relic': 5}, 'percent_stats': {'critchance': 25}}}}
        after = copy.deepcopy(before)
        after['hash'] = 'new'
        after['inputs']['forgepact']['stats'].update(exp=100, magicfind=1)
        after['inputs']['forgepact']['keys']['relic'] = 1
        self.assertTrue(rewards.context_matches(before, after, True))
        self.assertFalse(rewards.context_matches(before, after))
        after['inputs']['forgepact']['percent_stats']['critchance'] = 50
        self.assertFalse(rewards.context_matches(before, after, True))
        self.assertEqual(before['inputs']['forgepact']['stats']['exp'], 4)
        self.assertFalse(rewards.context_matches({}, {}, True))

    def test_native_xp_not_calibrated_multiplier_and_settings_are_frozen(self):
        profile = {'reward_baseline': {'schema': 1, 'complete': True, 'magic_find': 1000}, 'packets': [{'hash': 'a', 'exp': 400, 'native_exp': 100}]}
        plan = {'packets': [{'hash': 'a', 'count': 10, 'exp': 400}], 'preview': {}}
        settings = {'experience': 2, 'magic_find': 40}
        rewards.apply_to_plan(plan, profile, settings)
        settings['experience'] = 99
        self.assertEqual(plan['preview']['exp'], 2000)
        self.assertEqual(plan['effective_magic_find'], 40000)
        self.assertEqual(plan['reward_modifiers']['experience'], 2)
        self.assertIsNone(plan['preview']['items_estimate'])
        self.assertEqual(profile['packets'][0]['exp'], 400)

    def test_legacy_xp_is_never_guessed(self):
        with self.assertRaisesRegex(ValueError, 'new calibration'):
            rewards.apply_to_plan({}, {'forgepact': {'exp': 4}}, {})

    def test_corrupt_settings_do_not_silently_reset_rewards(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'settings.json'
            self.assertEqual(rewards.load(path)['experience'],1)
            path.write_text('{broken')
            with self.assertRaisesRegex(ValueError,'Saved reward settings'):
                rewards.load(path)

    def test_invalid_native_mf_cannot_start_an_expedition(self):
        for value in (None,True,-1,float('nan')):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'Magic Find'):
                rewards.apply_to_plan({},dict(reward_baseline=dict(schema=1,complete=True,magic_find=value)),{})

if __name__ == '__main__': unittest.main()
