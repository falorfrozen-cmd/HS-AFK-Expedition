"""Windows "expedition ready" notification: the Task Scheduler task follows the
armed expedition and the setting. Task Scheduler is faked; nothing is created.
"""
import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import notify, panel

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
ARMED = dict(expedition_id='farm_x', started_at='2026-09-23T09:30:00Z', hours=2.0, plan='unused')
PLAN = dict(label_hero='Suh', label_region='The Glacial Trail')


class FakeScheduler:
    def __init__(self, code=0):
        self.calls, self.code, self.xml = [], code, None
    def __call__(self, argv, **kwargs):
        self.calls.append(argv[1])
        if argv[1] == '/Create':
            self.xml = Path(argv[argv.index('/XML') + 1]).read_text(encoding='utf-16')
        return SimpleNamespace(returncode=self.code, stdout='', stderr='ERROR: Access is denied.' if self.code else '')


@unittest.skipUnless(os.name == 'nt', 'Windows Task Scheduler only')
class ReadyNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.d = Path(self.tmp.name)

    def test_task_runs_once_at_the_end_time_hidden_and_without_elevation(self):
        run = FakeScheduler()
        record = notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=run)
        self.assertEqual(run.calls, ['/Create'])
        self.assertEqual(record['scheduled'], dict(expedition_id='farm_x', at='2026-09-23T11:30:00+00:00'))
        local = datetime(2026, 9, 23, 11, 30, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        self.assertIn(f'<StartBoundary>{local.isoformat()}</StartBoundary>', run.xml)
        self.assertIn('<StartWhenAvailable>true</StartWhenAvailable>', run.xml)
        self.assertIn('<RunLevel>LeastPrivilege</RunLevel>', run.xml)
        self.assertIn('<Command>conhost.exe</Command>', run.xml)
        self.assertIn('--headless powershell.exe', run.xml)
        self.assertIn('Suh&apos;s expedition in The Glacial Trail is ready' if '&apos;' in run.xml else "Suh's expedition in The Glacial Trail is ready", run.xml)
        self.assertIn('&quot;AFK FARM\\Expedition ready&quot;', run.xml)
        self.assertFalse((self.d / 'notify-ready.xml').exists(), 'the request file is removed')
        script = (self.d / notify.SCRIPT).read_text(encoding='utf-8-sig')
        self.assertIn(notify.POWERSHELL_APP_ID, script); self.assertIn('/Delete /TN $Task /F', script)

    def test_unchanged_state_does_nothing_and_a_claim_or_the_setting_removes_it(self):
        run = FakeScheduler()
        notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=run)
        notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=run)
        self.assertEqual(run.calls, ['/Create'])
        record = notify.sync(self.d, None, True, None, now=NOW, run=run)
        self.assertEqual((run.calls, record['scheduled']), (['/Create', '/Delete'], None))
        notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=run)
        notify.sync(self.d, ARMED, False, PLAN, now=NOW, run=run)
        self.assertEqual(run.calls[-2:], ['/Create', '/Delete'])
        other = dict(ARMED, expedition_id='farm_y')
        notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=run); notify.sync(self.d, other, True, PLAN, now=NOW, run=run)
        self.assertEqual(run.calls[-3:], ['/Create', '/Delete', '/Create'], 'a new expedition replaces the old task')

    def test_a_past_end_time_or_a_refused_task_is_not_retried(self):
        run = FakeScheduler()
        self.assertIsNone(notify.sync(self.d, ARMED, True, PLAN, now=NOW + timedelta(hours=3), run=run)['scheduled'])
        self.assertEqual(run.calls, [])
        refused = FakeScheduler(code=1)
        record = notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=refused)
        self.assertIsNone(record['scheduled']); self.assertIn('Access is denied', record['error'])
        notify.sync(self.d, ARMED, True, PLAN, now=NOW, run=refused)
        self.assertEqual(refused.calls, ['/Create'])
        for bad in (dict(ARMED, started_at='yesterday'), dict(ARMED, started_at='2026-09-23T09:30:00'), dict(ARMED, hours=None)):
            with self.subTest(bad=bad):
                self.assertIsNone(notify.sync(self.d, bad, True, PLAN, now=NOW, run=FakeScheduler())['scheduled'])

    def test_texts_fall_back_and_never_break_the_command_line(self):
        self.assertEqual(notify.texts({})[1], "Your hero's expedition in its region is ready. Open AFK FARM and claim with Your hero in its region.")
        title, message = notify.texts(dict(character=dict(name='A"B'), zones=[dict(room='Act_01_01')]))
        self.assertNotIn('"', message); self.assertIn("A'B", message)


class PreferenceTests(unittest.TestCase):
    def test_setting_defaults_on_and_saves_separately_from_the_speed(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            self.assertTrue(panel.load_preferences(d)['ready_notification'])
            app = panel.Panel(d); app.job = dict(output='')
            with patch.object(app, 'sync_notification'):
                app.action('save_preferences', dict(ready_notification=False))
                self.assertEqual(panel.load_preferences(d), dict(schema=1, delivery_speed='normal', ready_notification=False))
                app.action('save_preferences', dict(delivery_speed='fast'))
                self.assertEqual(panel.load_preferences(d)['ready_notification'], False, 'saving the speed keeps the setting')
                for bad in (dict(ready_notification='yes'), dict(), dict(delivery_speed='warp')):
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        app.action('save_preferences', bad)


if __name__ == '__main__':
    unittest.main()
