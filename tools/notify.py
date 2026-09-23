"""Windows notification when an expedition is ready, even while AFK FARM is closed.

The panel keeps one Task Scheduler task per armed expedition (and per worker
trip), "AFK FARM\\Ready <key>", in step with the roster and the player's
setting: each runs once at its end time, shows a Windows notification through
Windows PowerShell (hidden, via ``conhost --headless``) and deletes itself.
Claiming, cancelling or switching the setting off removes it. Nothing else is
scheduled, and nothing runs with elevated rights. ``toast`` shows one
notification right away (a wishlist item dropped) without scheduling anything.
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

TASK = 'AFK FARM\\Expedition ready'          # 0.6.x: the single task, removed on the first roster sync
TASK_PREFIX = 'AFK FARM\\Ready '
KEY = re.compile(r'[A-Za-z0-9_-]{1,120}\Z')
RECORD = 'notify-ready.json'
SCRIPT = 'notify-ready.ps1'
# Windows PowerShell's own application id: toasts need a registered sender.
POWERSHELL_APP_ID = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
SCRIPT_TEXT = """# AFK FARM: shows "expedition ready" once, then removes its own scheduled task.
param([string]$Title, [string]$Message, [string]$Task)
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $title = [System.Security.SecurityElement]::Escape($Title)
    $message = [System.Security.SecurityElement]::Escape($Message)
    $xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$message</text></binding></visual></toast>")
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%APP_ID%').Show($toast)
} catch { }
if ($Task) { & schtasks.exe /Delete /TN $Task /F | Out-Null }
""".replace('%APP_ID%', POWERSHELL_APP_ID)


def _quiet():
    return dict(creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def end_time(armed):
    started = datetime.fromisoformat(str(armed['started_at']).replace('Z', '+00:00'))
    if started.tzinfo is None:
        raise ValueError('the start time has no time zone')
    return started + timedelta(hours=float(armed['hours']))


def texts(plan):
    plan = plan if isinstance(plan, dict) else {}
    hero = (plan.get('label_hero') or (plan.get('character') or {}).get('name') or 'Your hero').replace('"', "'")
    region = (plan.get('label_region') or ((plan.get('zones') or [{}])[0].get('room')) or 'its region').replace('"', "'")
    if plan.get('mode') == 'siege':
        return ('AFK FARM: siege report ready',
                f"{hero}'s siege of {region} is over. Open AFK FARM to see how many waves were held, and claim with {hero} in {region}.")
    return ('AFK FARM: expedition ready',
            f"{hero}'s expedition in {region} is ready. Open AFK FARM and claim with {hero} in {region}.")


def task_name(key):
    return TASK_PREFIX + key


def task_xml(when, script, title, message, task=TASK):
    """Task Scheduler XML: one run at ``when`` (local time), catch up after sleep."""
    local = when.astimezone().replace(tzinfo=None, microsecond=0)
    arguments = (f'--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script}" '
                 f'-Title "{title}" -Message "{message}" -Task "{task}"')
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AFK FARM: tells you once when your expedition is ready to claim, then removes itself.</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{local.isoformat()}</StartBoundary>
      <EndBoundary>{(local + timedelta(days=7)).isoformat()}</EndBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <DeleteExpiredTaskAfter>PT1H</DeleteExpiredTaskAfter>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>conhost.exe</Command><Arguments>{escape(arguments, {'"': '&quot;'})}</Arguments></Exec></Actions>
</Task>
"""


def schedule(data, when, title, message, run=subprocess.run, task=TASK):
    data = Path(data)
    script = data / SCRIPT
    script.write_text(SCRIPT_TEXT, encoding='utf-8-sig')
    xml = data / 'notify-ready.xml'
    xml.write_text(task_xml(when, script, title, message, task), encoding='utf-16')
    try:
        result = run(['schtasks', '/Create', '/TN', task, '/XML', str(xml), '/F'], capture_output=True, text=True, **_quiet())
    finally:
        xml.unlink(missing_ok=True)
    return result.returncode == 0, (result.stderr or result.stdout or '').strip()


