r"""Add the caches found on the real machine by tools/junk_hunt.py (2026-10-10).

Each entry names the CACHE subfolders only, never the profile around them:
the profiles beside these hold the sign-ins (Steam's htmlcache\Default keeps
cookies; Battle.net's BrowserCaches\<id> has Local Storage, Network and
Session Storage next to its Cache). Idempotent: an id already present is
left alone.
"""
import json
from pathlib import Path

DEFS = Path(__file__).resolve().parent.parent / "src" / "modules" / "cleanup" / "cleanup_scanner" / "definitions"

CHROMIUM = (r"%LOCALAPPDATA%\Google\Chrome\User Data", r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data")

NEW = {
    "browsers": [
        dict(id="chromium_service_worker_scripts",
             label="Chrome / Edge / Brave service-worker script cache",
             paths=[rf"{b}\*\Service Worker\ScriptCache" for b in CHROMIUM], safety="safe",
             description="Compiled service-worker scripts. Rebuilt on the next visit; no sign-in is kept here."),
        dict(id="chromium_service_worker_storage",
             label="Chrome / Edge / Brave offline site storage (Service Worker CacheStorage)",
             paths=[rf"{b}\*\Service Worker\CacheStorage" for b in CHROMIUM], safety="caution",
             description="What sites saved for offline use (2 GB in Chrome here). Sign-ins are NOT here "
                         "(those are cookies); a site works offline again after its next online visit."),
        dict(id="chrome_installer_caches",
             label="Chrome component and extension installer cache",
             paths=[r"%LOCALAPPDATA%\Google\Chrome\User Data\component_crx_cache",
                    r"%LOCALAPPDATA%\Google\Chrome\User Data\extensions_crx_cache"], safety="safe",
             description="Downloaded copies of components and extensions Chrome already installed."),
        dict(id="chrome_on_device_ai_model",
             label="Chrome on-device AI model (Gemini Nano)",
             paths=[r"%LOCALAPPDATA%\Google\Chrome\User Data\OptGuideOnDeviceModel"], safety="caution",
             description="~4 GB model behind Chrome's built-in AI features. Chrome downloads it again "
                         "the next time a feature needs it; nothing is signed out."),
    ],
    "games": [
        dict(id="steam_webhelper_caches",
             label="Steam browser caches (store and library pages)",
             paths=[r"%LOCALAPPDATA%\Steam\htmlcache\Default\Cache",
                    r"%LOCALAPPDATA%\Steam\htmlcache\Default\Code Cache",
                    r"%LOCALAPPDATA%\Steam\htmlcache\Default\GPUCache",
                    r"%LOCALAPPDATA%\Steam\htmlcache\GrShaderCache",
                    r"%LOCALAPPDATA%\Steam\htmlcache\ShaderCache",
                    r"%LOCALAPPDATA%\Steam\htmlcache\GraphiteDawnCache"], safety="safe",
             description="Only the cache folders: htmlcache\\Default also holds the store's cookies, "
                         "which this never touches."),
        dict(id="battlenet_local_caches",
             label="Battle.net app and browser caches",
             paths=[r"%LOCALAPPDATA%\Battle.net\Cache",
                    r"%LOCALAPPDATA%\Battle.net\BrowserCaches\*\Cache",
                    r"%LOCALAPPDATA%\Battle.net\BrowserCaches\*\Code Cache",
                    r"%LOCALAPPDATA%\Battle.net\BrowserCaches\*\DawnCache",
                    r"%LOCALAPPDATA%\Battle.net\BrowserCaches\*\GPUCache"], safety="safe",
             description="Only the caches; Local Storage, Network and Session Storage beside them "
                         "keep you signed in and are never touched."),
    ],
    "comms": [
        dict(id="whatsapp_store_webview_cache",
             label="WhatsApp (Store app) web view cache",
             paths=[r"%LOCALAPPDATA%\Packages\5319275A.WhatsAppDesktop_*\LocalCache\EBWebView\Default\Cache",
                    r"%LOCALAPPDATA%\Packages\5319275A.WhatsAppDesktop_*\LocalCache\EBWebView\Default\Code Cache",
                    r"%LOCALAPPDATA%\Packages\5319275A.WhatsAppDesktop_*\LocalCache\EBWebView\Default\GPUCache"],
             safety="safe",
             description="Only the caches. The sign-in and messages live in the same profile's "
                         "IndexedDB/Local Storage, which this never touches."),
        dict(id="teams_new_webview_cache",
             label="Microsoft Teams (new) web view cache and logs",
             paths=[r"%LOCALAPPDATA%\Packages\MSTeams_*\LocalCache\Microsoft\MSTeams\EBWebView\*\Cache",
                    r"%LOCALAPPDATA%\Packages\MSTeams_*\LocalCache\Microsoft\MSTeams\EBWebView\*\Code Cache",
                    r"%LOCALAPPDATA%\Packages\MSTeams_*\LocalCache\Microsoft\MSTeams\EBWebView\*\GPUCache",
                    r"%LOCALAPPDATA%\Packages\MSTeams_*\LocalCache\Microsoft\MSTeams\Logs"], safety="safe",
             description="Caches and logs only; Teams stays signed in."),
    ],
    "apps": [
        dict(id="outlook_new_webview_cache",
             label="Outlook (new) web view cache",
             paths=[r"%LOCALAPPDATA%\Microsoft\Olk\EBWebView\Default\Cache",
                    r"%LOCALAPPDATA%\Microsoft\Olk\EBWebView\Default\Code Cache",
                    r"%LOCALAPPDATA%\Microsoft\Olk\EBWebView\Default\GPUCache"], safety="safe",
             description="The new Outlook's page cache only. Mail, .ost and .pst files are never "
                         "touched (Cleanup refuses them outright)."),
        dict(id="m365_app_webview_cache",
             label="Microsoft 365 app web view cache",
             paths=[r"%LOCALAPPDATA%\Packages\Microsoft.MicrosoftOfficeHub_*\LocalState\EBWebView\Default\Cache",
                    r"%LOCALAPPDATA%\Packages\Microsoft.MicrosoftOfficeHub_*\LocalState\EBWebView\Default\Code Cache"],
             safety="safe", description="Page cache of the Microsoft 365 (Office hub) app; no sign-in."),
        dict(id="stremio_webview_cache",
             label="Stremio web view cache",
             paths=[r"%LOCALAPPDATA%\Programs\Stremio\stremio-shell-ng.exe.WebView2\EBWebView\Default\Cache",
                    r"%LOCALAPPDATA%\Programs\Stremio\stremio-shell-ng.exe.WebView2\EBWebView\Default\Code Cache"],
             safety="safe", description="Stremio's page cache only; add-ons and sign-in untouched."),
        dict(id="vscode_crashpad",
             label="VS Code crash reports",
             paths=[r"%APPDATA%\Code\Crashpad"], safety="safe",
             description="Crash dumps VS Code keeps after a crash."),
        dict(id="electron_updater_leftovers",
             label="Downloaded app-update installers (Electron apps)",
             paths=[r"%LOCALAPPDATA%\*-updater\pending", r"%LOCALAPPDATA%\*-updater\installer.exe"],
             safety="safe",
             description="Installers an app's updater already ran (CurseForge, OpenCode, ArdenWoW "
                         "launcher here). The updater downloads a fresh one when there is an update."),
    ],
    "system": [
        dict(id="update_orchestrator_logs",
             label="Windows Update orchestrator logs (USOShared)",
             paths=[r"%ProgramData%\USOShared\Logs"], safety="safe",
             description="Diagnostic trace logs of the update orchestrator. Needs administrator."),
    ],
    "dev": [
        dict(id="uv_cache",
             label="uv (Python) package cache",
             paths=[r"%LOCALAPPDATA%\uv\cache"], safety="caution",
             description="Downloaded Python packages. Environments keep working: uv hard-links their "
                         "files, so about half of this (5.8 of 10.8 GB here) stays in use by them and "
                         "is not freed. The next install re-downloads what it needs."),
    ],
}


def main() -> None:
    added = 0
    for category, entries in NEW.items():
        path = DEFS / f"{category}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        have = {s["id"] for s in data["scanners"]}
        for entry in entries:
            if entry["id"] not in have:
                data["scanners"].append(entry)
                added += 1
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"added {added} entries")


if __name__ == "__main__":
    main()
