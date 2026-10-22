"""Optimise OS "Free up space": ONE-WAY cleanups. Qt-free.

Never journaled and never reverted: caches can be rebuilt, but crash dumps,
WER reports and old logs are removed for good. They describe past failures. The
selection is built EXPLICITLY from the scanners in `GROUPS` -- never through
`stage_runners.run_cleanup_safe_stage`, whose "safe" sweep also takes shader
caches, Prefetch and the Recycle Bin. File deletions go through the Cleanup
module's `delete_items_report` (measured freed bytes, refusals, locked files,
sign-in-store and exclusion guards), which also records the run in
`cleanup_history` exactly as the Cleanup tabs do. Delivery Optimization uses
Windows' own cache-removal cmdlet; its result is reported separately.

Component store cleanup is `run_dism_stage` (DISM /StartComponentCleanup),
NEVER /ResetBase: ResetBase makes every installed update permanent (it can no
longer be uninstalled). DISM coordinates its own servicing lock and skips a
pending restart. Only the WU cache checks the Windows Update installer's IsBusy.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import stat
import subprocess
import time
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
    #: WU cache only: check IsBusy, then confirm wuauserv has stopped.
    servicing: bool = False
    default_checked: bool = False


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
    Group("app_leftovers", "This app's leftovers", ("scan_logviewer_leftovers",),
          "Old logviewer_cab_ extraction folders in TEMP, older than 1 hour; verified extraction output only."),
    Group("delivery", "Delivery Optimization cache", (),
          "Windows removes cached update delivery data through Delete-DeliveryOptimizationCache; policy refusals are reported.",
          default_checked=True),
    Group("thumbnails", "Thumbnail cache", ("scan_thumbnail_cache",),
          "Explorer rebuilds thumbnails. Icon cache is excluded."),
)
BY_ID = {g.id: g for g in GROUPS}
DISM_ID = "component_store"
SCANNERS = frozenset(name for g in GROUPS for name in g.scanners)
EXCLUDED = (
    ("Shader caches", "games stutter while they rebuild them"),
    ("Recycle Bin", "it is your undo for deleted files"),
    ("Restore points", "they are the way back from a bad update or driver"),
    ("Browser data", "caches, cookies and sign-ins belong to the browser's own settings"),
    ("Prefetch", "Windows uses it to speed up app starts"),
    ("Installer and driver stores", "Windows needs them for repair and rollback"),
    ("Previous Windows installations", "use Windows cleanup recommendations for Windows.old"),
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
    eligible: int | None = None

    @property
    def eligible_bytes(self) -> int:
        return self.size if self.eligible is None else self.eligible


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
    eligible: int | None = None

    @property
    def eligible_bytes(self) -> int:
        return 0 if self.error or not self.deletable else (self.size if self.eligible is None else self.eligible)

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
    kept_recent: int = 0
    kept_recent_bytes: int = 0
    cloud_only: int = 0
    locked_paths: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=list)
    protected: list[tuple[str, str]] = field(default_factory=list)
    locations: list[tuple[str, object, int]] = field(default_factory=list)
    delivery: dict | None = None

    @property
    def failed(self) -> bool:
        return bool(self.denied or _stage_failed(self.dism) or _stage_failed(self.delivery))

    def text(self) -> str:
        from core.formatting import format_size
        lines = [f"Measured: {format_size(self.freed)} freed ({self.files} files)."]
        if self.locked:
            lines.append(f"{self.locked} file(s) in use were left alone.")
        if self.denied:
            lines.append(f"{self.denied} item(s) could not be deleted (access denied).")
        for path, report, hours in self.locations:
            recent = getattr(report, "kept_recent", 0)
            protected = getattr(report, "protected", [])
            denied = [p for p in report.denied if _norm(p) not in {_norm(p) for p, _ in protected}]
            reasons = "; ".join(dict.fromkeys(why for _, why in protected))
            lines.append(f"{path}: Freed {format_size(report.freed)}; kept {recent} recent file(s)"
                         + (f" (newer than {hours} h)" if hours else "")
                         + (f" ({format_size(getattr(report, 'kept_recent_bytes', 0))})"
                            if getattr(report, "kept_recent_bytes", 0) else "")
                         + f"; {len(report.locked)} in use; {len(protected)} protected"
                         + (f" ({reasons})" if reasons else "")
                         + f"; {getattr(report, 'cloud_only', 0)} cloud-only; {len(denied)} access denied.")
            for kind, paths in (("In use", report.locked), ("Access denied", denied),
                                ("Protected", [f"{p} -- {why}" for p, why in protected])):
                lines += [f"  {kind}: {p}" for p in paths[:10]]
                if len(paths) > 10:
                    lines.append(f"  {kind}: (+{len(paths) - 10} more)")
        lines += [f"Refused: {path} -- {why}" for path, why in self.refused[:20]]
        if len(self.refused) > 20:
            lines.append(f"(+{len(self.refused) - 20} more refusals)")
        lines += [f"{label_of(key)}: {why}" for key, why in self.skipped.items()]
        if self.dism is not None:
            lines.append("Component store cleanup: " + _stage_text(self.dism))
        if self.delivery is not None:
            lines.append("Delivery Optimization cache: " + _stage_text(self.delivery))
        if self.cancelled:
            lines.append("Stopped before it finished; what had been deleted is counted above.")
        return "\n".join(lines)


def _stage_failed(stage) -> bool:
    return bool(stage and stage.get("status") != "SKIPPED" and
                (stage.get("error") or stage.get("status") == "FAILED" or
                 stage.get("returncode") not in (None, 0, 3010)))


def _stage_text(stage) -> str:
    if stage.get("status") == "SKIPPED":
        lead = "skipped: restart first" if stage.get("reboot_required") else "skipped"
    elif _stage_failed(stage):
        lead = "FAILED: " + (stage.get("error") or (f"exit {stage['returncode']}"
                            if stage.get("returncode") is not None else "operation failed"))
        if stage.get("reboot_required"):
            lead += "; restart required"
    else:
        lead = "completed" + ("; restart required" if stage.get("reboot_required") or stage.get("returncode") == 3010 else "")
    return lead + ("\n" + stage["output"] if stage.get("output") else "")


def label_of(key: str) -> str:
    return "Component store cleanup" if key == DISM_ID else BY_ID[key].label


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _dedupe(items: list) -> list:
    """Drop an item that IS, or lies inside, a directory item already kept
    (scan_minidump's folder and scan_memory_dumps' *.dmp in it)."""
    kept, roots, seen = [], [], set()
    for item in sorted(items, key=lambda i: (len(_norm(i.path)), _norm(i.path))):
        norm = _norm(item.path)
        if norm in seen or any(norm.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
            continue
        kept.append(item)
        seen.add(norm)
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
        if name == "scan_logviewer_leftovers":
            return scan_logviewer_leftovers()
        from modules.cleanup import cleanup_scanner
        from modules.cleanup.cleanup_scanner.scan_cache import cached_scan
        result = cached_scan(getattr(cleanup_scanner, name), min_age_days)
        if name == "scan_thumbnail_cache":
            result = dataclasses.replace(result, items=[i for i in result.items
                if os.path.basename(i.path).lower().startswith("thumbcache_")])
        return result

    def discover(self, name: str, min_age_days: int):
        """List targets without a size walk or caching incomplete measurements."""
        from modules.cleanup import cleanup_scanner
        from modules.cleanup.cleanup_scanner._common import directory_sizes_enabled
        token = directory_sizes_enabled.set(False)
        try:
            if name == "scan_logviewer_leftovers":
                return scan_logviewer_leftovers()
            if name == "scan_windows_logs":
                root = os.environ.get("SystemRoot", r"C:\Windows")
                for folder in (root, *(os.path.join(root, "Logs", d) for d in ("CBS", "DISM", "MoSetup"))):
                    try:
                        with os.scandir(folder):
                            pass
                    except FileNotFoundError:
                        logger.debug("optimise OS: %s is absent (nothing to scan there)", folder)
                        continue
            scanner = getattr(cleanup_scanner, name)
            # glob() silently converts an unreadable parent into no matches.
            # Check our catalog's wildcard parents before calling the scanner.
            spec = getattr(scanner, "spec", None)
            if spec is not None:
                from modules.cleanup.cleanup_scanner.catalog import expand
                for raw in spec.paths:
                    path = expand(raw)
                    if path and any(ch in path for ch in "*?["):
                        try:
                            with os.scandir(os.path.dirname(path)):
                                pass
                        except FileNotFoundError:
                            logger.debug("optimise OS: %s is absent (nothing to scan there)",
                                         os.path.dirname(path))
                            continue
            result = scanner(min_age_days=min_age_days)
            if name == "scan_thumbnail_cache":
                result.items = [i for i in result.items if os.path.basename(i.path).lower().startswith("thumbcache_")]
            return result
        finally:
            directory_sizes_enabled.reset(token)

    def delete(self, items: list, stop_wuauserv: bool, cancelled):
        from modules.cleanup.cleanup_scanner.scanners_system import delete_items_report
        return delete_items_report(items, stop_wuauserv=stop_wuauserv, cancelled=cancelled)

    def measure(self, item, cancelled=lambda: False) -> Location:
        """One combined, cancellable walk: present, eligible, newest and breakdown."""
        from modules.cleanup.cleanup_scanner.deleter import is_cloud_only, is_link_attrs
        where = Location(item.path, 0, item.is_dir, item.safety, item.keep_newer_than_hours, eligible=0)
        cutoff = time.time() - item.keep_newer_than_hours * 3600

        def measure_path(path, is_dir):
            total, eligible, newest = 0, 0, None
            stack = [path]
            while stack:
                if cancelled():
                    raise ScanCancelled()
                current = stack.pop()
                st = os.lstat(current)
                attrs = getattr(st, "st_file_attributes", 0)
                if stat.S_ISLNK(st.st_mode) or is_link_attrs(attrs) or is_cloud_only(attrs):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    with os.scandir(current) as entries:
                        for entry in entries:
                            if cancelled():
                                raise ScanCancelled()
                            stack.append(entry.path)
                else:
                    size = _file_size(current)
                    total += size
                    newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
                    if not item.keep_newer_than_hours or st.st_mtime <= cutoff:
                        eligible += size
            return Entry(path, total, is_dir, newest, eligible)

        try:
            if cancelled():
                raise ScanCancelled()
            st = os.lstat(item.path)
            attrs = getattr(st, "st_file_attributes", 0)
            if stat.S_ISLNK(st.st_mode) or is_link_attrs(attrs) or is_cloud_only(attrs):
                return where
            if not item.is_dir:
                entry = measure_path(item.path, False)
                where.size, where.eligible, where.newest = entry.size, entry.eligible, entry.newest
                return where
            kids = []
            with os.scandir(item.path) as entries:
                for entry in entries:
                    if cancelled():
                        raise ScanCancelled()
                    kids.append(measure_path(entry.path, entry.is_dir(follow_symlinks=False)))
            kids.sort(key=lambda e: (-e.size, e.path.lower()))
            where.size = sum(e.size for e in kids)
            where.eligible = sum(e.eligible_bytes for e in kids)
            stamps = [e.newest for e in kids if e.newest is not None]
            where.newest = max(stamps) if stamps else None
            where.children, where.rest = kids[:TOP_CHILDREN], kids[TOP_CHILDREN:]
        except OSError as error:
            logger.warning("optimise OS: cannot measure %s: %s", item.path, error)
            where.error = str(error)
        return where

    def servicing_busy(self) -> tuple[bool | None, str]:
        """(busy, why). None = could not tell, which callers treat as busy."""
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

    def delivery_size(self) -> int:
        return delivery_size()

    def delivery_clean(self) -> dict:
        output = _delivery_command("Delete-DeliveryOptimizationCache -Force -ErrorAction Stop")
        return {"returncode": 0, "output": output + "\nWindows managed the cache removal; freed bytes were not measured."}


class ScanCancelled(Exception):
    """A cancelled breakdown must never look like a completed empty scan."""


def _delivery_command(command: str) -> str:
    from core.system_paths import powershell_exe
    done = subprocess.run([powershell_exe(), "-NoProfile", "-NonInteractive", "-Command",
                           "$ErrorActionPreference='Stop'; " + command], capture_output=True,
                          text=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    if done.returncode:
        raise OSError(output or f"Delivery Optimization command exited {done.returncode}")
    return output


def delivery_size() -> int:
    """DO's locally cached bytes, rather than the full downloads' FileSize.

    Windows' DeliveryOptimizationStatus.psm1 exposes FileSizeInCache on its
    DOSwarmStats objects. This remains an estimate, never measured freed bytes.
    """
    output = _delivery_command("$n = (Get-DeliveryOptimizationStatus -ErrorAction Stop | "
                               "Measure-Object -Property FileSizeInCache -Sum).Sum; if ($null -eq $n) { 0 } else { [long]$n }")
    return int(output.strip())


def scan_logviewer_leftovers():
    """Conservative migration cleanup: flat, old CBS expand output only."""
    from modules.cleanup.cleanup_scanner._common import ScanItem, ScanResult
    from modules.log_viewer.archives import legacy_cab_output
    root = os.environ.get("TEMP", "")
    result = ScanResult()
    if not root:
        return result
    with os.scandir(root) as entries:
        for entry in entries:
            if legacy_cab_output(entry.path):
                result.items.append(ScanItem(entry.path, 0, True, keep_root=False, keep_newer_than_hours=1))
    return result


class SpaceCleaner:
    def __init__(self, backend=None) -> None:
        self.backend = backend if backend is not None else CleanupBackend()

    def _items(self, group: Group, discover=False) -> list:
        items = []
        for name in group.scanners:
            scan = getattr(self.backend, "discover", self.backend.scan) if discover else self.backend.scan
            result = scan(name, group.min_age_days)
            items.extend(dataclasses.replace(i, keep_newer_than_hours=max(i.keep_newer_than_hours, group.min_age_days * 24))
                         for i in result.items)
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

    def breakdown(self, cancelled=lambda: False) -> dict[str, list[Location] | str]:
        """Per group, the locations a clean would touch: each scan item and, for
        a folder, its largest top-level entries. Slow (walks every folder): call
        it on a worker. A group that cannot be scanned maps to its error text."""
        out: dict[str, list[Location] | str] = {}
        for group in GROUPS:
            if cancelled():
                raise ScanCancelled()
            try:
                if group.id == "delivery":
                    size = self.backend.delivery_size()
                    out[group.id] = ([Location("Delivery Optimization (Windows reported estimate)", size, False, eligible=size)]
                                     if size else [])
                    continue
                items = sorted(self._items(group, discover=True), key=lambda i: (-i.size, i.path.lower()))
                out[group.id] = sorted([self.backend.measure(item, cancelled) for item in items],
                                       key=lambda w: (-w.size, w.path.lower()))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
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
        if any(BY_ID[k].servicing for k in selected if k in BY_ID):
            servicing = self.backend.servicing_busy()
        plain, wu = [], []
        for group in GROUPS:
            if group.id not in selected:
                continue
            if cancelled():
                result.cancelled = True
                break
            if group.id == "delivery":
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
        # Deduplicate across groups and service batches. Give WU targets their
        # stopped-service batch even if a broader temp target contains one.
        all_items = _dedupe(plain + wu)
        wu_paths = {_norm(i.path) for i in wu}

        def needs_stop(item):
            root = _norm(item.path)
            return any(p == root or (item.is_dir and p.startswith(root.rstrip(os.sep) + os.sep)) for p in wu_paths)
        plain = [i for i in all_items if not needs_stop(i)]
        wu = [i for i in all_items if needs_stop(i)]
        for batch, stop in ((plain, False), (wu, True)):
            if not batch or cancelled():
                result.cancelled = result.cancelled or (bool(batch) and cancelled())
                continue
            report, refused, per_item = self.backend.delete(batch, stop, cancelled)
            result.freed += report.freed
            result.files += report.files
            result.locked += len(report.locked)
            protected = getattr(report, "protected", [])
            protected_paths = {_norm(p) for p, _ in protected}
            denied = [p for p in report.denied if _norm(p) not in protected_paths]
            result.denied += len(denied)
            result.denied_paths.extend(denied)
            result.locked_paths.extend(report.locked)
            result.protected.extend(protected)
            result.kept_recent += getattr(report, "kept_recent", 0)
            result.kept_recent_bytes += getattr(report, "kept_recent_bytes", 0)
            result.cloud_only += getattr(report, "cloud_only", 0)
            hours = {_norm(i.path): i.keep_newer_than_hours for i in batch}
            result.locations.extend((p, r, hours.get(_norm(p), 0)) for p, r in per_item)
            result.refused += list(refused)
            result.cancelled = result.cancelled or report.cancelled
        if DISM_ID in selected:
            if cancelled():
                result.cancelled = True
            else:
                try:
                    result.dism = self.backend.dism(log, cancelled)
                except (OSError, subprocess.SubprocessError) as error:
                    logger.warning("optimise OS: component cleanup failed: %s", error)
                    parts = [getattr(error, "stdout", "") or getattr(error, "output", ""),
                             getattr(error, "stderr", "")]
                    output = "\n".join(p.decode(errors="replace") if isinstance(p, bytes) else p for p in parts if p)
                    result.dism = {"status": "FAILED", "error": str(error), "output": output}
        if "delivery" in selected and not cancelled():
            try:
                result.delivery = self.backend.delivery_clean()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                logger.warning("optimise OS: delivery cleanup refused: %s", error)
                result.delivery = {"status": "FAILED", "error": str(error)}
        result.cancelled = result.cancelled or cancelled()
        return result
