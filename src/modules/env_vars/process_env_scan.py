"""Which RUNNING processes actually have a given environment variable in
their own inherited copy, and whether it still matches what the registry
says today. Qt-free.

`effective_env.py` already answers this for exactly one process: this app's
own. That leaves the actual admin question unanswered -- "I just changed
PATH/JAVA_HOME/whatever, which of the shells, IDEs and services already
running still have the OLD value and need restarting?" -- because every
other process on the machine is invisible to it.

Measured on this real machine (2026-09-29, unelevated): of ~340 running
processes, `psutil.Process.environ()` succeeds for ~190 (this user's own
processes: VS Code, Steam, shells, ...) and refuses with `AccessDenied` for
the other ~150 (SYSTEM services and other sessions). That refusal is reported
as its own row here -- `readable=False` -- never folded into "this process
doesn't have the variable", which is a different, false claim.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

from modules.env_vars.effective_env import values_equivalent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessEnvRow:
    pid: int
    name: str
    username: Optional[str]
    value: Optional[str]      # None = readable, but this var is absent there
    readable: bool            # False = environ() itself was refused
    refusal: str = ""
    is_stale: bool = False    # readable, has a value, and it disagrees with `expected`


def scan_running_processes(
    name: str,
    expected: Optional[str],
    process_iter: Optional[Callable[[], Iterable]] = None,
) -> List[ProcessEnvRow]:
    """One row per process currently running. `expected` is the combined
    registry value (`EffectiveRow.combined_registry_value`) a NEW process
    would inherit today -- `None` means the variable is not set in the
    registry at all, in which case nothing can be "stale", only present or
    absent."""
    import psutil

    if process_iter is None:
        def process_iter():
            return psutil.process_iter(["pid", "name", "username"])

    upper = name.upper()
    rows: List[ProcessEnvRow] = []
    for p in process_iter():
        pid = p.info["pid"] if hasattr(p, "info") else p.pid
        pname = (p.info.get("name") if hasattr(p, "info") else None) or ""
        uname = p.info.get("username") if hasattr(p, "info") else None
        try:
            env = p.environ()
        except psutil.AccessDenied:
            rows.append(ProcessEnvRow(pid, pname, uname, None, False, "Access denied"))
            continue
        except psutil.NoSuchProcess:
            logger.debug("pid %s (%s) exited between listing and environ() read", pid, pname)
            continue  # not a refusal worth reporting -- the process is simply gone
        except Exception as e:  # psutil.Error subclasses we haven't seen, or a platform surprise
            logger.warning("environ() failed for pid %s (%s): %s", pid, pname, e)
            rows.append(ProcessEnvRow(pid, pname, uname, None, False, str(e)))
            continue
        val = next((v for k, v in env.items() if k.upper() == upper), None)
        stale = val is not None and expected is not None and not values_equivalent(upper, expected, val)
        rows.append(ProcessEnvRow(pid, pname, uname, val, True, "", stale))
    return rows


@dataclass(frozen=True)
class ScanSummary:
    total: int
    has_value: int
    stale: int
    refused: int


def summarize(rows: List[ProcessEnvRow]) -> ScanSummary:
    return ScanSummary(
        total=len(rows),
        has_value=sum(1 for r in rows if r.readable and r.value is not None),
        stale=sum(1 for r in rows if r.is_stale),
        refused=sum(1 for r in rows if not r.readable),
    )
