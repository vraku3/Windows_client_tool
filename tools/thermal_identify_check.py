"""Real-hardware check of Identify through the app's own ThermalService.

    python tools/thermal_identify_check.py --log FILE [--group cpu|case|gpu]

Opens the hardware the way the app does, presses the group's Identify,
logs every fan's RPM each second for the 20 s and after, then shuts the
service down (every header handed back). Run elevated through a .ps1
wrapper for cpu/case; gpu needs no elevation.
"""
import argparse
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PyQt6.QtCore import QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from modules.thermal_control.engine.model import FAN  # noqa: E402
from modules.thermal_control.thermal_service import ThermalService  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    ap.add_argument("--group", default="cpu")
    args = ap.parse_args()
    out = open(args.log, "w", encoding="utf-8") if args.log else sys.stdout
    app = QApplication(sys.argv)
    store = {}
    cfg = SimpleNamespace(get=lambda k, d=None: store.get(k, d), set=lambda k, v: store.__setitem__(k, v))
    svc = ThermalService(SimpleNamespace(config=cfg, app_data_dir=os.environ["TEMP"],
                                         thread_pool=QThreadPool.globalInstance()),
                         os.path.join(os.environ["TEMP"], "identify_check_marker.json"))

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)

    def fans():
        return {s.name: round(s.value or 0) for s in svc.read_now() if s.kind == FAN}

    svc.start()
    svc.add_viewer()
    pump(8)
    print("full:", svc.full, "reason:", svc.reason, file=out, flush=True)
    print("before:", fans(), file=out, flush=True)
    started, problems = svc.identify_group(args.group)
    print("started:", started, "problems:", problems, file=out, flush=True)
    for i in range(24):
        pump(1)
        print(f"t+{i + 1}s left={svc.identifying()}", fans(), file=out, flush=True)
    svc.shutdown()
    print("shutdown done; controller:", svc.controller, file=out, flush=True)
    return 0 if started and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
