"""ELEVATED real-machine round trip for the Audit & Logging writes.

For every writable Audit & Logging control: read the machine's raw state
directly (wevtutil gl / auditpol /get /r / the registry), then stage -> apply
-> read raw -> revert -> read raw through the REAL pipeline (ChangeSet's
revert probe, apply_batch, TweakEngine, BackupService, revert_control). It
passes only when the final raw state equals the starting one, byte for byte.

    .venv\\Scripts\\python.exe tools\\audit_logging_roundtrip.py [control_id ...]

Exits 1 on any mismatch, 2 when not elevated. It CHANGES the machine for a
few seconds per control and puts it back; run it only on a machine you own.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import winreg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.backup_service import BackupService  # noqa: E402
from modules.security_dashboard import audit_policy  # noqa: E402
from modules.security_dashboard.applier import apply_batch  # noqa: E402
from modules.security_dashboard.catalog import load_catalog  # noqa: E402
from modules.security_dashboard.reverting import revert_control  # noqa: E402
from modules.security_dashboard.staging import ChangeSet  # noqa: E402
from modules.tweaks.tweak_engine import TweakEngine  # noqa: E402

_LOGS = {
    "security_log_size": "Security",
    "system_log_size": "System",
    "application_log_size": "Application",
    "powershell_log_size": "Microsoft-Windows-PowerShell/Operational",
    "taskscheduler_log_enabled": "Microsoft-Windows-TaskScheduler/Operational",
}
_CMDLINE = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit",
            "ProcessCreationIncludeCmdLine_Enabled")


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)


def _raw_log(name):
    out = _run(["wevtutil", "gl", name]).stdout
    fields = {}
    for line in out.splitlines():
        key, _, value = line.strip().partition(":")
        if key in ("enabled", "maxSize", "retention", "autoBackup"):
            fields[key] = value.strip()
    return fields


def _raw_audit(sub):
    proc = _run(["auditpol", "/get", f"/subcategory:{sub.guid}", "/r"])
    lines = [l for l in proc.stdout.splitlines() if sub.guid.lower() in l.lower()]
    return (proc.returncode, lines[0].split(",")[4] if lines else proc.stdout.strip())


def _raw_cmdline():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CMDLINE[0]) as key:
            return winreg.QueryValueEx(key, _CMDLINE[1])
    except FileNotFoundError:
        return "absent"


def raw_state(cid):
    if cid in _LOGS:
        return _raw_log(_LOGS[cid])
    if cid == "process_cmdline_auditing":
        return _raw_cmdline()
    for sub in audit_policy.SUBCATEGORIES:
        if cid == "audit_" + sub.name.lower().replace(" ", "_"):
            return _raw_audit(sub)
    raise KeyError(cid)


def round_trip(control, backup, engine):
    before = raw_state(control.id)
    changes = ChangeSet()
    changes.add(control, True)
    if not changes.changes:
        print(f"SKIP {control.id}: already in place, nothing to stage")
        print(f"     current {before}")
        return True
    (change,) = changes.changes
    applied = apply_batch(changes, engine, backup, invalidate_reads=lambda: None)
    during = raw_state(control.id)
    reverted = revert_control(control.id, backup, {control.id: control},
                              expected=change.from_value,
                              invalidate_reads=lambda: None)
    after = raw_state(control.id)
    ok = before == after
    print(f"{'OK  ' if ok else 'FAIL'} {control.id}")
    print(f"     before  {before}")
    print(f"     applied {during}  [{applied.results[0].state.name}]"
          f"  token={change.revert_token!r} note={change.revert_note!r}")
    print(f"     after   {after}  [{reverted.state.name}]")
    return ok


def main(argv):
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("needs an elevated prompt")
        return 2
    catalog = load_catalog()
    ids = argv or [c.id for c in catalog.values()
                   if c.category.value == "Audit & Logging" and not c.read_only_reason]
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        backup = BackupService(tmp)
        try:
            engine = TweakEngine(backup)
            for cid in ids:
                failures += not round_trip(catalog[cid], backup, engine)
        finally:
            backup.close()
    print(f"\n{len(ids) - failures}/{len(ids)} returned exactly to where they started")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
