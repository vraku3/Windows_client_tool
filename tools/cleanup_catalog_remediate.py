r"""One-off remediation of the Cleanup catalog (2026-10-10). Kept as the record
of every decision.

The audit (`tools/cleanup_catalog_audit.py`) found 289 entries marked "safe"
that pointed at an app's whole data folder, a sign-in store or user data --
the hosts file, Telegram's tdata, Signal's database, Zoom recordings, ShareX
screenshots, Notepad++ unsaved tabs, saved Wi-Fi, Outlook's .ost/.pst, VPN
keys, installed games. Rules applied here:

* a flagged path in REVIEWED_KEEP is a genuine cache / temp / log / crash
  dump and stays as it is;
* a flagged path in REVIEWED_CAUTION stays, and its entry becomes "caution"
  (a re-download or an irreversible-but-intended deletion, never "safe");
* every other flagged path is REMOVED from its entry;
* an entry left with no path is disabled, with the reason, so the knowledge
  that it is not junk is not lost and nobody re-adds it.

Every safety level is rewritten (see REVIEWED_KEEP's second half for why).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleanup_catalog_audit import DEFS, judge  # noqa: E402

REVIEWED_KEEP = {p.lower() for p in [
    # browser / mail caches with their engine's own names
    r"%APPDATA%\Mozilla\Firefox\Profiles\*\cache2", r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*\cache2",
    r"%APPDATA%\Mozilla\Firefox\Profiles\*\startupCache",
    r"%APPDATA%\Thunderbird\Profiles\*\cache2", r"%APPDATA%\Thunderbird\Profiles\*\startupCache",
    r"%LOCALAPPDATA%\Microsoft\Windows\INetCache",
    # build / shader / GPU caches
    r"%USERPROFILE%\.cache\clang", r"%USERPROFILE%\.cache\meson", r"%USERPROFILE%\.cache\wine",
    r"%LOCALAPPDATA%\FortniteGame\Saved\D3DCache", r"%APPDATA%\Ubisoft\Connect\shader-cache",
    r"%LOCALAPPDATA%\AMD\VulkanCache", r"%LOCALAPPDATA%\Intel\GraphicsCache", r"%LOCALAPPDATA%\comgr",
    r"%LOCALAPPDATA%\go-build", r"%APPDATA%\Sublime Text\Index",
    # crash dumps and logs
    r"%LOCALAPPDATA%\TslGame\Saved\CrashReportClient", r"%APPDATA%\Facepunch Studios\Rust\crash-reports",
    r"%APPDATA%\.minecraft\crash-reports", r"%APPDATA%\ExpressVPN Logs", r"%APPDATA%\ForkLogs",
    r"%windir%\System32\LogFiles\HTTPERR", r"%windir%\Logs\CBS",
    r"%windir%\Performance\WinSAT\*.xml", r"%windir%\Performance\WinSAT\Media.ets",
    # app caches that hold no sign-in
    r"%APPDATA%\CyberGhost\API Cache", r"%LOCALAPPDATA%\CyberGhost\APICache",
    r"%LOCALAPPDATA%\CyberGhost\BrowserCache", r"%APPDATA%\Telegram Desktop\emoji",
    r"%APPDATA%\ai.opencode.desktop\Shared Dictionary",
    r"%APPDATA%\Adobe\Common\Media Cache Files", r"%LOCALAPPDATA%\Adobe\Common\Media Cache Files",
    r"%APPDATA%\Adobe\Common\Peak Files", r"%LOCALAPPDATA%\Adobe\Common\Peak Files",
    r"%LOCALAPPDATA%\NVIDIA\GeForce Experience\UpdateTemp",
    r"%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db",
    r"%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache_*.db",
    r"%LOCALAPPDATA%\EmbyServer\transcoding", r"%LOCALAPPDATA%\Roblox\_downloads",
    r"%LOCALAPPDATA%\VEGASTemp", r"%TEMP%", r"%TEMP%\MediaFire", r"%TEMP%\ffmpeg",
    r"%ProgramData%\chocolatey\lib-bad",
]}

#: Kept, but the entry becomes "caution": the next build/install re-downloads,
#: or (Recycle Bin, video caches) it is a deliberate, irreversible clean.
REVIEWED_CAUTION = {p.lower() for p in [
    r"%USERPROFILE%\.m2\repository", r"%LOCALAPPDATA%\Maven\repository", r"%USERPROFILE%\.nuget\packages",
    r"%LOCALAPPDATA%\nuget\v3-cache", r"%USERPROFILE%\.cargo\registry\src",
    r"%APPDATA%\pip\wheels", r"%LOCALAPPDATA%\pip\wheels",
    r"%APPDATA%\pnpm-store", r"%LOCALAPPDATA%\pnpm-store", r"%USERPROFILE%\.pnpm-store",
    r"%USERPROFILE%\.terraform.d\plugin-cache", r"%USERPROFILE%\.packer.d\plugin-cache",
    r"%USERPROFILE%\.gradle\daemon", r"%USERPROFILE%\.gradle\wrapper",
    r"%APPDATA%\Autodesk\Revit\Autodesk Revit*\FamilyCache",
    r"%LOCALAPPDATA%\DaVinci Resolve\CacheClip", r"%LOCALAPPDATA%\DaVinci Resolve\Render Cache",
    r"%LOCALAPPDATA%\DaVinci Resolve\OptimizedMedia",
    r"%FIXED_DRIVES%\$Recycle.Bin",
]}

#: Reviewed in "caution"/"danger" entries: Windows logs, dumps, and caches
#: Windows rebuilds. Everything else flagged there was dropped -- the
#: Thorough preset selects caution and Aggressive selects danger, so a VM, a
#: database or WinSxS\Manifests under either is as live as one under "safe".
REVIEWED_KEEP |= {p.lower() for p in [
    r"%windir%\Logs\DISM", r"%windir%\Logs\MoSetup", r"%windir%\Logs\SIH",
    r"%windir%\Logs\WindowsUpdate", r"%windir%\Logs\WindowsServerBackup", r"%windir%\Panther",
    r"%LOCALAPPDATA%\Microsoft\Windows\Setup\Diag", r"%windir%\LiveKernelReports",
    r"%FIXED_DRIVES%\FOUND.*", r"%windir%\SoftwareDistribution\Backup", r"%windir%\Temp\PostReboot",
    r"%windir%\System32\FNTCACHE.DAT", r"%LOCALAPPDATA%\UnrealEngine\Engine\DerivedDataCache",
]}
REVIEWED = REVIEWED_KEEP | REVIEWED_CAUTION

REASON = ("Disabled 2026-10-10: every path pointed at the app's whole data folder, a sign-in "
          "store or user data (settings, accounts, saves, recordings, unsynced files), not a "
          "cache. Deleting it signs you out or loses data. Add only genuine cache subfolders.")


def main() -> int:
    removed = kept = disabled = cautioned = 0
    for f in sorted(DEFS.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        changed = False
        for spec in data["scanners"]:
            if spec.get("disabled_reason"):
                continue
            new_paths, make_caution = [], False
            for p in spec["paths"]:
                key = p.lower()
                if not judge(p) or key in REVIEWED_KEEP:
                    new_paths.append(p)
                    kept += 1
                elif key in REVIEWED_CAUTION:
                    new_paths.append(p)
                    make_caution = True
                else:
                    removed += 1
            if new_paths == spec["paths"] and not make_caution:
                continue
            changed = True
            if not new_paths:
                spec["disabled_reason"] = REASON
                disabled += 1
            else:
                spec["paths"] = new_paths
            if make_caution and spec["safety"] == "safe":
                spec["safety"] = "caution"
                cautioned += 1
        if changed:
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"paths removed {removed}, entries disabled {disabled}, entries now caution {cautioned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
