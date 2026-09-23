"""Collection log, wishlist and share summaries (0.7.0).

Spool files are synthetic records shaped like the plugin's; the real catalog
(web/collection.json) supplies names. Nothing contacts the game or Windows.
"""
import json, sys, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, collection, panel

ROOT = Path(__file__).resolve().parents[1]
HERO = dict(identity_version=2, slot=1, name='Suh', **{'class': 8})


def record(ident, seq, name, rarity, t='2026-09-24T10:00:00Z', kind='item'):
    return dict(expedition_id=ident, seq=seq, kind=kind, t=t, packet='p', type=6, name=name,
                item=dict(itemType=6, itemInfoStruct={'27': rarity, '28': name}), placed=True, filter_visible=True)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)
        (self.d / 'spool').mkdir(); (self.d / 'sessions').mkdir(); (self.d / 'plans').mkdir()
        self.catalog = collection.Catalog(ROOT)
        self.crest = self.catalog.by_key['helmet_harlequin_crest']

    def spool(self, ident, rows):
        (self.d / 'spool' / f'{ident}.ndjson').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')

    def claim(self, ident, rows, updated, hero=HERO, room='Act_01_01'):
        self.spool(ident, rows)
        afk.write_json(self.d / 'sessions' / f'{ident}.result.json', dict(rewards_saved=True, updated=updated, exp=100, gold=5))
        plan = dict(panel_version='0.7.0', character=hero, zones=[dict(room=room)], hours=2.0, scale=1.0, preview=dict(kills=500), label_region='The Glacial Trail')
        afk.write_json(self.d / 'plans' / f'{ident}.json', plan)
        return (ident, afk.read_json(self.d / 'sessions' / f'{ident}.result.json'), plan)

    def test_catalog_names_are_unique_and_cover_every_collectible_rarity(self):
        self.assertGreater(len(self.catalog.items), 900)
        self.assertEqual(len(self.catalog.by_name), len(self.catalog.items))
        self.assertEqual({e['rarity'] for e in self.catalog.items}, {'Unholy', 'Angelic', 'Heroic', 'Set', 'Satanic'})
        self.assertTrue(all(e['icon'] for e in self.catalog.items))

    def test_only_collectible_rarities_with_a_catalog_name_count(self):
        self.spool('a', [record('a', 1, self.crest['name'], 6), record('a', 2, self.crest['name'], 6),
                         record('a', 3, self.crest['name'], 2), record('a', 4, 'Cap', 6),
                         record('a', 5, self.crest['name'], 6, kind='summary')])
        found = collection.scan_spool(self.d / 'spool' / 'a.ndjson', self.catalog)
        self.assertEqual(list(found), ['helmet_harlequin_crest']); self.assertEqual(found['helmet_harlequin_crest']['count'], 2)
        self.assertEqual(found['helmet_harlequin_crest']['first_seq'], 1)

    def test_first_finds_follow_delivery_order_and_are_cached(self):
        older = self.claim('old_claim', [record('old_claim', 1, self.crest['name'], 6)], '2026-09-20T00:00:00Z')
        newer = self.claim('new_claim', [record('new_claim', 1, self.crest['name'], 6), record('new_claim', 2, 'Angel', 6)], '2026-09-24T00:00:00Z',
                           hero=dict(HERO, slot=2, name='Sgham'))
        box = collection.Collection(self.d, ROOT)
        found, firsts = box.log([newer, older])
        self.assertEqual(found['helmet_harlequin_crest']['first_claim'], 'old_claim')
        self.assertEqual(found['helmet_harlequin_crest']['count'], 2); self.assertEqual(found['helmet_harlequin_crest']['heroes'], ['Suh', 'Sgham'])
        self.assertEqual(firsts, {'old_claim': ['helmet_harlequin_crest'], 'new_claim': ['w_gun_angel']})
        with patch.object(collection, 'scan_spool', side_effect=AssertionError('unchanged spools come from the cache')):
            collection.Collection(self.d, ROOT).log([older, newer])
        view = box.view([older, newer], dict(items=[dict(key='helmet_harlequin_crest')]))
        crest = next(i for i in view['items'] if i['key'] == 'helmet_harlequin_crest')
        self.assertTrue(crest['found'] and crest['wished']); self.assertEqual(view['found'], 2)
        self.assertEqual((view['by_rarity']['Satanic']['found'], view['by_rarity']['Set']['found']), (1, 1), 'Angel is a set piece')
        self.assertEqual(sum(s['found'] for s in view['sets']), 1)

    def test_wishlist_accepts_catalog_items_once_and_reports_hits(self):
        with self.assertRaisesRegex(ValueError, 'Choose a unique'): collection.wishlist_add(self.d, self.catalog, 'no_such_item')
        collection.wishlist_add(self.d, self.catalog, 'helmet_harlequin_crest'); collection.wishlist_add(self.d, self.catalog, 'helmet_harlequin_crest')
        self.assertEqual([w['key'] for w in collection.load_wishlist(self.d)['items']], ['helmet_harlequin_crest'])
        self.claim('x_claim', [record('x_claim', 1, self.crest['name'], 6)], '2026-09-24T00:00:00Z')
        box = collection.Collection(self.d, ROOT)
        hits = collection.wishlist_hits(box, 'x_claim', collection.load_wishlist(self.d))
        self.assertEqual([(h['name'], h['count']) for h in hits], [(self.crest['name'], 1)])
        shown = []
        self.assertTrue(collection.notify_hits_once(self.d, 'x_claim', hits, lambda t, m: shown.append((t, m)), 'Suh', 'The Glacial Trail'))
        self.assertFalse(collection.notify_hits_once(self.d, 'x_claim', hits, lambda t, m: shown.append((t, m))), 'never twice for one claim')
        self.assertEqual(len(shown), 1); self.assertIn("Suh found Harlequinn's Crest in The Glacial Trail", shown[0][1])
        collection.wishlist_remove(self.d, 'helmet_harlequin_crest')
        self.assertEqual(collection.load_wishlist(self.d)['items'], [])

    def test_the_panel_serves_collection_share_and_wishlist_actions(self):
        self.claim('farm_1_claim', [record('farm_1_claim', 1, self.crest['name'], 6)], '2026-09-24T10:00:00Z')
        chars = [dict(HERO, class_name='Samurai', level=90)]
        with patch.object(panel, 'characters', return_value=chars):
            app = panel.Panel(self.d); app.job = dict(output='')
            app.action('wishlist_add', dict(key='helmet_harlequin_crest'))
            snap = app.snapshot()
            self.assertEqual(snap['collection'], dict(total=len(self.catalog.items), found=1, wishlist=1))
            reward = next(r for r in snap['rewards'] if r['id'] == 'farm_1_claim')
            self.assertEqual(reward['new_finds'], 1); self.assertEqual(reward['wishlist_hits'][0]['key'], 'helmet_harlequin_crest')
            share = app.share_summary('farm_1_claim')
            self.assertEqual((share['hero']['name'], share['hero']['class_name'], share['hours'], share['new_finds_total']), ('Suh', 'Samurai', 2.0, 1))
            with self.assertRaisesRegex(ValueError, 'Invalid'): app.share_summary('../x')
            with self.assertRaisesRegex(ValueError, 'Only a delivered'): app.share_summary('missing_claim')
            server = panel.ThreadingHTTPServer(('127.0.0.1', 0), panel.Handler); server.app = app
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
            base = f'http://127.0.0.1:{server.server_port}'
            with urllib.request.urlopen(base + '/api/collection') as r: self.assertEqual(json.load(r)['found'], 1)
            with urllib.request.urlopen(base + '/api/share?id=farm_1_claim') as r: self.assertEqual(json.load(r)['hero']['name'], 'Suh')
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(base + '/api/share?id=nope')
            self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
