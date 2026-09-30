"""Modes and units (spec 5.5).

Two orthogonal settings that together decide every number the UI shows.

**Mode** selects WHAT is measured -- size, allocated space, file count, or
percent of parent. It is pane state, not per-view state: switching mode changes
the tree bars, the chart and the size columns together.

**Unit** selects HOW a byte count is rendered, with a configurable decimal
count. Auto picks the most appropriate unit per value, which is what produces
Pro's mixed-unit columns -- one row in MB and the next in GB.

Kept free of Qt so it can be tested without a display.
"""
from enum import Enum

# Unit and format_bytes moved to core.formatting — five copies of
# this existed and disagreed below 1 KB. Re-exported so every
# `from .formatting import Unit, format_bytes` still resolves.
from core.formatting import Unit, format_bytes  # noqa: F401

from ..store.aggregates import FILETIME_TICKS_PER_DAY
from ..store.node_store import DIR

# Same cutoffs the Age view (store/aggregates.py's AGE_BUCKETS) already uses
# for "This week" and "Older": one source of truth for what "recent" and
# "stale" mean in this module, so the Last Modified colour and the Age
# histogram never disagree about where a file lands.
ACTIVE_WITHIN_DAYS = 7
STALE_AFTER_DAYS = 365


class Mode(str, Enum):
    SIZE = "Size"
    ALLOCATED = "Allocated space"
    FILES = "Number of files"
    PERCENT = "Percent"




def format_count(value: int) -> str:
    return f"{value:,}"


def format_percent(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}%"


def files_of(store, node: int) -> int:
    """File count for a node: a folder's subtree total, or 1 for a file.

    `rollup` charges a file to its PARENT, so `file_count` on a file itself is
    0. That is right for aggregation and wrong for display: under Number of
    files a file row read "0", which is not a count but an artefact of where
    the tally is kept. Weighting a chart by the raw column is worse still --
    every leaf gets zero area and the chart draws nothing.
    """
    return store.file_count[node] if store.attrs[node] & DIR else 1


def weight_value(store, node: int, mode: Mode) -> int:
    """The number a chart weights this node by under the current mode.

    PERCENT weights by size: a slice's share of its parent IS its percent, so
    a percent-weighted chart is a size-weighted chart with different labels.
    """
    if mode is Mode.ALLOCATED:
        return store.alloc[node]
    if mode is Mode.FILES:
        return files_of(store, node)
    return store.size[node]


def node_value(store, node: int, mode: Mode) -> float:
    """The raw number this node contributes under the current mode."""
    if mode is Mode.SIZE:
        return store.size[node]
    if mode is Mode.ALLOCATED:
        return store.alloc[node]
    if mode is Mode.FILES:
        return files_of(store, node)
    if mode is Mode.PERCENT:
        return percent_of_parent(store, node)
    raise ValueError(f"unknown mode: {mode!r}")


def percent_of_parent(store, node: int) -> float:
    """Share of the parent's size. A root, or a zero-sized parent, is 100%."""
    parent = store.parent[node]
    if not (0 <= parent < len(store)) or parent == node:
        return 100.0
    total = store.size[parent]
    if total <= 0:
        return 0.0
    return store.size[node] * 100.0 / total


def format_value(store, node: int, mode: Mode, unit: Unit = Unit.AUTO,
                 decimals: int = 1) -> str:
    """The display string for a node under the current mode and unit."""
    if mode is Mode.FILES:
        return format_count(files_of(store, node))
    if mode is Mode.PERCENT:
        return format_percent(percent_of_parent(store, node), decimals)
    return format_bytes(int(node_value(store, node, mode)), unit, decimals)


def age_tier(mtime: int, now: int) -> str | None:
    """"active" (touched within the last week), "stale" (untouched for a
    year or more), or None for the ordinary middle ground -- which is also
    what a missing or future timestamp returns, since neither is evidence
    of either extreme and colouring one of them is a guess, not a reading.

    `mtime` and `now` are both Windows FILETIME ticks (see
    `scan/filters.py`'s `filetime_now`), matching every other age
    calculation in this module rather than converting to Unix time and
    risking a second, disagreeing epoch conversion.
    """
    if not mtime:
        return None
    age_days = (now - mtime) / FILETIME_TICKS_PER_DAY
    if age_days < 0:
        return None
    if age_days < ACTIVE_WITHIN_DAYS:
        return "active"
    if age_days >= STALE_AFTER_DAYS:
        return "stale"
    return None


def bar_fraction(store, node: int, mode: Mode) -> float:
    """0.0-1.0 for the proportional bar drawn beside a row.

    Always relative to the parent, whatever the mode, because the bar answers
    "how much of the folder I am inside does this account for" -- the question
    Pro's bars answer.
    """
    parent = store.parent[node]
    if not (0 <= parent < len(store)) or parent == node:
        return 1.0
    if mode is Mode.FILES:
        total = files_of(store, parent)
        mine = files_of(store, node)
    elif mode is Mode.ALLOCATED:
        total = store.alloc[parent]
        mine = store.alloc[node]
    else:
        total = store.size[parent]
        mine = store.size[node]
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, mine / total))
