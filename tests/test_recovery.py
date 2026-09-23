import hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import afk,recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.d=Path(self.tmp.name)
        self.plan=dict(expedition_id='e_claim',hours=1,scale=.5,packets=[dict(count=2)])
        self.path=self.d/'plans/e_claim.json';afk.write_json(self.path,self.plan)
        self.progress=dict(expedition_id='e_claim',checkpoint_version=2,state='done',plan_hash=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                           calls_done=2,calls_total=2,failed=0,skipped=0,items=1,gold=10,exp=20,save_committed=True,
                           saved='saved (character and account save performed)')
        afk.write_json(self.d/'sessions/e_claim.progress.json',self.progress)
        afk.write_json(self.d/'state.json',dict(armed=dict(expedition_id='e')))
        (self.d/'spool').mkdir()
        self.rows=[dict(expedition_id='e_claim',seq=1,kind='item',item={'native':True}),dict(expedition_id='e_claim',seq=2,kind='summary',calls=2,items=1,gold=10,exp_credited=20)]
        self.spool()
    def spool(self): (self.d/'spool/e_claim.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
    def test_saved_claim_settles_without_game_or_replay(self):
        result=recovery.settle(self.d,'e_claim')
        self.assertTrue(result['rewards_saved']);self.assertEqual(result['stages']['ingest'],'pending')
        self.assertNotIn('armed',recovery.read(self.d/'state.json'))
        with self.assertRaises(ValueError):recovery.settle(self.d,'e_claim')
    def test_unclean_failure_and_unsaved_are_never_recovered(self):
        for changes in [dict(state='running'),dict(save_committed=False),dict(saved='saved (room-end save performed)'),dict(failed=1),dict(calls_done=1),dict(plan_hash='wrong')]:
            afk.write_json(self.d/'sessions/e_claim.progress.json',dict(self.progress,**changes))
            with self.subTest(changes=changes),self.assertRaises(ValueError):recovery.settle(self.d,'e_claim')
    def test_failure_sidecar_wins_over_completed_checkpoint(self):
        afk.write_json(self.d/'sessions/e_claim.failure.json',{})
        self.assertFalse(recovery.inspect(self.d,'e_claim')['recoverable'])
    def test_spool_reward_totals_must_agree_with_saved_checkpoint(self):
        for key in ('gold','exp_credited'):
            original=self.rows[-1][key];self.rows[-1][key]+=1;self.spool()
            with self.subTest(key=key):self.assertFalse(recovery.inspect(self.d,'e_claim')['recoverable'])
            self.rows[-1][key]=original
    def test_corrupt_or_duplicate_spool_refuses(self):
        self.rows.insert(1,self.rows[0]);self.spool()
        self.assertFalse(recovery.inspect(self.d,'e_claim')['recoverable'])
        (self.d/'spool/e_claim.ndjson').write_text('{broken')
        self.assertFalse(recovery.inspect(self.d,'e_claim')['recoverable'])
    def test_result_write_interruption_can_be_reconciled_again(self):
        afk.write_json(self.d/'sessions/e_claim.result.json',dict(stages=dict(ingest='done')))
        self.assertTrue(recovery.settle(self.d,'e_claim')['success'])
    def test_cross_process_reward_lock(self):
        with recovery.data_lock(self.d):
            with self.assertRaises((OSError,ValueError)):
                with recovery.data_lock(self.d):pass


if __name__=='__main__':unittest.main()
