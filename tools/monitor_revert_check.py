"""Prove the revert countdown against a real display change.

Raises the active monitor's refresh rate, then deliberately lets the
countdown EXPIRE. Nobody clicks anything, which is exactly the case the
guard exists for: the machine must put the old mode back by itself.

Two things learned the hard way on the first attempt, both fixed here:

* **Write the log to a file directly, never through a pipe.** The first run
  went through `grep`, the pipeline was killed at a timeout, and every line
  of a safety-critical run was lost. A run you cannot read is a run you
  cannot trust.
* **Restore in a `finally`.** The first run left the display at 120 Hz
  because the countdown never resolved and nothing else put it back. The
  guard being under test is exactly the thing that cannot be relied on to
  clean up after itself.

A hard kill still skips the `finally`, so this also prints the exact command
to restore by hand.
"""
import sys
import time

sys.path.insert(0, "src")


if "--yes-change-my-display" not in sys.argv:
    print(__doc__)
    print("This CHANGES a real display. Re-run with --yes-change-my-display "
          "to proceed.")
    raise SystemExit(2)

LOG = open("monitor_revert_check.log", "w", encoding="utf-8", buffering=1)


def say(*parts):
    line = " ".join(str(p) for p in parts)
    LOG.write(line + "\n")
    print(line, flush=True)


from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from modules.monitor_control import _apply_guard as guard  # noqa: E402
from modules.monitor_control import display_modes as dm  # noqa: E402
from modules.monitor_control import display_writes as dw  # noqa: E402

device = None
for name, _adapter, attached, _primary in dm.attached_devices():
    if attached and dm.current_mode(name):
        device = name
        break

before = dm.current_mode(device)
rates = dm.refresh_rates_for(device, before.width, before.height)
target = max(rates)
say(f"device : {device}")
say(f"before : {before.describe()}")
say(f"rates  : {rates}")
say(f"target : {target:g} Hz, then let the countdown expire")
say(f"restore by hand if needed: {before.width}x{before.height}"
    f"@{before.refresh_hz:g}")

if target == before.refresh_hz:
    say("already at the highest rate here - nothing to prove")
    raise SystemExit(0)

state = {}


def _snapshot():
    return dm.current_mode(device)


def _apply():
    ok, reason = dw.apply_mode(device, before.width, before.height, target)
    say(f"  apply    : ok={ok} {reason}")
    if not ok:
        raise OSError(reason)
    say(f"  now      : {dm.current_mode(device).describe()}")


def _restore(mode):
    ok, reason = dw.apply_mode(device, mode.width, mode.height, mode.refresh_hz)
    say(f"  restore  : ok={ok} {reason}")
    if not ok:
        raise OSError(reason)


def _start_countdown(before_mode):
    def _resolved(kept):
        state["kept"] = kept
        state["outcome"] = guard.resolve(kept, before_mode, _restore)
        say(f"  resolved : kept={kept} -> {state['outcome'].value}")
        app.quit()

    countdown = guard.RevertCountdown(
        seconds=5, on_resolve=_resolved,
        summary=f"{device} raised to {target:g} Hz")
    countdown.start()
    state["countdown"] = countdown
    say("  countdown: running, nothing will be clicked")


try:
    result = guard.run_apply(snapshot=_snapshot, apply=_apply,
                             start_countdown=_start_countdown)
    say(f"  run_apply: applied={result.applied} {result.error}")

    if result.applied:
        # A hard ceiling: if the countdown does not resolve, do not sit here.
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(
            lambda: (say("  WATCHDOG : the countdown never resolved"),
                     app.quit()))
        watchdog.start(20_000)
        app.exec()
finally:
    time.sleep(0.5)
    after = dm.current_mode(device)
    say(f"after  : {after.describe() if after else 'unreadable'}")
    if after and after.refresh_hz != before.refresh_hz:
        say("  the guard did NOT put it back - restoring directly")
        ok, reason = dw.apply_mode(device, before.width, before.height,
                                   before.refresh_hz)
        say(f"  forced restore: ok={ok} {reason}")
        after = dm.current_mode(device)
    say(f"FINAL  : {after.describe() if after else 'unreadable'}")
    say(f"BACK WHERE IT STARTED: "
        f"{bool(after and after.refresh_hz == before.refresh_hz)}")
    LOG.close()
