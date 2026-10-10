r"""Open every module and every tab like a person would, and report what broke.

    .venv\Scripts\python.exe tools\module_tour.py [--seconds 5] [--only NAME] [--log FILE]

Builds the real App + MainWindow off-screen (with a throwaway app-data
folder, so the person's config and log are untouched), selects each sidebar
entry in turn, then each enabled tab of a composite, waiting `--seconds`
for each one to load real data. Every log record at WARNING or above and
every unhandled exception is attributed to the page that was showing.

Read-only by construction: it only navigates. Nothing is clicked inside a
module. Run it unelevated and again elevated (through a .ps1 wrapper with
--log) -- the two find different things.
"""
import argparse
import logging
import os
import sys
import tempfile
import time
import traceback
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

CURRENT = ["(startup)"]
FOUND = defaultdict(list)          # page -> [(level, logger, first line, detail)]

#: Expected unelevated noise: a module saying it is admin-gated is the design.
EXPECTED = ("requires admin", "If dialog fails")


class Collector(logging.Handler):
    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        msg = record.getMessage()
        if any(e in msg for e in EXPECTED):
            return
        detail = ""
        if record.exc_info:
            detail = "".join(traceback.format_exception(*record.exc_info))[-1500:]
        FOUND[CURRENT[0]].append((record.levelname, record.name, msg.splitlines()[0][:300], detail))


def excepthook(exc_type, exc, tb):
    FOUND[CURRENT[0]].append(("UNHANDLED", exc_type.__name__, str(exc)[:300],
                              "".join(traceback.format_exception(exc_type, exc, tb))[-2500:]))


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--only")
    ap.add_argument("--log")
    args = ap.parse_args()
    out = open(args.log, "w", encoding="utf-8") if args.log else sys.stdout

    qt = QApplication(sys.argv)
    from app import App
    import main as app_main
    from ui.main_window import MainWindow
    from core.composite_module import CompositeModule

    data = tempfile.mkdtemp(prefix="module-tour-")
    app = App(app_data_dir=data)
    logging.getLogger().addHandler(Collector())
    sys.excepthook = excepthook
    app_main.register_all_modules(app)
    app.start()
    window = MainWindow(app)
    for module in app.module_registry.modules:
        window.register_module(module)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.resize(1600, 1000)
    window.show()
    pump(qt, 2)

    names = [n for n in window._module_map if not args.only or args.only.lower() in n.lower()]
    started = time.monotonic()
    for name in names:
        module = window._module_map[name]
        CURRENT[0] = name
        try:
            window._on_module_selected(name)
        except Exception:                                   # noqa: BLE001 -- the finding itself
            excepthook(*sys.exc_info())
        pump(qt, args.seconds)
        tabs = getattr(module, "_tabs", None) if isinstance(module, CompositeModule) else None
        if tabs is not None:
            for i in range(tabs.count()):
                if not tabs.isTabEnabled(i) or i == 0:
                    continue
                CURRENT[0] = f"{name} > {tabs.tabText(i)}"
                try:
                    tabs.setCurrentIndex(i)
                except Exception:                           # noqa: BLE001
                    excepthook(*sys.exc_info())
                pump(qt, args.seconds)
    CURRENT[0] = "(shutdown)"
    window.close()
    pump(qt, 1)
    app.shutdown()

    print(f"toured {len(names)} modules in {time.monotonic() - started:.0f}s; "
          f"pages with findings: {len(FOUND)}", file=out)
    for page, items in FOUND.items():
        print(f"\n## {page}", file=out)
        seen = set()
        for level, logger_name, line, detail in items:
            key = (level, logger_name, line)
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{level}] {logger_name}: {line}", file=out)
            if detail:
                print("      " + detail.strip().replace("\n", "\n      ")[-1200:], file=out)
    out.flush()
    return 1 if FOUND else 0


if __name__ == "__main__":
    sys.exit(main())
