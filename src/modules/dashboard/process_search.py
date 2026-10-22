"""Exposes live processes to the app's global search bar (W5-04).

Every dashboard tab has its own search box, but the app's global search --
the bar in the MainWindow that queries every module -- could not find a
process. That is the gap this fills: type a name, pid, user or command
line in the global bar and the process is a hit.

The query reads the machine ON DEMAND rather than holding a cache. The
bulk syscall costs ~2.6 ms for the whole machine (`ntquery` measures it),
so a live read is cheaper than keeping a snapshot in sync -- and it can
never answer with a process that died a second ago, which a stale cache
would do.

Cold details (path, command line, user) are resolved per hit, lazily, the
same way the Details tab resolves them -- unelevated half the machine
refuses, and a refusal is not an answer, so a process whose command line
or user could not be read still matches on what could be (name, pid) and
its result says so.

Qt-free: it reads through the engine, so it is testable headless.
"""
import datetime
import logging
from typing import List

from core.search_provider import (FilterField, SearchProvider, SearchQuery,
                                  SearchResult)

from core.procengine.snapshot import SnapshotSource

logger = logging.getLogger(__name__)

#: The bar queries every provider per keystroke and merges the lists;
#: returning the whole machine would drown the other modules' results.
MAX_RESULTS = 50

PROCESS = "Process"

_UNIX_EPOCH_FILETIME = 116444736000000000
_TICKS_PER_SECOND = 10_000_000


class ProcessSearchProvider(SearchProvider):
    """Searches the processes running right now."""

    module_name = "Dashboard"

    def __init__(self) -> None:
        self._source = SnapshotSource()

    def search(self, query: SearchQuery) -> List[SearchResult]:
        needle = (query.text or "").strip()
        if not needle:
            return []
        try:
            snapshot = self._source.read()
        except Exception:  # noqa: BLE001 - a search must not take the bar down
            logger.warning("Process search could not read the machine",
                           exc_info=True)
            return []

        lowered = needle.lower()
        hits = []
        for info in snapshot.by_pid.values():
            if _matches(info, lowered):
                hits.append(info)
        if not hits:
            return []

        # Rank: exact pid and name matches outrank a partial command-line
        # hit, the way someone searching is most likely looking.
        results = []
        for rank, info in enumerate(hits):
            results.append(SearchResult(
                timestamp=_start_time(info),
                source="processes",
                type=PROCESS,
                summary=_summary(info),
                detail=info,
                relevance=_relevance(info, lowered, rank),
            ))
        results.sort(key=lambda result: result.relevance, reverse=True)
        return results[:MAX_RESULTS]

    def get_filterable_fields(self) -> List[FilterField]:
        return [FilterField(name="type", label="Type", values=[PROCESS])]


def _matches(info, needle: str) -> bool:
    if needle in info.name.lower():
        return True
    if needle == str(info.pid):
        return True
    details = info.details
    if details.user and needle in details.user.lower():
        return True
    if details.cmdline and needle in details.cmdline.lower():
        return True
    if details.description and needle in details.description.lower():
        return True
    return False


def _summary(info) -> str:
    """What the hit is called: "python.exe (PID 12345)". A description, where
    one exists, leads -- "Google Chrome" beats "chrome.exe"."""
    name = getattr(info.details, "description", None) or info.name
    return f"{name} (PID {info.pid})"


def _relevance(info, needle: str, rank: int) -> float:
    if needle == str(info.pid) or needle == info.name.lower():
        return 1.0
    return 0.9 / (1 + rank * 0.005)


def _start_time(info) -> datetime.datetime:
    create_time = getattr(info.raw, "create_time", 0) or 0
    if create_time <= 0:
        return datetime.datetime.now()
    try:
        seconds = (create_time - _UNIX_EPOCH_FILETIME) / _TICKS_PER_SECOND
        return datetime.datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return datetime.datetime.now()
