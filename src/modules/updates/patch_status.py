"""Patch level from Windows Update's own history (Qt-free).

"When was this machine last patched" is not "when did Windows Update last
install something": Defender definitions install several times a day and
Store apps daily, so `AutoUpdate.Results.LastInstallationSuccessDate` read
2026-10-09 07:48 here while the last WINDOWS security update was 2026-09-14.
This module classifies the history so the answer is about the OS.

Measured 2026-10-09 on this machine (149 entries, read unelevated over COM):

- monthly OS updates are titled "2026-09 Security Update (KB5129195)
  (26200.9457)" and "2026-09 Preview Update (KB5124010) (26200.9550)" --
  the words "Cumulative Update" appear nowhere in them, so matching on that
  phrase (the old title style) finds nothing; the trailing (build.revision)
  is what identifies an OS update;
- a feature update is "Windows 11, version 26H2";
- a failure that later succeeded is not a problem: the AMD display driver
  failed 2026-09-23 and installed 2026-09-24. Only titles whose LATEST
  attempt failed are reported, and Store apps / definitions are left out.

ResultCode: 2 succeeded, 3 succeeded with errors, 4 failed, 5 aborted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SUCCEEDED = (2, 3)
FAILED = (4, 5)

_OS_BUILD = re.compile(r"\(\d{5}\.\d+\)\s*$")
_OLD_CUMULATIVE = re.compile(r"Cumulative Update for Windows", re.I)
_FEATURE = re.compile(r"^Windows 1[01],? version \w+", re.I)
_KB = re.compile(r"\bKB\d{6,8}\b")
_NOISE = re.compile(r"Security Intelligence Update|antimalware platform|^9[A-Z0-9]{11}-", re.I)

#: Monthly security updates ship on the second Tuesday. Past this many days
#: without one, the machine has missed a cycle.
STALE_SECURITY_DAYS = 45


@dataclass
class HistoryEntry:
    when: datetime            # timezone-aware
    title: str
    result_code: int
    hresult: int = 0


@dataclass
class PatchStatus:
    last_security: Optional[HistoryEntry] = None    # monthly OS security update
    last_os_update: Optional[HistoryEntry] = None   # security OR preview
    last_feature: Optional[HistoryEntry] = None
    unresolved_failures: List[HistoryEntry] = field(default_factory=list)
    entries_read: int = 0


def is_os_quality_update(title: str) -> bool:
    return bool(_OS_BUILD.search(title) or _OLD_CUMULATIVE.search(title))


def is_security_update(title: str) -> bool:
    """An OS quality update that is not a non-security preview."""
    return is_os_quality_update(title) and "preview" not in title.lower()


def kb_of(title: str) -> str:
    match = _KB.search(title)
    return match.group(0) if match else ""


def analyse(entries: Sequence[HistoryEntry], now: Optional[datetime] = None,
            failure_days: int = 30) -> PatchStatus:
    """Classify install history (any order) into what an admin asks."""
    now = now or datetime.now(timezone.utc)
    status = PatchStatus(entries_read=len(entries))
    latest_by_title: dict = {}
    for e in sorted(entries, key=lambda x: x.when):
        latest_by_title[e.title] = e
        if e.result_code not in SUCCEEDED:
            continue
        if is_security_update(e.title):
            status.last_security = e
        if is_os_quality_update(e.title):
            status.last_os_update = e
        if _FEATURE.search(e.title):
            status.last_feature = e
    cutoff = now - timedelta(days=failure_days)
    status.unresolved_failures = sorted(
        (e for e in latest_by_title.values()
         if e.result_code in FAILED and e.when >= cutoff and not _NOISE.search(e.title)),
        key=lambda x: x.when, reverse=True)
    return status


def read_history(limit: int = 1000) -> Tuple[Optional[List[HistoryEntry]], str]:
    """Install history over COM; (None, reason) when it cannot be read.

    Needs COM initialised on the calling thread -- run it on a COMWorker.
    """
    try:
        import win32com.client
        searcher = win32com.client.Dispatch("Microsoft.Update.Session").CreateUpdateSearcher()
        total = int(searcher.GetTotalHistoryCount())
        history = searcher.QueryHistory(0, min(total, limit)) if total else None
    except Exception as e:  # COM raises pywintypes.com_error and friends
        logger.warning("Windows Update history could not be read: %s", e)
        return None, f"Windows Update history could not be read: {e}"
    out: List[HistoryEntry] = []
    for i in range(history.Count if history is not None else 0):
        item = history.Item(i)
        try:
            if int(item.Operation) != 1:          # 1 = installation, 2 = uninstallation
                continue
            when = item.Date
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            out.append(HistoryEntry(when, str(item.Title or ""), int(item.ResultCode),
                                    int(item.HResult or 0)))
        except Exception as e:  # one malformed entry must not lose the rest
            logger.warning("skipping an unreadable Windows Update history entry: %s", e)
    return out, ""


def read_patch_status(reader: Callable = read_history) -> Tuple[Optional[PatchStatus], str]:
    entries, reason = reader()
    if entries is None:
        return None, reason
    return analyse(entries), ""
