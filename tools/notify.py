"""Windows notification when an expedition is ready, even while AFK FARM is closed.

The panel keeps one Task Scheduler task, "AFK FARM\\Expedition ready", in step
with the armed expedition and the player's setting: it runs once at the
expedition's end time, shows a Windows notification through Windows PowerShell
(hidden, via ``conhost --headless``) and deletes itself. Claiming, cancelling or
switching the setting off removes it. Nothing else is scheduled, and nothing
runs with elevated rights.
"""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

TASK = 'AFK FARM\\Expedition ready'
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
    return ('AFK FARM: expedition ready',
            f"{hero}'s expedition in {region} is ready. Open AFK FARM and claim with {hero} in {region}.")


def task_xml(when, script, title, message):
    """Task Scheduler XML: one run at ``when`` (local time), catch up after sleep."""
    local = when.astimezone().replace(tzinfo=None, microsecond=0)
    arguments = (f'--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script}" '
                 f'-Title "{title}" -Message "{message}" -Task "{TASK}"')
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


def schedule(data, when, title, message, run=subprocess.run):
    data = Path(data)
    script = data / SCRIPT
    script.write_text(SCRIPT_TEXT, encoding='utf-8-sig')
    xml = data / 'notify-ready.xml'
    xml.write_text(task_xml(when, script, title, message), encoding='utf-16')
    try:
        result = run(['schtasks', '/Create', '/TN', TASK, '/XML', str(xml), '/F'], capture_output=True, text=True, **_quiet())
    finally:
        xml.unlink(missing_ok=True)
    return result.returncode == 0, (result.stderr or result.stdout or '').strip()


def remove(run=subprocess.run):
    result = run(['schtasks', '/Delete', '/TN', TASK, '/F'], capture_output=True, text=True, **_quiet())
    return result.returncode == 0


def sync(data, armed, enabled, plan=None, now=None, run=subprocess.run):
    """Keep the task in step with the armed expedition and the setting.

    Returns the record: ``scheduled`` names the expedition and end time the
    task covers, ``error`` why Task Scheduler refused it. A refused request is
    not retried for the same expedition and time.
    """
    if os.name != 'nt':
        return {}
    data = Path(data)
    path = data / RECORD
    record = _read(path)
    want = None
    if enabled and armed:
        try:
            end = end_time(armed)
        except (KeyError, TypeError, ValueError):
            end = None
        if end and end > (now or datetime.now(timezone.utc)):
            want = dict(expedition_id=armed.get('expedition_id'), at=end.isoformat())
    have = record.get('scheduled')
    if want == have or (want and record.get('failed_for') == want):
        return dict(dict(schema=1, scheduled=None), **record)
    if have:
        remove(run)
    record = dict(schema=1, scheduled=None)
    if want:
        title, message = texts(plan)
        ok, detail = schedule(data, end, title, message, run)
        if ok:
            record['scheduled'] = want
        else:
            record.update(failed_for=want, error=detail or 'Task Scheduler refused the notification task.')
    _write(path, record)
    return record
