import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import calibration


def sample(counts, room='Act_01_01'):
    rows = []
    for n in counts:
        for i in range(60):
            for _ in range(n // 60 + (i < n % 60)):
                rows.append(dict(kind='kill', room=room, packet='p'))
            rows.append(dict(kind='farm_clock', room=room, seconds=1))
    return rows


class QualityTests(unittest.TestCase):
    def test_one_minute_with_thirty_usable_kills_is_a_short_sample_not_an_accuracy_claim(self):
        rows=sample([30])
        q=calibration.quality(rows,'Act_01_01',{'p'})
        self.assertEqual(q['status'],'short');self.assertFalse(q['independently_validated'])
        self.assertEqual(calibration.quality(rows[:-1],'Act_01_01',{'p'})['status'],'insufficient')
        self.assertEqual(calibration.quality(sample([29]),'Act_01_01',{'p'})['status'],'insufficient')
    def test_steady_requires_enough_complete_windows(self):
        q = calibration.quality(sample([60]*10), 'Act_01_01', {'p'})
        self.assertEqual(q['status'], 'steady')
        self.assertEqual(q['window_rates'], [60]*10)
        self.assertFalse(q['independently_validated'])
        self.assertEqual(calibration.quality(sample([60]*3), 'Act_01_01', {'p'})['status'], 'short')

    def test_idle_and_drift_are_not_hidden(self):
        self.assertEqual(calibration.quality(sample([0,120]*5), 'Act_01_01', {'p'})['status'], 'variable')
        q = calibration.quality(sample([30]*5+[60]*5), 'Act_01_01', {'p'})
        self.assertGreater(q['half_drift'], .2)

    def test_town_and_unusable_kills_do_not_boost_pace(self):
        rows = sample([60]*10)+sample([600], 'Town_01_rm')
        self.assertEqual(calibration.quality(rows, 'Act_01_01', {'p'})['kills'], 600)
        self.assertEqual(calibration.quality(rows, 'Act_01_01', {'missing'})['status'], 'insufficient')

    def test_partial_window_is_not_scaled_into_full_window(self):
        rows = sample([60]*10)+[dict(kind='kill',room='Act_01_01',packet='p')]*90
        self.assertEqual(calibration.quality(rows, 'Act_01_01', {'p'})['window_rates'], [60]*10)

    def test_invalid_capture_never_looks_steady(self):
        rows = sample([60]*10)+[dict(kind='context_invalid')]
        self.assertEqual(calibration.quality(rows, 'Act_01_01', {'p'})['status'], 'invalid')

    def test_bad_clock_is_rejected(self):
        for seconds in (-1, float('nan'), float('inf'), 12):
            self.assertEqual(calibration.quality([dict(kind='farm_clock',room='Act_01_01',seconds=seconds)], 'Act_01_01')['status'], 'invalid')


if __name__ == '__main__': unittest.main()
