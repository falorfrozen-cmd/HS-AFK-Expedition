"""The packets' facts cache (0.9): read once, again only when a file is new or changed, dropped
when it is gone, and reused from disk by a fresh panel. Synthetic packets only.
"""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
os.environ['AFK_NOTIFY_DISABLED'] = '1'   # tests never schedule real Windows tasks or toasts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, packet_facts


def monster(h, rank=2, special=0):
    return dict(packet_hash=h * 64, room='Act_01_01', game_build_id='B', monster_key='e_orc_warrior_2', rank=rank, self_object='Orc_obj',
                script='gml_Script_DropItem', args=[rank, 0],
                protected=dict(max_hp=500.0, damage=5.0, killExperience=10.0, dSlots=1, dCommonChance=4, dCommonDropMult=11,
                               dSatanicDropMult=1, extraMagicFind=0, lootAmount=0),
                self_snapshot=dict(name='Grunt Champion', moveSpeed=2.4, isRanged=0, affixList=[0, 8, 44], specialType=special))


class PacketFactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)
        (self.d / 'packets').mkdir()
        p = patch.dict(afk.__dict__, dict(DATA=self.d, PACKETS=self.d / 'packets')); p.start(); self.addCleanup(p.stop)
        packet_facts.clear(); self.addCleanup(packet_facts.clear)

    def put(self, row):
        afk.write_json(self.d / 'packets' / f"{row['packet_hash']}.json", row)

    def test_facts_are_read_once_again_only_when_a_file_changes_and_dropped_when_it_goes(self):
        self.put(monster('a')); self.put(dict(monster('b'), self_object='Chest_Drop_obj', args=[3, 0], monster_key=''))
        facts, first = packet_facts.facts()
        self.assertEqual(facts['a' * 64]['monster']['affixes'], [0, 8], 'flags from 40 up are not affixes')
        self.assertEqual((facts['b' * 64]['monster'], facts['b' * 64]['first'], facts['b' * 64]['object']), (None, 3, 'Chest_Drop_obj'))
        with patch.object(packet_facts, 'read', side_effect=AssertionError('read again')):
            self.assertEqual(packet_facts.facts()[1], first, 'nothing changed: nothing is read')
        self.put(monster('c', special=9))
        os.remove(self.d / 'packets' / ('b' * 64 + '.json'))
        with patch.object(packet_facts, 'read', wraps=packet_facts.read) as reads:
            facts, second = packet_facts.facts()
        self.assertEqual(reads.call_count, 1, 'only the new file is read')
        self.assertEqual((sorted(facts), second > first), (['a' * 64, 'c' * 64], True))
        self.assertEqual(facts['c' * 64]['monster']['origin'], 'abyss')

    def test_a_fresh_panel_reuses_the_cache_on_disk(self):
        self.put(monster('a'))
        packet_facts.facts()
        self.assertTrue((self.d / packet_facts.FILE).exists())
        packet_facts.clear()
        with patch.object(packet_facts, 'read', side_effect=AssertionError('read again')):
            facts, _ = packet_facts.facts()
        self.assertEqual(list(facts), ['a' * 64])
        (self.d / packet_facts.FILE).write_text('{not json', encoding='utf-8')
        packet_facts.clear()
        self.assertEqual(list(packet_facts.facts()[0]), ['a' * 64], 'a broken cache is simply rebuilt')

    def test_a_packet_that_cannot_attack_has_no_monster_facts(self):
        for h, row in (('d', dict(monster('d'), protected={})), ('e', dict(monster('e'), rank=9)),
                       ('f', dict(monster('f'), script='gml_Script_DropRiftItems')), ('g', dict(monster('g'), self_object='Goblin_Rune_obj'))):
            self.put(row)
        facts, _ = packet_facts.facts()
        self.assertTrue(all(f['monster'] is None for f in facts.values()))


if __name__ == '__main__':
    unittest.main()