def remove(run=subprocess.run, task=TASK):
    result = run(['schtasks', '/Delete', '/TN', task, '/F'], capture_output=True, text=True, **_quiet())
    return result.returncode == 0


def entry(key, when, title, message):
    """One notification to schedule: a task key, its time and its texts."""
    return dict(key=key, at=when, title=title, message=message)


def expedition_entry(armed, plan):
    """The ready notification of an armed expedition, or None if its time is unreadable."""
    try:
        end = end_time(armed)
    except (KeyError, TypeError, ValueError):
        return None
    title, message = texts(plan)
    return entry('exp-' + str(armed.get('expedition_id')), end, title, message)


def sync_all(data, entries, enabled, now=None, run=subprocess.run):
    """Keep one task per future entry, in step with the roster and the setting.

    ``entries`` come from ``expedition_entry`` (and worker trips). Returns the
    record: ``tasks`` maps each scheduled key to its time; ``errors`` why Task
    Scheduler refused a key. A refused request is not retried for the same key
    and time. The single 0.6.x task is removed once.
    """
    if os.name != 'nt' or (run is subprocess.run and os.environ.get('AFK_NOTIFY_DISABLED')):
        return {}
    data = Path(data)
    path = data / RECORD
    record = _read(path)
    now = now or datetime.now(timezone.utc)
    want = {}
    for e in entries or []:
        if enabled and e and KEY.fullmatch(str(e.get('key') or '')) and isinstance(e.get('at'), datetime) and e['at'] > now:
            want[e['key']] = e
    tasks = record.get('tasks') if isinstance(record.get('tasks'), dict) else {}
    failed = record.get('failed') if isinstance(record.get('failed'), dict) else {}
    errors = record.get('errors') if isinstance(record.get('errors'), dict) else {}
    changed = False
    if record.get('scheduled'):              # a 0.6.x record: its single task goes
        remove(run, TASK); changed = True
    for key in [k for k in tasks if k not in want or tasks[k] != want[k]['at'].isoformat()]:
        remove(run, task_name(key)); tasks.pop(key); changed = True
    for key in [k for k in failed if k not in want]:
        failed.pop(key); errors.pop(key, None); changed = True
    for key, e in want.items():
        at = e['at'].isoformat()
        if tasks.get(key) == at or failed.get(key) == at:
            continue
        ok, detail = schedule(data, e['at'], e['title'], e['message'], run, task_name(key))
        changed = True
        if ok:
            tasks[key] = at; failed.pop(key, None); errors.pop(key, None)
        else:
            failed[key] = at; errors[key] = detail or 'Task Scheduler refused the notification task.'
    record = dict(schema=2, tasks=tasks, failed=failed, errors=errors)
    if changed or not path.exists():
        _write(path, record)
    return record


def sync(data, armed, enabled, plan=None, now=None, run=subprocess.run):
    """One armed expedition (0.6.x callers and tests): ``scheduled`` is its entry."""
    item = expedition_entry(armed, plan) if armed else None
    record = sync_all(data, [item] if item else [], enabled, now, run)
    if not record:
        return {}
    key = item['key'] if item else None
    at = record['tasks'].get(key) if key else None
    return dict(record, scheduled=dict(expedition_id=armed.get('expedition_id'), at=at) if at else None,
                error=record['errors'].get(key) if key else None)


def toast(data, title, message, run=subprocess.Popen):
    """Show one notification now (no task). Returns False where Windows has no toasts."""
    if os.name != 'nt' or (run is subprocess.Popen and os.environ.get('AFK_NOTIFY_DISABLED')):
        return False
    data = Path(data)
    script = data / SCRIPT
    script.write_text(SCRIPT_TEXT, encoding='utf-8-sig')
    safe = lambda text: str(text).replace('"', "'")
    try:
        run(['conhost.exe', '--headless', 'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(script), '-Title', safe(title), '-Message', safe(message), '-Task', ''], **_quiet())
        return True
    except OSError:
        return False
