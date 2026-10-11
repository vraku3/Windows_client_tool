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
ERROR_DIR_NOT_EMPTY = 145
_ATTR_DIRECTORY = 0x10
_ATTR_REPARSE_POINT = 0x400
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


def _is_link(st: os.stat_result) -> bool:
    """A symlink or any reparse point (junction, mount point...)."""
    return (stat.S_ISLNK(st.st_mode)
            or bool(getattr(st, "st_file_attributes", 0) & _ATTR_REPARSE_POINT))


def _unlink_link(path: str, report: DeleteReport) -> None:
    """Remove the link ITSELF. Never adds to `freed`: the target is not ours."""
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
        if attrs & _ATTR_DIRECTORY:
            os.rmdir(path)                           # dir symlink / junction, not the target
        else:
            os.remove(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("cannot remove link %s: %s", _short(path), exc)
        report.denied.append(_short(path))


def _rmdir(path: str, report: DeleteReport) -> None:
    try:
        os.rmdir(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        if getattr(exc, "winerror", None) == ERROR_DIR_NOT_EMPTY:
            logger.debug("left %s: not empty (contents already recorded)", _short(path))
            return
        logger.warning("cannot remove folder %s: %s", _short(path), exc)
        report.denied.append(_short(path))


def _skipped(entry: os.DirEntry, skip, is_dir: bool, report: DeleteReport) -> bool:
    """True (and listed as denied) when the caller's guard protects this entry."""
    if not skip(_short(entry.path), is_dir):
        return False
    logger.warning("not deleting %s: protected", _short(entry.path))
    report.denied.append(_short(entry.path))
    return True


def _descend(entry: os.DirEntry, floor: float, now: float, cancelled,
             report: DeleteReport, skip) -> None:
    try:
        fresh = os.lstat(entry.path)                 # may have been swapped since scandir
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("cannot read %s: %s", _short(entry.path), exc)
        report.denied.append(_short(entry.path))
        return
    if _is_link(fresh):
        _unlink_link(entry.path, report)
        return
    _clear(entry.path, floor, now, cancelled, report, skip)
    if not report.cancelled:
        _rmdir(entry.path, report)                   # only succeeds once empty


def _clear(folder: str, floor: float, now: float, cancelled, report: DeleteReport,
           skip=lambda path, is_dir: False) -> None:
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
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            logger.warning("cannot read %s: %s", _short(entry.path), exc)
            report.denied.append(_short(entry.path))
            continue
        link = entry.is_symlink() or entry.is_junction()
        if link:
            is_dir = bool(getattr(st, "st_file_attributes", 0) & _ATTR_DIRECTORY)
        else:
            is_dir = entry.is_dir(follow_symlinks=False)
        if _skipped(entry, skip, is_dir, report):      # links included, BEFORE any unlink
            continue
        if link:
            _unlink_link(entry.path, report)
            continue
        if is_dir:
            _descend(entry, floor, now, cancelled, report, skip)
            if report.cancelled:
                return
            continue
        if floor and now - st.st_mtime < floor:
            report.kept_recent += 1
            continue
        _remove_file(entry.path, st.st_size, report)


def delete_path(path: str, is_dir: bool, *, keep_root: bool = True,
                keep_newer_than_s: float = 0,
                cancelled: Callable[[], bool] = lambda: False,
                skip: Callable[[str, bool], bool] = lambda path, is_dir: False) -> DeleteReport:
    import time
    report = DeleteReport()
    if cancelled():
        report.cancelled = True
        return report
    target = long_path(path)
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return report
    except OSError as exc:
        logger.warning("cannot read %s: %s", _short(target), exc)
        report.denied.append(_short(target))
        return report
    # The root's type is what lstat says, never the caller's flag.
    if skip(_short(target), bool(getattr(st, "st_file_attributes", 0) & _ATTR_DIRECTORY) or stat.S_ISDIR(st.st_mode)):
        logger.warning("not deleting %s: protected", _short(target))
        report.denied.append(_short(target))
        return report
    if _is_link(st):
        _unlink_link(target, report)
        return report
    now = time.time()
    if not is_dir:
        if keep_newer_than_s and now - st.st_mtime < keep_newer_than_s:
            report.kept_recent += 1
        else:
            _remove_file(target, st.st_size, report)
        return report
    _clear(target, keep_newer_than_s, now, cancelled, report, skip)
    if not keep_root and not report.cancelled:
        _rmdir(target, report)
    return report
