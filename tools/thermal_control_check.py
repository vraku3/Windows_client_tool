"""Real-hardware check for the thermal engine. Read-only unless --roundtrip.

    python tools/thermal_control_check.py [--log FILE] [--roundtrip "Chassis Fan #1"]

Lists every temperature, fan and controllable header the engine sees. With
--roundtrip it takes ONE header, sets it to 60%, reads the duty back, hands
it back to the BIOS and reads again -- pick a header with nothing plugged in
(0 RPM) so no real fan changes speed. Needs elevation for anything beyond
the GPU; run it through an elevated PowerShell (Start-Process -Verb RunAs
cannot redirect output, hence --log).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from modules.thermal_control.engine import gpu_kmt, lhm_bridge  # noqa: E402
from modules.thermal_control.engine.model import CONTROL  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    ap.add_argument("--roundtrip")
    args = ap.parse_args()
    out = open(args.log, "w", encoding="utf-8") if args.log else sys.stdout

    def p(*a):
        print(*a, file=out, flush=True)

    p("unavailable:", lhm_bridge.unavailable_reason() or "(no -- full set readable)")
    bridge = lhm_bridge.LhmBridge()
    bridge.open()
    sensors = bridge.read() + gpu_kmt.read_gpus()
    for s in sensors:
        p(f"{s.kind:<11} {s.hardware:<26} {s.name:<28} {s.display():>12}  {'CTRL ' if s.controllable else ''}{s.id}")
    rc = 0
    if args.roundtrip:
        rc = _roundtrip(bridge, sensors, args.roundtrip, p)
    failed = bridge.release_all()
    p("release_all failures:", failed)
    bridge.close()
    return rc


def _duty(bridge, cid):
    return next((s.value for s in bridge.read() if s.id == cid), None)


def _roundtrip(bridge, sensors, name, p) -> int:
    target = next((s for s in sensors if s.kind == CONTROL and s.name == name and s.controllable), None)
    if target is None:
        p(f"no controllable header named {name!r}")
        return 2
    before = _duty(bridge, target.id)
    bridge.set_percent(target.id, 60.0)
    time.sleep(1.5)
    during = _duty(bridge, target.id)
    bridge.release(target.id)
    time.sleep(3.0)
    after = _duty(bridge, target.id)
    p(f"ROUNDTRIP {name}: before={before} set=60 read_back={during} after_release={after}")
    ok = during is not None and abs(during - 60.0) < 2.0
    p("write verified" if ok else "WRITE NOT VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
