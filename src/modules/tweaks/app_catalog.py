# src/modules/tweaks/app_catalog.py
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse

from core.windows_utils import ps_quote
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Windows records every AppX deployment attempt and its outcome here, and the
# log is readable unelevated. It is the only thing that can tell "we failed to
# remove it" apart from "we removed it and something put it back" -- a
# before/after snapshot of Get-AppxPackage cannot.
APPX_DEPLOYMENT_LOG = "Microsoft-Windows-AppXDeploymentServer/Operational"

# AppX packages that must never appear in a removal queue
PROTECTED_APPS_DEFAULT = {
    "Microsoft.OneDriveSync",
    "Microsoft.Office.OneNote",
    "Microsoft.WindowsStore",
    "Microsoft.Windows.Photos",
}


@dataclass(frozen=True)
class WingetApp:
    """One row of `winget list` -- an app that can be uninstalled by id."""

    name: str
    app_id: str
    version: str
    source: str

    @property
    def is_appx(self) -> bool:
        """A `MSIX\\...` id is an AppX package under another name."""
        return self.app_id.startswith("MSIX\\")

    @property
    def is_pseudo_id(self) -> bool:
        """True for winget's internal handles for things it did not install."""
        return self.is_appx or self.app_id.startswith("ARP\\")

    @property
    def display(self) -> str:
        version = f"  {self.version}" if self.version else ""
        origin = "winget" if self.source else "installed program"
        return f"{self.name}{version}   [{origin}]"


