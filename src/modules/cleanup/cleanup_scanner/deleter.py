# src/modules/cleanup/cleanup_scanner/deleter.py
r"""Delete what a ScanItem names -- and report what REALLY went.

`shutil.rmtree(ignore_errors=True)` was the old engine: it removed the
folder root along with the contents, followed nothing but reported
nothing, and every file it could not delete (in use, access denied) was
still counted as freed by the caller (2026-10-11). This walks the tree
itself so each outcome is measured:

* a directory item's CONTENTS go and the root stays (`keep_root`) --
  `%TEMP%`, `C:\Windows\Temp` and app cache roots are folders Windows and
  the apps expect to exist, with ACLs Windows set;
* a junction or symlink is unlinked and never followed: its target is
  someone else's data and is neither deleted nor counted;
* a file in use (ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION) is left
  and listed in `locked`; a refusal is listed in `denied`;
* read-only files are cleared and deleted; paths over MAX_PATH go through
  the `\\?\` prefix;
* files newer than `keep_newer_than_s` are kept -- an installer
  extracting into %TEMP% reads its files back minutes later.

Qt-free.
"""
import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Callable, List

logger = logging.getLogger(__name__)

ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
_IN_USE = (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION)


@dataclass
class DeleteReport:
    freed: int = 0
    files: int = 0
    locked: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    kept_recent: int = 0
    cancelled: bool = False

    def merge(self, other: "DeleteReport") -> None:
        self.freed += other.freed
        self.files += other.files
        self.locked.extend(other.locked)
        self.denied.extend(other.denied)
        self.kept_recent += other.kept_recent
        self.cancelled = self.cancelled or other.cancelled


def long_path(path: str) -> str:
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _short(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def _remove_file(path: str, size: int, report: DeleteReport) -> None:
    for attempt in (1, 2):
        try:
            os.remove(path)
            report.freed += size
            report.files += 1
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) in _IN_USE:
                report.locked.append(_short(path))
                return
            if attempt == 1:
                try:
                    os.chmod(path, stat.S_IWRITE)      # read-only attribute
                    continue
                except OSError:
                    logger.debug("cannot clear read-only on %s", path, exc_info=True)
            logger.warning("cannot delete %s: %s", _short(path), exc)
            report.denied.append(_short(path))
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) in _IN_USE:
                report.locked.append(_short(path))
            else:
                logger.warning("cannot delete %s: %s", _short(path), exc)
                report.denied.append(_short(path))
            return


def _unlink_link(entry: os.DirEntry, report: DeleteReport) -> None:
    try:
        if entry.is_junction() or entry.is_dir(follow_symlinks=False):
            os.rmdir(entry.path)                     # removes the link, not the target
        else:
            os.remove(entry.path)
    except OSError as exc:
        logger.warning("cannot remove link %s: %s", _short(entry.path), exc)
        report.denied.append(_short(entry.path))


def _clear(folder: str, floor: float, now: float, cancelled, report: DeleteReport) -> None:
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("cannot list %s: %s", _short(folder), exc)
        report.denied.append(_short(folder))
        return
    for entry in entries:
        if cancelled():
            report.cancelled = True
            return
        if entry.is_symlink() or entry.is_junction():
            _unlink_link(entry, report)
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            logger.warning("cannot read %s: %s", _short(entry.path), exc)
            report.denied.append(_short(entry.path))
            continue
        if entry.is_dir(follow_symlinks=False):
            _clear(entry.path, floor, now, cancelled, report)
            if report.cancelled:
                return
            try:
                os.rmdir(entry.path)                 # only succeeds once empty
            except OSError:
                logger.debug("left %s: not empty (contents already recorded)", _short(entry.path))
            continue
        if floor and now - st.st_mtime < floor:
            report.kept_recent += 1
            continue
        _remove_file(entry.path, st.st_size, report)


def _unlink_root_junction(target: str, report: DeleteReport) -> None:
    try:
        os.rmdir(target)                             # removes the junction, not the target
    except OSError as exc:
        logger.warning("cannot remove junction %s: %s", _short(target), exc)
        report.denied.append(_short(target))


def delete_path(path: str, is_dir: bool, *, keep_root: bool = True,
                keep_newer_than_s: float = 0,
                cancelled: Callable[[], bool] = lambda: False) -> DeleteReport:
    import time
    report = DeleteReport()
    target = long_path(path)
    if not os.path.lexists(target):
        return report
    if os.path.isjunction(target):
        _unlink_root_junction(target, report)
        return report
    if not is_dir or os.path.islink(target):
        try:
            size = os.lstat(target).st_size
        except OSError:
            size = 0
        _remove_file(target, size, report)
        return report
    _clear(target, keep_newer_than_s, time.time(), cancelled, report)
    if not keep_root and not report.cancelled:
        try:
            os.rmdir(target)
        except OSError:
            logger.debug("left %s: not empty (contents already recorded)", _short(target))
    return report
