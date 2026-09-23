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


if __name__=='__main__':unittest.main()