class AppCatalog:
    """Manages the installable app catalog and installed-app detection.

    Catalog source: definitions/app_catalog.json (winget-installable apps).
    Installed detection: `winget list` output + PowerShell Get-AppxPackage.
    """

    def __init__(self, catalog_path: Optional[str] = None):
        if catalog_path is None:
            catalog_path = os.path.join(
                os.path.dirname(__file__), "definitions", "app_catalog.json"
            )
        with open(catalog_path, encoding="utf-8") as f:
            self.entries: List[Dict] = json.load(f)

    def categories(self) -> List[str]:
        """Return sorted unique category names."""
        return sorted({e["category"] for e in self.entries})

    def filter_by_category(self, category: str) -> List[Dict]:
        if category == "All":
            return self.entries
        return [e for e in self.entries if e["category"] == category]

    # ------------------------------------------------------------------
    # Detection helpers (called from worker threads)
    # ------------------------------------------------------------------

    def _winget_list_output(self,
                            on_output: Optional[callable] = None
                            ) -> Optional[str]:
        """`winget list`'s text, or None when the question was not answered.

        None and "no apps" are different answers, and the difference decides
        whether a removal can be called verified.
        """
        try:
            result = subprocess.run(
                ["winget", "list", "--accept-source-agreements",
                 "--disable-interactivity"],
                capture_output=True, text=True, timeout=60, check=False,
                encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.warning("winget list failed: %s", e)
            if on_output:
                on_output(f"could not read the installed app list: {e}")
            return None
        complaint = (result.stderr or "").strip()
        if result.returncode != 0 or not (result.stdout or "").strip():
            if on_output:
                on_output(f"could not read the installed app list "
                          f"(exit {result.returncode}): "
                          f"{complaint or 'no output'}")
            return None
        return result.stdout

    def detect_installed_winget(self) -> Set[str]:
        """Run `winget list` and return set of installed winget IDs."""
        output = self._winget_list_output()
        return self._parse_winget_list(output) if output else set()

    def detect_installed_desktop(self) -> List["WingetApp"]:
        """Installed Win32/desktop apps, as rows that can be uninstalled."""
        output = self._winget_list_output()
        return self.desktop_apps_from(output) if output else []

    def parse_winget_rows(self, output: str) -> List["WingetApp"]:
        """`winget list` output → rows, read by COLUMN.

        The columns are fixed-width and the header gives their offsets. They
        have to be: 24 of the 139 rows on this machine carry an id like
        `ARP\\Machine\\X64\\AMD Catalyst Install Manager`, spaces and all, and
        names carry dots of their own ("7-Zip 26.02 (x64 edition)",
        "Node.js"). Splitting on whitespace and taking the first dotted token
        returned 66 junk ids out of 126 here -- '.NET', 'Drv_3.00.0045', and
        bare version numbers -- and those ids are what marks a catalog entry
        as already installed.
        """
        lines = output.splitlines()
        header_index = -1
        for index, line in enumerate(lines):
            if "Name" in line and "Id" in line and "Version" in line:
                header_index = index
                break
        if header_index < 0:
            return []

        header = lines[header_index]
        name_at = header.index("Name")
        id_at = header.index("Id")
        version_at = header.index("Version")
        # "Available" is only there when something has an update pending.
        after_version = min(
            [at for at in (header.find("Available"), header.find("Source"))
             if at > version_at] or [len(header) + 4096]
        )
        source_at = header.find("Source")

        rows: List[WingetApp] = []
        for line in lines[header_index + 1:]:
            if not line.strip() or set(line.strip()) <= {"-", "="}:
                continue
            if len(line) <= id_at:
                continue        # trailing prose, e.g. "139 packages found"
            app_id = line[id_at:version_at].strip()
            if not app_id:
                continue
            rows.append(WingetApp(
                name=line[name_at:id_at].strip(),
                app_id=app_id,
                version=line[version_at:after_version].strip(),
                source=(line[source_at:].strip()
                        if source_at > 0 and len(line) > source_at else ""),
            ))
        return rows

    def desktop_apps_from(self, output: str) -> List["WingetApp"]:
        """The rows worth offering as removable desktop apps.

        `MSIX\\...` rows are AppX packages, which the tab already lists from
        Get-AppxPackage -- carrying them here too would offer two different
        removals of one thing.
        """
        return [row for row in self.parse_winget_rows(output)
                if not row.is_appx]

    def _parse_winget_list(self, output: str) -> Set[str]:
        """The set of real winget package IDs that are installed.

        `MSIX\\` and `ARP\\` ids are winget's internal handles for packages it
        did not install; they never match a catalog entry, and treating one as
        an id is how the catalog list came to mark the wrong things installed.
        """
        return {row.app_id for row in self.parse_winget_rows(output)
                if not row.is_pseudo_id}

    def detect_installed_appx(self) -> Set[str]:
        """Return set of installed AppX package family names via PowerShell."""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-AppxPackage | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=20, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return self._parse_appx_list(result.stdout)
        except Exception as e:
            logger.warning("Get-AppxPackage failed: %s", e)
            return set()

    def _parse_appx_list(self, output: str) -> Set[str]:
        return {line.strip() for line in output.splitlines() if line.strip()}

    def detect_installed_win32(self) -> Set[str]:
        """Return set of display names from registry Uninstall keys."""
        import winreg
        names: Set[str] = set()
        paths = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, path in paths:
            try:
                with winreg.OpenKey(hive, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub:
                                display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                if display_name:
                                    names.add(display_name)
                        except OSError:
                            # OSError here is how winreg says the enumeration is
                            # finished. Control flow, not a failure.
                            logger.debug("detect_installed_win32: registry enumeration finished", exc_info=True)
                            continue
            except OSError:
                # OSError here is how winreg says the enumeration is
                # finished. Control flow, not a failure.
                logger.debug("detect_installed_win32: registry enumeration finished", exc_info=True)
                continue
        return names

    # ------------------------------------------------------------------
    # Install / remove (called from worker threads)
    # ------------------------------------------------------------------

    def install_app(self, winget_id: str,
                    on_output: Optional[callable] = None,
                    is_cancelled: Optional[callable] = None) -> bool:
        """Run winget install. Streams output via on_output callback."""
        return self._run_winget(
            ["winget", "install", winget_id, "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
            on_output, is_cancelled,
        )

    def _winget_present(self, app_id: str,
                        on_output: Optional[callable] = None
                        ) -> Optional[bool]:
        """Is this app in winget's installed list? None = not answered."""
        output = self._winget_list_output(on_output)
        if output is None:
            return None
        return any(row.app_id == app_id
                   for row in self.parse_winget_rows(output))

    def remove_app_winget(self, app_id: str,
                          on_output: Optional[callable] = None,
                          is_cancelled: Optional[callable] = None) -> bool:
        """Uninstall a desktop app, and return whether it is actually gone.

        Same evidence rule as `remove_appx`: winget exiting 0 is not proof.
        It exits 0 on plenty of uninstalls that leave the app in place, and
        an unanswered question is never a success.
        """
        before = self._winget_present(app_id, on_output)
        if before is None:
            return False
        if not before:
            if on_output:
                on_output(f"{app_id} is not in the installed app list, so "
                          "there is nothing here to remove")
            return False

        # `--id ... --exact`, never a bare query: a positional query matches
        # id, name OR moniker, so it can hit several apps and refuse -- and an
        # ARP id contains spaces, which a query would split on.
        self._run_winget(
            ["winget", "uninstall", "--id", app_id, "--exact", "--silent",
             "--accept-source-agreements"],
            on_output, is_cancelled,
        )

        after = self._winget_present(app_id, on_output)
        if after is None:
            if on_output:
                on_output(f"could not confirm whether {app_id} was removed")
            return False
        if after:
            if on_output:
                on_output(f"{app_id} is still installed after the removal")
            return False
        return True

    def _appx_present(self, package_name: str,
                      on_output: Optional[callable] = None) -> Optional[bool]:
        """Is this package installed? None means the question was not answered.

        Asks for the WHOLE list and matches in Python, rather than
        `Get-AppxPackage '<name>'`. The unfiltered form is the one that
        populates the UI and is known to work in the app's own context; the
        filtered form returned nothing there while the full list contained the
        package, which is how a removal came to do nothing at all in silence.

        The return code and stderr are checked, because **empty output from a
        command that failed is not evidence of absence** — that rule is
        everywhere in this codebase, and reading empty stdout as "the package
        is gone" is exactly how a removal that never happened got reported as
        a success.
        """
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-AppxPackage | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=120,
        )
        complaint = (result.stderr or "").strip()
        if result.returncode != 0 or complaint:
            if on_output:
                on_output(f"could not read the installed package list "
                          f"(exit {result.returncode}): "
                          f"{complaint or 'no output'}")
            return None
        names = {line.strip() for line in (result.stdout or "").splitlines()
                 if line.strip()}
        if not names:
            if on_output:
                on_output("could not read the installed package list: it came "
                          "back empty, which no Windows install ever is")
            return None
        return package_name in names

    # ------------------------------------------------------------------
    # "It is still there" -- but did our removal fail, or did it work and
    # something reinstall it? Only the deployment log knows.
    # ------------------------------------------------------------------

    def _parse_readd_source(self, output: str,
                            package_name: str) -> Optional[str]:
        """Where a re-Add of this package came from, per the deployment log.

        `output` is one event per line as `<id><TAB><message>`. Returns the
        installer's path (or at least the package file it used), or None when
        the log shows no Add for this package -- and None must stay None: an
        Add for some other package, or our own Remove event, is not evidence
        that anything put this one back.
        """
        uris: Dict[str, str] = {}      # basename -> full Windows path
        source_file: Optional[str] = None

        for line in output.splitlines():
            _, _, message = line.partition("\t")
            if not message:
                continue
            for raw in re.findall(r"file:///\S+", message):
                path = self._file_uri_to_path(raw)
                if path:
                    uris[os.path.basename(path).lower()] = path
            if "Add operation" not in message or package_name not in message:
                continue
            match = re.search(r"from:\s*\(([^)]+)\)", message)
            if match:
                source_file = match.group(1).strip()

        if source_file is None:
            return None
        return uris.get(source_file.lower(), source_file)

    @staticmethod
    def _file_uri_to_path(uri: str) -> Optional[str]:
        """`file:///C:/a/JAM%20Software/x.msix.` -> `C:\\a\\JAM Software\\x.msix`.

        The log writes the URI inside a sentence, so the trailing full stop is
        part of the match and has to come off.
        """
        cleaned = urllib.parse.unquote(uri).rstrip(".")
        if not cleaned.lower().startswith("file:///"):
            return None
        return cleaned[len("file:///"):].replace("/", "\\")

    def _appx_readd_source(self, package_name: str,
                           seconds_back: float) -> Optional[str]:
        """Ask the deployment log whether this package was added back."""
        window = max(int(seconds_back) + 5, 10)
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            "Get-WinEvent -FilterHashtable @{LogName='"
            + APPX_DEPLOYMENT_LOG + "';"
            "StartTime=(Get-Date).AddSeconds(-" + str(window) + ");"
            "Id=400,854} | Sort-Object TimeCreated | ForEach-Object "
            r"{ [string]$_.Id + [char]9 + ($_.Message -replace '\s+',' ') }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=30, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.warning("could not read %s: %s", APPX_DEPLOYMENT_LOG, e)
            return None
        if result.returncode != 0 or not (result.stdout or "").strip():
            # No events, or the log could not be read. Either way we do not
            # know why the package is still there, and saying nothing beats
            # naming a culprit we did not see.
            return None
        return self._parse_readd_source(result.stdout, package_name)

    def remove_appx(self, package_name: str,
                    on_output: Optional[callable] = None) -> bool:
        """Remove a package, and return whether it is actually gone.

        True only on positive evidence: the package was listed before, and is
        not listed afterwards. Everything else is False with a reason --
        including "the list could not be read", because an unanswered question
        is not a success.
        """
        before = self._appx_present(package_name, on_output)
        if before is None:
            return False
        if not before:
            # `Get-AppxPackage 'X' | Remove-AppxPackage` with nothing matching
            # removes nothing, prints nothing and exits 0. Saying so beats
            # running it and reporting whatever silence comes back.
            if on_output:
                on_output(f"{package_name} is not visible as an installed "
                          "package, so there is nothing here to remove")
            return False

        cmd = f"Get-AppxPackage '{ps_quote(package_name)}' | Remove-AppxPackage"
        started = time.monotonic()
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=120,
        )
        if on_output:
            for line in (result.stdout + result.stderr).splitlines():
                on_output(line)

        after = self._appx_present(package_name, on_output)
        if after is None:
            if on_output:
                on_output(f"could not confirm whether {package_name} was "
                          "removed")
            return False
        if after:
            source = self._appx_readd_source(package_name,
                                             time.monotonic() - started)
            if on_output:
                if source:
                    on_output(f"{package_name} WAS removed successfully, and "
                              f"was then reinstalled from {source} -- "
                              "something on this machine puts it back, so "
                              "removing it here cannot make it stay away")
                else:
                    on_output(f"{package_name} is still installed after the "
                              "removal")
            return False
        return True

    def _run_winget(self, args: List[str],
                    on_output: Optional[callable],
                    is_cancelled: Optional[callable] = None) -> bool:
        """Run winget, streaming output. `is_cancelled` (if given) is polled
        by a side thread that kills the process tree if it fires -- without
        it, `Worker.cancel()` on the caller only stops the NEXT app in a
        batch (see tweaks_module.py's apply loop), not one already running.
        Same shape as `windows_features/features_module.py`'s
        `_run_dism_action` and `updates/winget_updater.py`'s `_run_winget`.
        """
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stop_watching = threading.Event()

            def _watch_for_cancel() -> None:
                while not stop_watching.wait(0.5):
                    if is_cancelled and is_cancelled():
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        return

            watcher = threading.Thread(target=_watch_for_cancel, daemon=True)
            watcher.start()
            try:
                for line in proc.stdout:
                    if on_output:
                        on_output(line.rstrip())
                proc.wait()
            finally:
                stop_watching.set()
            return proc.returncode == 0
        except Exception as e:
            logger.error("winget command failed: %s", e)
            if on_output:
                on_output(f"Error: {e}")
            return False
