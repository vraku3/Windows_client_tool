"""Optimise OS "Free up space": ONE-WAY cleanups. Qt-free.

Never journaled and never reverted: caches can be rebuilt, but crash dumps,
WER reports and old logs are removed for good. They describe past failures. The
selection is built EXPLICITLY from the scanners in `GROUPS` -- never through
`stage_runners.run_cleanup_safe_stage`, whose "safe" sweep also takes shader
caches, Prefetch and the Recycle Bin. Every deletion goes through the Cleanup
module's `delete_items_report` (measured freed bytes, refusals, locked files,
sign-in-store and exclusion guards), which also records the run in
`cleanup_history` exactly as the Cleanup tabs do.

Component store cleanup is `run_dism_stage` (DISM /StartComponentCleanup),
NEVER /ResetBase: ResetBase makes every installed update permanent (it can no
longer be uninstalled). Both it and the Windows Update cache are refused while
Windows servicing is busy (TrustedInstaller running, or the Windows Update
installer reporting busy), and refused when that cannot be read.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ONE_WAY_NOTE = ("One-way: Windows and apps can rebuild caches. Crash dumps, WER reports and old logs "
                "are removed for good; they are useful only for troubleshooting a past crash. "
                "Revert does not bring them back. "
                "Estimated before, measured after.")
DISM_NOTE = ("Runs DISM /StartComponentCleanup: removes superseded component versions; can take "
             "several minutes and holds the Windows servicing lock. Never /ResetBase -- that would "
             "make every installed update permanent (no longer uninstallable).")


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    scanners: tuple[str, ...]
    why: str
    min_age_days: int = 0
    #: Deleting under a running servicing stack races it: stop wuauserv, and
    #: refuse while servicing is busy.
    servicing: bool = False


GROUPS = (
    Group("temp", "Temporary files (yours and Windows')", ("scan_temp_files",),
          "%TEMP% and C:\\Windows\\Temp. Files younger than 24 hours are kept: installers read "
          "their extracted files back."),
    Group("wu_cache", "Windows Update download cache", ("scan_wu_cache",),
          "Already-installed update downloads. Windows Update is stopped for the deletion and "
          "started again; refused while an update is installing.", servicing=True),
    Group("wer", "Windows Error Reporting archives", ("scan_wer_reports",),
          "Crash reports already sent or queued, useful for troubleshooting a past crash. "
          "Removed for good; their details are no longer available afterwards."),
    Group("dumps", "Crash dumps", ("scan_user_crash_dumps", "scan_minidump", "scan_memory_dumps"),
          "App crash dumps, blue-screen minidumps and MEMORY.DMP. Needed only to debug a past "
          "crash; removed for good. Diagnose > Crash Dumps reads them, so look first if one matters."),
    Group("logs", "Windows logs older than 30 days", ("scan_windows_logs",),
          "CBS, DISM, setup and Windows Update logs older than 30 days, useful only to troubleshoot "
          "past failures. Removed for good.", min_age_days=30),
)
BY_ID = {g.id: g for g in GROUPS}
DISM_ID = "component_store"
SCANNERS = frozenset(name for g in GROUPS for name in g.scanners)
EXCLUDED = (
    ("Shader caches", "games stutter while they rebuild them"),
    ("Recycle Bin", "it is your undo for deleted files"),
    ("Restore points", "they are the way back from a bad update or driver"),
    ("Browser data", "caches, cookies and sign-ins belong to the browser's own settings"),
)


@dataclass
class Estimate:
    id: str
    size: int = 0
    count: int = 0
    error: str = ""


#: A directory location lists this many of its largest top-level entries; the
#: rest are one "N more" row.
TOP_CHILDREN = 15


@dataclass(frozen=True)
class Entry:
    """One top-level file or folder inside a scanned directory."""
    path: str
    size: int
    is_dir: bool
    newest: float | None = None        # newest modification time inside, epoch seconds


@dataclass
class Location:
    """One scan item, as the owner sees it before choosing."""
    path: str
    size: int
    is_dir: bool
    safety: str = "safe"
    keep_newer_than_hours: int = 0
    newest: float | None = None
    children: list[Entry] = field(default_factory=list)    # the largest TOP_CHILDREN
    rest: list[Entry] = field(default_factory=list)        # everything else, one row
    error: str = ""

    @property
    def deletable(self) -> bool:
        return self.safety != "danger"


@dataclass
class CleanResult:
    freed: int = 0
    files: int = 0
    locked: int = 0
    denied: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    dism: dict | None = None
    cancelled: bool = False

    def text(self) -> str:
        from core.formatting import format_size
        lines = [f"Measured: {format_size(self.freed)} freed ({self.files} files)."]
        if self.locked:
            lines.append(f"{self.locked} file(s) in use were left alone.")
        if self.denied:
            lines.append(f"{self.denied} item(s) could not be deleted (access denied).")
        lines += [f"Refused: {path} -- {why}" for path, why in self.refused[:20]]
        if len(self.refused) > 20:
            lines.append(f"(+{len(self.refused) - 20} more refusals)")
        lines += [f"{label_of(key)}: {why}" for key, why in self.skipped.items()]
        if self.dism is not None:
            code = self.dism.get("returncode")
            lines.append("Component store cleanup: " + ("completed." if code == 0 else
                                                         f"FAILED (exit {code})."))
        if self.cancelled:
            lines.append("Stopped before it finished; what had been deleted is counted above.")
        return "\n".join(lines)


def label_of(key: str) -> str:
    return "Component store cleanup" if key == DISM_ID else BY_ID[key].label


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def newest_mtime(path: str) -> float | None:
    """Newest modification time of any file under `path` (links not followed).
    Uses the find data scandir already holds -- no extra stat per file."""
    newest, stack = None, [path]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError as error:
            logger.debug("optimise OS: newest-file walk cannot list %s: %s", folder, error)
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                when = entry.stat(follow_symlinks=False).st_mtime
            except OSError as error:
                logger.debug("optimise OS: newest-file walk cannot read %s: %s", entry.path, error)
                continue
            newest = when if newest is None else max(newest, when)
    return newest


def _dedupe(items: list) -> list:
    """Drop an item that IS, or lies inside, a directory item already kept
    (scan_minidump's folder and scan_memory_dumps' *.dmp in it)."""
    kept, roots = [], []
    for item in sorted(items, key=lambda i: (len(os.path.normcase(i.path)), i.path)):
        norm = os.path.normcase(os.path.normpath(item.path))
        if any(norm == r or norm.startswith(r.rstrip("\\") + "\\") for r in roots):
            continue
        kept.append(item)
        if item.is_dir:
            roots.append(norm)
    return kept


def _file_size(path: str) -> int:
    """Bytes the file occupies on disk (sparse/compressed files count what they use)."""
    try:
        from modules.cleanup.cleanup_scanner.disk_size import on_disk_size_of
    except ImportError as error:
        logger.debug("optimise OS: on-disk size helper unavailable (%s); using the file size", error)
        return os.lstat(path).st_size
    return on_disk_size_of(path)


class CleanupBackend:
    """The real machine: the Cleanup module's scanners and deleter."""

    def scan(self, name: str, min_age_days: int):
        if name not in SCANNERS:
            raise ValueError(f"{name} is not one of the Free-up-space scanners")
        from modules.cleanup import cleanup_scanner
        from modules.cleanup.cleanup_scanner.scan_cache import cached_scan
        return cached_scan(getattr(cleanup_scanner, name), min_age_days)

    def delete(self, items: list, stop_wuauserv: bool, cancelled):
        from modules.cleanup.cleanup_scanner.scanners_system import delete_items_report
        return delete_items_report(items, stop_wuauserv=stop_wuauserv, cancelled=cancelled)

    def children(self, path: str) -> list[Entry]:
        """The top-level entries of `path`, each measured the way the deleter
        counts it (`get_dir_size`: on-disk bytes, links and cloud placeholders
        skipped). Raises OSError when `path` itself cannot be listed."""
        from modules.cleanup.cleanup_scanner._common import get_dir_size
        out = []
        for entry in list(os.scandir(path)):
            try:
                if entry.is_symlink() or entry.is_junction():
                    out.append(Entry(entry.path, 0, False, None))     # unlinked, never followed
                elif entry.is_dir(follow_symlinks=False):
                    out.append(Entry(entry.path, get_dir_size(entry.path), True, newest_mtime(entry.path)))
                else:
                    out.append(Entry(entry.path, _file_size(entry.path),
                                     False, entry.stat(follow_symlinks=False).st_mtime))
            except OSError as error:
                logger.debug("optimise OS: cannot measure %s: %s", entry.path, error)
        return out

    def file_newest(self, path: str) -> float | None:
        try:
            return os.lstat(path).st_mtime
        except OSError as error:
            logger.debug("optimise OS: cannot stat %s: %s", path, error)
            return None

    def servicing_busy(self) -> tuple[bool | None, str]:
        """(busy, why). None = could not tell, which callers treat as busy."""
        try:
            import win32service as svc
            manager = svc.OpenSCManager(None, None, svc.SC_MANAGER_CONNECT)
            try:
                handle = svc.OpenService(manager, "TrustedInstaller", svc.SERVICE_QUERY_STATUS)
                try:
                    state = svc.QueryServiceStatus(handle)[1]
                finally:
                    svc.CloseServiceHandle(handle)
            finally:
                svc.CloseServiceHandle(manager)
        except Exception as error:      # pywintypes.error is not an OSError
            logger.warning("optimise OS: TrustedInstaller unreadable: %s", error)
            return None, f"Windows Modules Installer state could not be read ({error})"
        if state != svc.SERVICE_STOPPED:
            return True, "Windows Modules Installer (TrustedInstaller) is running -- servicing is in progress"
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                busy = bool(win32com.client.Dispatch("Microsoft.Update.Installer").IsBusy)
            finally:
                pythoncom.CoUninitialize()
        except Exception as error:      # a COM failure is a com_error, not an OSError
            logger.warning("optimise OS: Windows Update installer unreadable: %s", error)
            return None, f"Windows Update's installer state could not be read ({error})"
        if busy:
            return True, "Windows Update is installing an update"
        return False, ""

    def dism(self, log, cancelled) -> dict:
        from modules.updates.stage_runners import run_dism_stage
        return run_dism_stage(None, log, cancelled)


