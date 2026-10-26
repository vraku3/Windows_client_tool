"""No `X.cancel()` followed by `X = None` without core.worker.retire().

Dropping the last reference to a running Worker frees its WorkerSignals while
queued cancelled/finished signals are still in the event loop; delivering
them aborted whole-suite runs natively (0xC0000409) at random points.
2026-10-26: nine such sites (Quick Fix card, Certificates, Diagnose search,
Startup, three Cleanup panels) -- four clean chunk runs after retire(), where
about half had crashed before. Hand the worker to retire([X]) first.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
# A cancel of self.<name>, then (within 4 lines) self.<name> = None, with no
# retire( in between. Threads that are .wait()-ed before the drop are fine.
_CANCEL = re.compile(r"^\s*(self\.\w+)\.cancel\(\)\s*$")


def _offenders():
    found = []
    for path in SRC.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _CANCEL.match(line)
            if not m:
                continue
            name = m.group(1)
            window = []
            for l in lines[i + 1:i + 5]:
                if l.lstrip().startswith(("def ", "async def ")):
                    break  # a drop in another function (e.g. the finished handler) is fine
                window.append(l)
            drop = next((j for j, l in enumerate(window) if re.match(rf"^\s*{re.escape(name)}\s*=\s*None\s*$", l)), None)
            if drop is None:
                continue
            between = window[:drop]
            if any("retire(" in l or ".wait(" in l for l in between + [line]):
                continue
            found.append(f"{path.relative_to(SRC)}:{i + 1}: {line.strip()}")
    return found


def test_no_running_worker_is_dropped_after_cancel():
    assert not _offenders(), ("cancel() then '= None' drops a running worker; use "
                              "core.worker.retire([w]) first:\n" + "\n".join(_offenders()))
