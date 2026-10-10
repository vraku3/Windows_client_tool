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
    ap.add_argument("--spin", help="comma-separated header names: 100%% for --seconds, RPM logged")
    ap.add_argument("--seconds", type=int, default=8)
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
    if args.spin:
        rc = _spin(bridge, sensors, [n.strip() for n in args.spin.split(",")], args.seconds, p)
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


def _rpms(bridge, ids):
    by_id = {s.id: s for s in bridge.read()}
    return {cid: (by_id[cid.replace("/control/", "/fan/")].value
                  if cid.replace("/control/", "/fan/") in by_id else None,
                  by_id[cid].value if cid in by_id else None) for cid in ids}


def _spin(bridge, sensors, names, seconds, p) -> int:
    """The physical test Identify depends on: does the FAN speed up?"""
    rpm = {s.id: s.value for s in sensors}
    targets = [s for s in sensors if s.kind == CONTROL and s.controllable and not s.id.startswith("/gpu")
               and (s.name in names or ("connected" in names and rpm.get(s.id.replace("/control/", "/fan/"))))]
    if not targets:
        p(f"no controllable header among {names}")
        return 2
    ids = [t.id for t in targets]
    p("SPIN before:", _rpms(bridge, ids))
    for cid in ids:
        bridge.set_percent(cid, 100.0)
    for i in range(seconds):
        time.sleep(1.0)
        p(f"SPIN t+{i + 1}s (rpm, duty):", _rpms(bridge, ids))
    for cid in ids:
        bridge.release(cid)
    for i in range(4):
        time.sleep(1.0)
        p(f"SPIN released t+{i + 1}s:", _rpms(bridge, ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