class SpaceCleaner:
    def __init__(self, backend=None) -> None:
        self.backend = backend if backend is not None else CleanupBackend()

    def _items(self, group: Group) -> list:
        items = []
        for name in group.scanners:
            result = self.backend.scan(name, group.min_age_days)
            items.extend(result.items)
        return _dedupe(items)

    def estimate(self) -> list[Estimate]:
        """One measurement per group. A failed scan is an error, never 0 bytes."""
        out = []
        for group in GROUPS:
            try:
                items = [i for i in self._items(group) if i.safety != "danger"]
                out.append(Estimate(group.id, sum(i.size for i in items), len(items)))
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning("optimise OS: %s estimate failed: %s", group.id, error)
                out.append(Estimate(group.id, error=str(error)))
        return out

    def _location(self, item) -> Location:
        where = Location(item.path, item.size, item.is_dir, item.safety, item.keep_newer_than_hours)
        if not item.is_dir:
            where.newest = self.backend.file_newest(item.path)
            return where
        try:
            kids = sorted(self.backend.children(item.path), key=lambda e: (-e.size, e.path.lower()))
        except OSError as error:
            logger.warning("optimise OS: cannot list %s: %s", item.path, error)
            where.error = str(error)
            return where
        where.children, where.rest = kids[:TOP_CHILDREN], kids[TOP_CHILDREN:]
        stamps = [e.newest for e in kids if e.newest is not None]
        where.newest = max(stamps) if stamps else None
        return where

    def breakdown(self) -> dict[str, list[Location] | str]:
        """Per group, the locations a clean would touch: each scan item and, for
        a folder, its largest top-level entries. Slow (walks every folder): call
        it on a worker. A group that cannot be scanned maps to its error text."""
        out: dict[str, list[Location] | str] = {}
        for group in GROUPS:
            try:
                items = sorted(self._items(group), key=lambda i: (-i.size, i.path.lower()))
                out[group.id] = [self._location(item) for item in items]
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning("optimise OS: %s breakdown failed: %s", group.id, error)
                out[group.id] = str(error)
        return out

    @staticmethod
    def _pick(items: list, picks: dict, result: CleanResult) -> list:
        """Narrow a group's fresh scan to what the owner ticked. `picks` maps an
        item path to None (the whole item) or to [(child path, is_dir), ...]: a
        partly ticked folder becomes one item per ticked child, which the same
        deleter (same guards, same age floor) deletes; an unticked child is
        simply not handed over."""
        wanted = {_norm(path): sub for path, sub in picks.items()}
        out = []
        for item in items:
            key = _norm(item.path)
            if key not in wanted:
                continue
            sub = wanted[key]
            if sub is None:
                out.append(item)
                continue
            for child, is_dir in sub:
                if os.path.dirname(_norm(child)) != key:
                    result.refused.append((child, "not directly inside the location it was listed under"))
                    continue
                out.append(dataclasses.replace(item, path=child, size=0, is_dir=is_dir, keep_root=not is_dir))
        return out

    def clean(self, selected: set[str], log=lambda line: None, cancelled=lambda: False,
              choice: dict[str, dict] | None = None) -> CleanResult:
        """`choice` (optional) narrows each group to the locations the owner
        ticked: {group id: {item path: None | [(child path, is_dir), ...]}}.
        Without it a selected group is cleaned whole."""
        result = CleanResult()
        servicing = None
        if DISM_ID in selected or any(BY_ID[k].servicing for k in selected if k in BY_ID):
            servicing = self.backend.servicing_busy()
        plain, wu = [], []
        for group in GROUPS:
            if group.id not in selected:
                continue
            if group.servicing and servicing[0] is not False:
                result.skipped[group.id] = f"Refused: {servicing[1]}. Try again when it has finished."
                continue
            try:
                items = self._items(group)
            except (OSError, ValueError, RuntimeError) as error:
                result.skipped[group.id] = f"Could not be scanned -- left alone: {error}"
                continue
            if choice is not None:
                items = self._pick(items, choice.get(group.id) or {}, result)
            for item in items:
                if item.safety == "danger":
                    result.refused.append((item.path, "marked dangerous by its scanner -- not part of this cleanup"))
                    continue
                item.selected = True
                (wu if group.servicing else plain).append(item)
        for batch, stop in ((plain, False), (wu, True)):
            if not batch or cancelled():
                result.cancelled = result.cancelled or (bool(batch) and cancelled())
                continue
            report, refused, _per_item = self.backend.delete(batch, stop, cancelled)
            result.freed += report.freed
            result.files += report.files
            result.locked += len(report.locked)
            result.denied += len(report.denied)
            result.refused += list(refused)
            result.cancelled = result.cancelled or report.cancelled
        if DISM_ID in selected:
            if servicing[0] is not False:
                result.skipped[DISM_ID] = f"Refused: {servicing[1]}. Try again when it has finished."
            elif cancelled():
                result.cancelled = True
            else:
                result.dism = self.backend.dism(log, cancelled)
        return result
