r"""Off-screen render of App Buster against THIS machine, read-only.

    .venv\Scripts\python.exe tools\app_buster_render.py [outdir]

Scans for real (nothing is removed, installed or changed), waits for storage
and winget, and saves Cards and Details in dark and light, plus a Properties
dialog for one app and the removal dialog for a multi-selection (shown, never
accepted). Prints the Smart View counts so a run can be compared with the
screenshots.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt6.QtCore import QThreadPool, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.config_manager import ConfigManager  # noqa: E402
from core.semantic_colors import set_theme  # noqa: E402
from core.theme_manager import ThemeManager  # noqa: E402
from modules.app_buster import app_buster_module as abm, dialogs  # noqa: E402
from modules.app_buster.engine import views  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "app-buster-render")
os.makedirs(OUT, exist_ok=True)


class FakeApp:
    pass


def pump(app, seconds, until=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return False


def grab(widget, name):
    path = os.path.join(OUT, name)
    widget.grab().save(path)
    print("wrote", path)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    themes = ThemeManager(os.path.join(ROOT, "src", "ui", "styles"))
    FakeApp.config = ConfigManager(OUT, {"version": 1})
    FakeApp.config.load()
    FakeApp.thread_pool = QThreadPool.globalInstance()
    # Never touch the real "seen apps" baseline: Newly discovered would be wrong next session.
    abm._seen_path = lambda: os.path.join(OUT, "seen.json")
    mod = abm.AppBusterModule()
    mod.on_start(FakeApp())
    w = mod.create_widget()
    w.resize(1500, 900)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    w.show()
    mod.on_activate()
    assert pump(app, 120, lambda: bool(w._rows) and not w._busy), "scan did not finish"
    pump(app, 180, lambda: "available" in w._status.text() or "did not complete" in w._status.text())
    counts = views.view_counts(w._rows, w._new_keys, w._opts)
    print("rows", len(w._rows), "shown", len(w._model.rows), "counts", counts, "status:", w._status.text())
    for theme in ("dark", "light"):
        themes.apply_theme(theme)
        set_theme(theme)
        w._set_layout("cards")
        w._apply()
        pump(app, 0.5)
        grab(w, f"cards-{theme}.png")
        w._set_layout("details")
        w._apply()
        pump(app, 0.5)
        grab(w, f"details-{theme}.png")
    themes.apply_theme("dark")
    set_theme("dark")
    w._set_layout("cards")
    w._pick_view("defect")
    pump(app, 0.3)
    grab(w, "view-defect.png")
    w._pick_view("all")
    w._select("remove")
    w._toggle_group(True)
    pump(app, 0.3)
    grab(w, "selected-grouped.png")
    w._toggle_group(False)
    picked = w._checked_records()
    dlg = dialogs.RemovalDialog(picked + [r for r in w._rows if r.type == "Defect"][:2], False, w)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.show()
    pump(app, 0.3)
    grab(dlg, "removal-dialog.png")
    dlg.reject()
    target = next(r for r in w._rows if r.type == "Windows app" and r.storage)
    props = dialogs.PropertiesDialog(target, w)
    props.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    props.show()
    pump(app, 0.3)
    grab(props, "properties.png")
    w._search.setText("visual c++")
    pump(app, 0.3)
    print("search 'visual c++' ->", len(w._model.rows), "rows in view", w._view)
    grab(w, "search.png")
    w._reset_selection()
    mod.on_stop()


if __name__ == "__main__":
    main()
