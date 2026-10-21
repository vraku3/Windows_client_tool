r"""REAL elevated Maximum -> Revert, with raw before/after comparisons.

Run explicitly as the owner: .venv\Scripts\python tools/optimise_os_roundtrip.py
Refuses an existing Optimise OS journal or Game Mode ON. Never run by tests.

Covers every reversible registry / service / power item Maximum selects --
including Remote Assistance (T5), the Retail Demo service (T6) and the Chrome /
Brave background-mode policies (M3, M4; skipped as "Not applicable" when the
browser is not installed). It deliberately does NOT cover:
* the Windows capability removals (M5-M7): re-adding needs Windows Update and
  may be impossible on an Insider build, so a round trip could strand them;
* the optional features/capabilities, which no profile ticks;
* "Free up space": one-way by design, there is nothing to revert.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from modules.game_mode.engine.optimise import (  # noqa: E402
    BY_ID, ITEMS, OptimiseError, Optimiser, WindowsSystem, display, profile_ids,
)

#: Kinds this tool never changes (see the module docstring).
NOT_ROUND_TRIPPED = ("capability", "feature")
ROUNDTRIP_IDS = {i for i in profile_ids("maximum") if BY_ID[i].kind not in NOT_ROUND_TRIPPED}
SNAPSHOT = tuple(spec for spec in ITEMS if spec.kind not in NOT_ROUND_TRIPPED)


def snapshot(system, scheme=None) -> dict:
    """Raw registry presence/type/value, live delays, SCM config/state and power AC index."""
    return {spec.id: spec.read(system, scheme if spec.kind == "power" else None) for spec in SNAPSHOT}


def main(system=None, folder=None) -> int:
    system = system if system is not None else WindowsSystem()
    engine = Optimiser(system, folder)          # an explicit system: no space / usage engines
    print("Not covered here, on purpose: capability removals (re-adding needs Windows Update / internet) "
          "and the one-way Free up space cleanups.\n")
    try:
        if not system.is_admin():
            raise OptimiseError("Run this tool elevated.")
        engine._off_only()
        if engine.path.exists():
            raise OptimiseError(f"Optimise OS journal already exists: {engine.path}. Revert it in the app first.")
        before = snapshot(system)
        scheme = before["usb_suspend"]["scheme"]
        if engine.path.exists():
            raise OptimiseError("An Optimise OS journal appeared during the snapshot; refusing to apply.")
    except (OSError, ValueError, OptimiseError) as error:
        print(f"Refused before making changes: {error}")
        return 2

    failed = False
    try:
        result = engine.apply(ROUNDTRIP_IDS)
        print("APPLY\n" + result.text())
        applied = snapshot(system, scheme)
        for key in sorted(ROUNDTRIP_IDS):
            spec = BY_ID[key]
            if key in result.skipped:
                print(f"Skipped: {spec.label}: {result.skipped[key]}")
                continue
            if applied[key] != spec.target(before[key]):
                print(f"Apply verification failed: {spec.label}: {display(applied[key])}")
                failed = True
        failed = failed or bool(result.failed)
    except (OSError, ValueError, OptimiseError) as error:
        print(f"Apply/read-back failed; attempting recorded recovery: {error}")
        failed = True
    finally:
        try:
            result = engine.revert()
            print("\nREVERT\n" + result.text())
            failed = failed or bool(result.failed)
        except (OSError, ValueError, OptimiseError) as error:
            print(f"Revert failed; journal kept at {engine.path}: {error}")
            failed = True

    try:
        after = snapshot(system, scheme)
    except (OSError, ValueError) as error:
        print(f"Final snapshot failed: {error}. Check the journal at {engine.path}.")
        return 1
    print("\nSetting | Before | After Revert | Result")
    print("-" * 110)
    for spec in SNAPSHOT:
        equal = before[spec.id] == after[spec.id]
        print(f"{spec.label} | {display(before[spec.id])} | {display(after[spec.id])} | "
              + ("SAME" if equal else "DIFFERENT"))
        if not equal:
            failed = True
            print(f"  Raw before: {before[spec.id]!r}\n  Raw after:  {after[spec.id]!r}")
    if engine.path.exists():
        print(f"Recovery is incomplete; journal kept at {engine.path}.")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
