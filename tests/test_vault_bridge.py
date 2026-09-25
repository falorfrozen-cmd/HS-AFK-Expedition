"""AFK client -> real Item Editor HTTP handler -> temporary SQLite Vault.
The player's database and saves are never used.
"""
import contextlib,importlib.util,io,json,sys,tempfile,threading,unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import ingest_spool
EDITOR=Path(__file__).resolve().parents[2]/'hero-siege-item-editor'


@unittest.skipUnless((EDITOR/'test_vault_ingest.py').is_file(),'Sibling Item Editor source is needed for the integration test')
class VaultBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('afk_vault_contract_fixture',EDITOR/'test_vault_ingest.py')
        cls.fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.fixture)
    def test_offline_then_retry_then_duplicate_against_real_vault(self):
        e=self.fixture.editor
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);spool=root/'bridge.ndjson'
            spool.write_text('\n'.join(json.dumps(r) for r in (self.fixture.GEAR,self.fixture.MATERIAL,self.fixture.SUMMARY)))
            original=spool.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                with patch.object(ingest_spool,'discover_editor',return_value=None):
                    self.assertEqual(ingest_spool.main(['ingest',str(spool)]),2)
                self.assertEqual(spool.read_bytes(),original)
                with patch.object(e,'VAULT_DB_FILE',root/'vault.sqlite3'),patch.object(e,'_VAULT_STORE',None),patch.object(e,'_VAULT_STORE_PATH',None):
                    server=ThreadingHTTPServer(('127.0.0.1',0),e.H)
                    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                    try:
                        with patch.object(ingest_spool,'discover_editor',return_value=f'http://127.0.0.1:{server.server_port}'):
                            self.assertEqual(ingest_spool.main(['ingest',str(spool)]),0)
                            self.assertEqual(e.vault_store().count_items(),2)
                            self.assertEqual(ingest_spool.main(['ingest',str(spool)]),0)
                            self.assertEqual(e.vault_store().count_items(),2)
                        self.assertEqual(spool.read_bytes(),original)
                    finally:server.shutdown();server.server_close();thread.join(5)



@unittest.skipUnless((EDITOR/'test_vault_ingest.py').is_file(),'Sibling Item Editor source is needed for the integration test')
class CampVaultBridgeTests(unittest.TestCase):
    """The camp's Vault client and panel -> real Item Editor handler (2.16.1 or newer) -> temporary Vault."""
    @classmethod
    def setUpClass(cls):
        VaultBridgeTests.setUpClass();cls.fixture=VaultBridgeTests.fixture
    def test_keys_come_once_and_a_cancelled_request_never_takes(self):
        import vault_take,panel,afk
        import workers as W
        e=self.fixture.editor
        if not hasattr(e,'op_vault_afk_take'):self.skipTest('Item Editor 2.16.1 or newer is needed')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(e,'VAULT_DB_FILE',root/'vault.sqlite3'),patch.object(e,'_VAULT_STORE',None),patch.object(e,'_VAULT_STORE_PATH',None):
                keys=[self.fixture.spool_record(i,12,'Basic Key',{'b':0.0,'a':float(900+i),'j':0,'c':0.0,'o':60.0}) for i in (1,2)]
                keys.append(self.fixture.spool_record(3,12,'Crystal Key',{'b':1.0,'a':903.0,'j':0,'c':0.0,'o':7.0}))
                self.assertNotIn('err',e.op_vault_ingest({'expedition_id':'camp_bridge','records':keys}))
                server=ThreadingHTTPServer(('127.0.0.1',0),e.H)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base=f'http://127.0.0.1:{server.server_port}'
                stock=lambda:{f"{r['cls']}:{r['base']}":r['count'] for r in vault_take.stock(base)['stock']}
                try:
                    self.assertEqual(stock(),{'12:0':120,'12:1':7})
                    done=vault_take.take(base,'a'*32,{'12:0':50,'12:1':7})
                    self.assertEqual((done['state'],vault_take.taken(done)),('done',{'12:0':50,'12:1':7}))
                    again=vault_take.take(base,'a'*32,{'12:0':50})
                    self.assertEqual((again['state'],again['replayed'],vault_take.taken(again)),('done',True,{'12:0':50,'12:1':7}))
                    self.assertEqual(vault_take.cancel(base,'a'*32)['state'],'done')
                    self.assertEqual(vault_take.cancel(base,'b'*32)['state'],'cancelled')
                    self.assertEqual(vault_take.take(base,'b'*32,{'12:0':1})['state'],'cancelled')
                    self.assertIn('err',vault_take.take(base,'c'*32,{'12:0':71}))
                    self.assertEqual(stock(),{'12:0':70})
                    # The panel: its receipt, the key rack, and the lost-answer path settled by cancelling.
                    data=root/'afk'
                    for sub in ('sessions','spool','plans','models'):(data/sub).mkdir(parents=True)
                    afk.write_json(data/'config.json',dict(game_bin=str(data)));(data/'Hero_Siege.exe').write_bytes(b'x')
                    app=panel.Panel(data);app.job=dict(output='');app.editor=base
                    with patch.object(panel.ingest_spool,'discover_editor',side_effect=AssertionError('a real Item Editor')),                         patch.object(panel.notify,'sync_all',side_effect=AssertionError('Task Scheduler')),patch.object(app,'sync_notification'):
                        app.action('camp_take',dict(items={'12:0':20}))
                        real=vault_take.take
                        def lost(*args,**kwargs):
                            real(*args,**kwargs);raise TimeoutError('the answer was lost')
                        with patch.object(vault_take,'take',lost):app.action('camp_take',dict(items={'12:0':5}))
                        self.assertEqual(W.load(data)['camp']['keys'],{'0':25})
                        self.assertEqual(sorted(v['state'] for v in W.load(data)['vault_takes'].values()),['done','done'])
                        self.assertEqual(stock(),{'12:0':45})
                        self.assertEqual({r['key']:r['count'] for r in app.camp_vault_view()['stock']},{'12:0':45})
                finally:server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
