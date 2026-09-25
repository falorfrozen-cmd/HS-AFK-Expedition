"""Keys and jeweler materials from the Vault to the camp (0.8).

A fake Item Editor answers like POST /api/vault/afk-take: every request id is
carried out at most once, and a cancelled id never takes. The panel keeps one
receipt per request and settles an unclear answer by cancelling. No test
reaches the player's Item Editor, Vault, game or Task Scheduler.
"""
import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import afk, camp, panel, vault_take
import workers as W

HERO = dict(slot=0, name='Ilk', class_name='Viking')


class FakeEditor:
    """The editor's camp route in memory. ``lose``: 'before' (the take never arrives) or
    'after' (it is carried out and the answer lost); ``down``: every call fails."""

    def __init__(self, stock=None, lose=None, old=False):
        self.stock = dict(stock or {'12:0': 156, '12:1': 27, '14:5': 40, '12:33': 52, '14:60': 300})
        self.events = {}
        self.lose, self.old, self.down = lose, old, False
        self.calls = []

    def __call__(self, base, body, timeout=30.0):
        action = body['action']
        self.calls.append(action)
        if self.down:
            raise ConnectionRefusedError('editor closed')
        if self.old:
            raise vault_take.OldEditor(vault_take.OLD_EDITOR)
        if action == 'stock':
            return dict(category='AFK Materials', stock=[dict(cls=int(k.split(':')[0]), base=int(k.split(':')[1]), name=k, count=n, stacks=1)
                                                         for k, n in sorted(self.stock.items()) if n])
        request = body['requestId']
        if action == 'cancel':
            self.events.setdefault(request, ('cancelled', []))
            return self.reply(request)
        if self.lose == 'before':
            self.lose = None
            raise TimeoutError('timed out')
        if request in self.events:
            return self.reply(request)
        wanted = {f"{r['cls']}:{r['base']}": r['count'] for r in body['items']}
        short = [k for k, n in wanted.items() if self.stock.get(k, 0) < n]
        if short:
            return dict(err=f'AFK Materials has {self.stock.get(short[0], 0)} {short[0]}, not {wanted[short[0]]}. Nothing was taken.')
        for k, n in wanted.items():
            self.stock[k] -= n
        self.events[request] = ('done', [dict(cls=int(k.split(':')[0]), base=int(k.split(':')[1]), name=k, count=n) for k, n in sorted(wanted.items())])
        if self.lose == 'after':
            self.lose = None
            raise TimeoutError('timed out')
        return self.reply(request)

    def reply(self, request):
        state, taken = self.events[request]
        return dict(state=state, requestId=request, taken=taken, eventId=len(self.events))


class CampVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name) / 'afk'
        for sub in ('sessions', 'spool', 'plans', 'models'):
            (self.d / sub).mkdir(parents=True)
        afk.write_json(self.d / 'config.json', dict(game_bin=str(self.d)))
        (self.d / 'Hero_Siege.exe').write_bytes(b'x')
        self.app = panel.Panel(self.d); self.app.job = dict(output=''); self.app.editor = 'http://127.0.0.1:1'
        self.editor = FakeEditor()
        patchers = [patch.object(vault_take, 'call', self.editor),
                    # never find the player's Item Editor, Task Scheduler or the game from a test
                    patch.object(panel.ingest_spool, 'discover_editor', side_effect=AssertionError('a real Item Editor')),
                    patch.object(panel.notify, 'sync_all', side_effect=AssertionError('Task Scheduler')),
                    patch.object(panel.notify, 'toast', side_effect=AssertionError('Windows notification')),
                    patch.object(self.app, 'sync_notification'), patch.object(self.app, 'cli'),
                    patch.object(self.app, 'fresh', side_effect=AssertionError('the game'))]
        for p in patchers:
            p.start(); self.addCleanup(p.stop)

    def take(self, items):
        self.app.action('camp_take', dict(items=items))

    def camp(self):
        return W.load(self.d)['camp']

    def receipts(self):
        return {k: v['state'] for k, v in W.load(self.d)['vault_takes'].items()}

    def test_keys_go_onto_the_rack_and_materials_into_the_stock(self):
        self.take({'12:0': 20, '12:1': 5})
        self.take({'14:5': 12})
        self.assertEqual(self.camp()['keys'], {'0': 20, '1': 5})
        self.assertEqual(self.camp()['stock'], {'14:5': 12})
        self.assertEqual((self.editor.stock['12:0'], self.editor.stock['12:1'], self.editor.stock['14:5']), (136, 22, 28))
        self.assertEqual(sorted(self.receipts().values()), ['done', 'done'])
        self.assertIn('Took 20 Basic Key, 5 Crystal Key from the Vault for the camp.', self.app.job['output'])
        view = self.app.camp_vault_view()
        self.assertEqual({r['key']: (r['count'], r['goes_to']) for r in view['stock']},
                         {'12:0': (136, 'rack'), '12:1': (22, 'rack'), '14:5': (28, 'stock')})   # dungeon keys and fragments stay out
        self.assertEqual((view['editor'], view['key_cap'], view['key_room'], view['pending']), (True, 25, 0, []))

    def test_a_lost_answer_is_settled_at_once_and_never_taken_twice(self):
        self.editor.lose = 'after'
        self.take({'12:0': 10})
        self.assertEqual(self.camp()['keys'], {'0': 10})
        self.assertEqual(self.editor.stock['12:0'], 146)
        self.assertEqual(self.editor.calls, ['take', 'cancel'])
        self.app.settle_vault_takes(); self.app.settle_late_vault_takes()
        self.assertEqual(self.camp()['keys'], {'0': 10}, 'credited once')
        self.assertEqual(list(self.receipts().values()), ['done'])

    def test_an_editor_that_goes_quiet_is_settled_later_either_way(self):
        # Carried out, then the editor went quiet: the keys arrive once, later.
        self.editor.lose = 'after'
        with patch.object(vault_take, 'call', side_effect=self.quiet_cancel(self.editor)):
            with self.assertRaisesRegex(ValueError, 'did not answer.*nothing is ever taken twice'):
                self.take({'12:0': 4})
        self.assertEqual(list(self.receipts().values()), ['unknown'])
        self.assertEqual(self.app.workers_view()['pending_vault_takes'][0]['state'], 'unknown')
        self.assertEqual(self.camp()['keys'], {})
        self.app.settle_late_vault_takes()
        self.assertEqual(self.camp()['keys'], {'0': 4})
        self.assertEqual(list(self.receipts().values()), ['done'])
        # Never arrived, editor quiet: cancelled later, nothing taken, and the id can never take.
        self.editor.lose = 'before'
        with patch.object(vault_take, 'call', side_effect=self.quiet_cancel(self.editor)):
            with self.assertRaisesRegex(ValueError, 'did not answer'):
                self.take({'12:1': 3})
        self.app.settle_vault_takes()
        self.assertEqual(sorted(self.receipts().values()), ['done', 'refused'])
        self.assertEqual((self.camp()['keys'], self.editor.stock['12:1']), ({'0': 4}, 27))
        request = next(k for k, v in self.receipts().items() if v == 'refused')
        self.assertEqual(self.editor.events[request][0], 'cancelled')

    @staticmethod
    def quiet_cancel(editor):
        def call(base, body, timeout=30.0):
            if body['action'] == 'cancel':
                raise ConnectionResetError('editor closed')
            return editor(base, body, timeout)
        return call

    def test_nothing_is_taken_when_the_vault_or_the_camp_cannot_hold_it(self):
        self.editor.stock['12:1'] = 3
        with self.assertRaisesRegex(ValueError, r'did not give them: AFK Materials has 3 12:1, not 4\. Nothing was taken\.$'):
            self.take({'12:0': 1, '12:1': 4})
        self.assertEqual((self.camp()['keys'], self.editor.stock['12:0']), ({}, 156))
        self.assertEqual(list(self.receipts().values()), ['refused'])
        calls = len(self.editor.calls)
        with self.assertRaisesRegex(ValueError, 'key rack has room for 25 more keys'):
            self.take({'12:0': 26})
        with self.assertRaisesRegex(ValueError, "Jeweler's stock has room for 500 more"):
            self.take({'14:5': 501})
        self.assertEqual(len(self.editor.calls), calls, 'the editor is not asked when the camp has no room')
        for items in ({'12:33': 1}, {'14:60': 1}, {'12:0': 0}, {'12:0': 1.5}, {'12:0': True}, {}, None):
            with self.assertRaises(ValueError):
                self.take(items)
        self.assertEqual(len(self.receipts()), 1)

    def test_an_old_or_closed_editor_takes_nothing(self):
        self.editor.old = True
        with self.assertRaisesRegex(ValueError, '2.16.1 or newer'):
            self.take({'12:0': 1})
        self.assertEqual(list(self.receipts().values()), ['refused'])
        self.assertEqual(self.app.camp_vault_view()['error'], vault_take.OLD_EDITOR)
        self.app.editor = None
        with patch.object(panel.ingest_spool, 'discover_editor', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Open the Item Editor'):
                self.take({'12:0': 1})
            self.assertEqual(self.app.camp_vault_view()['editor'], False)
        self.assertEqual(self.camp()['keys'], {})

    def test_a_take_the_editor_carried_out_arrives_past_the_cap(self):
        self.editor.lose = 'after'
        with patch.object(vault_take, 'call', side_effect=self.quiet_cancel(self.editor)):
            with self.assertRaises(ValueError):
                self.take({'12:0': 25})
        state = W.load(self.d); state['camp']['keys'] = {'0': 10}; W.save(self.d, state)   # keys found meanwhile
        self.app.settle_vault_takes()
        self.assertEqual(self.camp()['keys'], {'0': 35})


if __name__ == '__main__':
    unittest.main()
