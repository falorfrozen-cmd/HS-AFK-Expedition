import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import validate_farm


class IndependentValidationTests(unittest.TestCase):
    def setUp(self):
        self.p=dict(room='Act_03_03',game_build='B',rate_basis='farm-clock',forgepact={},
                    character=dict(identity_version=2,name='Hero',slot=2,**{'class':8}),
                    farm_context=dict(hash='a'*64),basis_seconds=600,kills=600,coverage=1,
                    kills_per_min=60,exp_per_min=600,breaks_per_min=2)
        self.ref=dict(document_type='afk.validation-reference',created_at='2026-09-21T00:00:00Z',profile=self.p,
                      source_sha256='first',criteria=dict(min_seconds=600,min_kills=200,min_coverage=.95,max_relative_error=.2))
        self.rows=[dict(kind='session_start',t='2026-09-21T00:00:01Z'),dict(kind='session_stop')]
    def compare(self,p=None,digest='second',rows=None):return validate_farm.compare(self.ref,p or copy.deepcopy(self.p),rows or self.rows,digest)
    def test_independent_equal_rate_meets_declared_target(self):
        result=self.compare();self.assertEqual(result['status'],'within_target')
        self.assertEqual(result['metrics']['kills_per_min']['predicted'],600)
    def test_same_recording_cannot_validate_itself(self):
        with self.assertRaises(ValueError):self.compare(digest='first')
    def test_existing_recording_cannot_be_chosen_after_seeing_result(self):
        with self.assertRaises(ValueError):self.compare(rows=[dict(kind='session_start',t='2026-09-20T23:59:59Z'),dict(kind='session_stop')])
    def test_changed_context_refuses_comparison(self):
        p=copy.deepcopy(self.p);p['farm_context']['hash']='b'*64
        with self.assertRaises(ValueError):self.compare(p)
    def test_short_recording_is_evidence_not_a_pass(self):
        self.assertEqual(self.compare(dict(self.p,basis_seconds=334))['status'],'insufficient')
    def test_missed_forecast_is_reported(self):
        self.assertEqual(self.compare(dict(self.p,kills_per_min=120))['status'],'outside_target')
    def test_modified_reference_source_cannot_freeze_old_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'capture.ndjson';source.write_text('{"kind":"session_stop"}\n')
            p=dict(self.p,session=str(source),session_sha256=validate_farm.digest(source))
            source.write_text(source.read_text()+'{"kind":"kill"}\n')
            with self.assertRaises(ValueError):validate_farm.freeze(p,Path(folder)/'reference.json')


if __name__=='__main__':unittest.main()
