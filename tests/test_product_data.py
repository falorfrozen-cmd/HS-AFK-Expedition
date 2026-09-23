import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import product_data,item_labels,afk


class PresentationTests(unittest.TestCase):
    def test_native_rarity_field_is_not_the_old_guessed_enum(self):
        self.assertEqual(item_labels.rarity_name(7),'Angelic')
        self.assertEqual(item_labels.rarity_name(10),'Unholy')
        self.assertEqual(item_labels.rarity_name(8),'Tier 8')
    def test_real_loot_filter_and_bad_record_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'spool').mkdir();(root/'web').mkdir()
            afk.write_json(root/'web/items.json',{'native_key':{'name':'Known item','icon':'/assets/items/1.png'}})
            rows=[dict(kind='item',seq=1,name='native_key',filter_visible=False,item={'itemInfoStruct':{'27':7}}),dict(kind='summary')]
            (root/'spool/e.ndjson').write_text('\n'.join(map(json.dumps,rows))+'\nbroken')
            result=product_data.Presentation(root).loot(root,'e')
            self.assertEqual(result['total'],1);self.assertEqual(result['filtered'],1);self.assertEqual(result['unreadable'],1)
            self.assertEqual(result['items'][0]['name'],'Known item');self.assertEqual(result['rarities'],{'Angelic':1})
    def test_native_display_name_resolves_existing_icon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'spool').mkdir()
            afk.write_json(root/'web/items.json',{'boots_key':dict(name="Treasure Hunter's Boots",icon='/assets/items/25300.png')})
            (root/'spool/e.ndjson').write_text(json.dumps(dict(kind='item',seq=1,name="Treasure Hunter's Boots",item={'itemInfoStruct':{'27':6}})))
            result=product_data.Presentation(root).loot(root,'e')
            self.assertEqual(result['items'][0]['icon'],'/assets/items/25300.png')
    def test_unsupported_extra_rolls_are_explained(self):
        self.assertFalse(product_data.support_warnings({'angelic_items':1}))
        self.assertTrue(product_data.support_warnings({'angelic_items':2}))


if __name__=='__main__':unittest.main()
