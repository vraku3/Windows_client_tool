"""Real round trip of App Buster's actions that leaves the machine as it was.

    python tools/app_buster_roundtrip.py [--appx Microsoft.BingWeather]

1. Desktop cleanup: creates a FAKE leftover Uninstall entry under HKCU (its
   uninstaller and folder do not exist), checks the scan calls it Orphaned,
   cleans it up through `actions.clean_up` with the real Runner (reg export
   backup, RegDeleteTree), and checks it is gone and the backup exists.
2. Windows app: takes a package that is on the PC but NOT installed for this
   account ("Installable"), installs it for you, checks, removes it for you,
   checks -- ending where it started. Skipped if that package is not
   Installable here. Needs no elevation.
"""
import argparse
import os
import sys
import winreg

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from modules.app_buster.engine import actions as act  # noqa: E402
from modules.app_buster.engine import model as m, scan  # noqa: E402

KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\AppBusterRoundTripTest"
FULL_KEY = "HKCU" + chr(92) + KEY


def desktop(log) -> bool:
    gone = os.path.join(os.environ["TEMP"], "AppBusterRoundTrip-missing")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "App Buster round-trip test leftover")
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{os.path.join(gone, "unins000.exe")}"')
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, gone)
    rows = scan.scan().rows
    rec = next((r for r in rows if r.registry_key == "HKCU\\" + KEY), None)
    log(f"scan: {rec.type if rec else 'NOT FOUND'} -- {rec.reason if rec else ''}")
    if rec is None or rec.type != m.ORPHANED:
        return False
    before = set(os.listdir(act.backup_folder())) if os.path.isdir(act.backup_folder()) else set()
    out = act.clean_up(rec, act.Runner(), log)
    backups = set(os.listdir(act.backup_folder())) - before
    log(f"clean_up: {out.state} {out.reason}; key exists after: {act.key_exists(FULL_KEY)}; "
        f"new backup: {sorted(backups)}")
    return out.state == act.REMOVED and not act.key_exists("HKCU\\" + KEY) and bool(backups)


def windows_app(package: str, log) -> bool:
    runner = act.Runner()
    rows = scan.scan().rows
    rec = next((r for r in rows if r.package_name == package), None)
    if rec is None or rec.status != m.INSTALLABLE:
        log(f"{package}: not Installable here ({rec.status if rec else 'absent'}); skipped")
        return True
    log(f"{package}: Installable -- installing for this account")
    out = act.install_windows_app(rec, runner, log)
    log(f"install: {out.state} {out.reason}")
    if out.state != act.INSTALLED:
        return False
    installed = next(r for r in scan.scan().rows if r.family == rec.family and r.status == m.INSTALLED)
    log(f"now listed as {installed.status}, {installed.full_name}")
    out = act.remove_windows_app(installed, act.SCOPE_USER, runner, log)
    log(f"remove: {out.state} {out.reason}")
    after = next((r for r in scan.scan().rows if r.family == rec.family), None)
    log(f"after: {after.status if after else 'absent'}")
    return out.state == act.REMOVED and after is not None and after.status == m.INSTALLABLE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--appx", default="Microsoft.BingWeather")
    args = ap.parse_args()
    log = lambda line: print("  ", line, flush=True)      # noqa: E731
    try:
        ok1 = desktop(log)
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
        except OSError as e:                # already cleaned up -- the expected case
            print("   (test key already gone:", e, ")")
    print("DESKTOP CLEANUP", "OK" if ok1 else "FAILED")
    ok2 = windows_app(args.appx, log)
    print("WINDOWS APP INSTALL+REMOVE", "OK" if ok2 else "FAILED")
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
