r"""Off-screen render of the Debloat Apps tab's new "Reinstalled" badge.

    .venv\Scripts\python.exe tools\debloat_reinstall_badge_render.py [outdir]

Builds a real DebloatToolsModule widget, seeds one real KNOWN_PACKAGES id as
"previously removed" via debloat_reinstall_tracker, populates the Apps table
against a fake installed list that includes it, and grabs the widget --
proving the warning-coloured status text and tooltip actually render, not
just that the Python object carries the right string.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.event_bus import EventBus  # noqa: E402
from modules.debloat import debloat_module as dm  # noqa: E402
from modules.debloat import debloat_reinstall_tracker as rt  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "ui-debloat"
os.makedirs(OUT, exist_ok=True)


class _FakeBackup:
    def create_restore_point(self, label, module):
        return "rp-fake"

    def record_steps(self, *a, **k):
        pass


class _FakeConfig:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeApp:
    def __init__(self):
        self.backup = _FakeBackup()
        self.thread_pool = None
        self.config = _FakeConfig()
        self.event_bus = EventBus()


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    store = os.path.join(OUT, "debloat_removed.json")
    rt._store_path = lambda: store  # noqa: SLF001 -- test-only seam
    if os.path.exists(store):
        os.remove(store)
    rt.record_removed(["Microsoft.XboxApp"])

    mod = dm.DebloatToolsModule()
    mod.on_start(_FakeApp())
    widget = mod.create_widget()
    mod._populate_apps_table(
        ["Microsoft.XboxApp", "Microsoft.BingWeather"],
        {r["package"]: r for r in rt.check_reinstalled(
            ["Microsoft.XboxApp", "Microsoft.BingWeather"])})
    widget.resize(1100, 700)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    widget.show()
    app.processEvents()

    path = os.path.join(OUT, "apps-tab-reinstall-badge.png")
    widget.grab().save(path)
    print(f"wrote {path}")

    for row in range(mod._apps_table.rowCount()):
        name = mod._apps_table.item(row, 1).text()
        status = mod._apps_table.item(row, 3).text()
        if "Xbox" in name:
            print("row check:", name.encode("ascii", "replace"),
                  "->", status.encode("ascii", "replace"))
            assert "Reinstalled" in status, status


if __name__ == "__main__":
    main()
