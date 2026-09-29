"""What THIS process actually sees vs. what the registry says. No Qt.

The classic support question: "I set an environment variable, why doesn't my
program see it?" Windows only re-reads the registry into a NEW process' block
at creation, so any process already running -- this one included -- keeps
whatever it inherited at launch. A registry write is not a live change; this
compares the two and says, for each name, whether it is stale.
"""
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from modules.env_vars.env_ops import EnvVar


@dataclass(frozen=True)
class EffectiveRow:
    name: str
    system_value: Optional[str]     # None = not set at this scope
    user_value: Optional[str]
    process_value: Optional[str]    # None = not in this process' environment at all
    is_stale: bool                  # registry disagrees with what this process actually has
    process_only: bool              # in the process env but neither registry scope names it

    @property
    def combined_registry_value(self) -> Optional[str]:
        """What a NEW process would get: User overrides System for the same name,
        except PATH, which Windows appends (System first, then User)."""
        if self.name.upper() == "PATH":
            parts = [v for v in (self.system_value, self.user_value) if v]
            return ";".join(parts) if parts else None
        return self.user_value if self.user_value is not None else self.system_value


def read_process_env() -> Dict[str, str]:
    """This process' own environment -- exactly what it inherited at launch."""
    return dict(os.environ)


def _index(rows: Sequence[EnvVar]) -> Dict[str, str]:
    return {v.name.upper(): v.value for v in rows}


def compare(system_rows: Sequence[EnvVar], user_rows: Sequence[EnvVar],
           process_env: Optional[Dict[str, str]] = None) -> List[EffectiveRow]:
    """One row per name that appears anywhere (registry, at either scope, or the
    live process). A name only in the live process (set by whatever launched
    it, never written to the registry) is marked `process_only`."""
    process_env = process_env if process_env is not None else read_process_env()
    sysmap, usrmap = _index(system_rows), _index(user_rows)
    proc_upper = {k.upper(): (k, v) for k, v in process_env.items()}
    names = sorted(set(sysmap) | set(usrmap) | set(proc_upper))

    out = []
    for upper in names:
        sysval, usrval = sysmap.get(upper), usrmap.get(upper)
        proc_key, procval = proc_upper.get(upper, (upper, None))
        expected = _combined(upper, sysval, usrval)
        stale = expected is not None and procval is not None and not values_equivalent(upper, expected, procval)
        out.append(EffectiveRow(
            name=proc_key if proc_key in process_env else upper,
            system_value=sysval, user_value=usrval, process_value=procval,
            is_stale=stale, process_only=sysval is None and usrval is None and procval is not None))
    return out


def _combined(name_upper: str, sysval: Optional[str], usrval: Optional[str]) -> Optional[str]:
    if name_upper == "PATH":
        parts = [v for v in (sysval, usrval) if v]
        return ";".join(parts) if parts else None
    return usrval if usrval is not None else sysval


def values_equivalent(name_upper: str, expected: str, actual: str) -> bool:
    """PATH comparisons ignore order and blank entries: Windows itself, and
    whatever launched this process, can reorder or add its own segments
    (a shell's own PATH additions) without that being registry drift.

    Public (not `_`-prefixed): `process_env_scan.py` reuses this exact rule to
    judge staleness in OTHER processes' environment blocks, not just this
    process' own -- the same "PATH order/blanks don't count as drift" logic
    has to apply there too, or a process with a merely reordered PATH would
    be flagged stale for no real reason.
    """
    if name_upper == "PATH":
        def norm(text: str):
            return {p.strip().rstrip("\\").lower() for p in text.split(";") if p.strip()}
        return norm(expected) <= norm(actual)
    return expected == actual


def stale_rows(rows: Sequence[EffectiveRow]) -> List[EffectiveRow]:
    return [r for r in rows if r.is_stale]
