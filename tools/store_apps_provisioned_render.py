r"""Off-screen render of Store Apps' new "New Users" (provisioned) column
and the provisioned-only virtual row.

    .venv\Scripts\python.exe tools\store_apps_provisioned_render.py [outdir]

Builds a real StoreAppsModule widget, feeds it a fake installed-apps list
plus a fake provisioned map (matching the real shape captured live 2026-09-30:
Clipchamp.Clipchamp provisioned but not installed for the current user), and
grabs the widget -- proving the "Still provisioned" warning row and the
"New Users" column actually render, not just that the Python object carries
the right string.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QThreadPool  # noqa: E402

from core.backup_service import BackupService  # noqa: E402
from core.config_manager import ConfigManager  # noqa: E402
from core.event_bus import EventBus  # noqa: E402
from modules.store_apps import store_apps_module as sam  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "ui-store-apps"
os.makedirs(OUT, exist_ok=True)


class FakeApp:
    pass


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    FakeApp.backup = BackupService(OUT)
    FakeApp.config = ConfigManager(OUT, {"version": 1})
    FakeApp.config.load()
    FakeApp.config.set("modules.store_apps.show_provisioned", True)
    FakeApp.thread_pool = QThreadPool.globalInstance()
    FakeApp.event_bus = EventBus()

    mod = sam.StoreAppsModule()
    mod.on_start(FakeApp())
    widget = mod.create_widget()

    apps = [
        {"Name": "Microsoft.WindowsCalculator",
         "Publisher": "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
         "Version": "11.2607.0.0", "InstallLocation": "",
         "PackageFamilyName": "Microsoft.WindowsCalculator_8wekyb3d8bbwe",
         "Architecture": "X64"},
    ]
    # Shape matches the real DisplayName/PackageName pairs captured live
    # 2026-09-30: Clipchamp is provisioned but not installed for this user.
    provisioned_map = {
        "Microsoft.WindowsCalculator": "Microsoft.WindowsCalculator_2021.2607.0.0_neutral_~_8wekyb3d8bbwe",
        "Clipchamp.Clipchamp": "Clipchamp.Clipchamp_4.6.10320.0_neutral_~_yxz26nhyzhsrt",
    }
    mod._on_apps_loaded(apps, None, provisioned_map=provisioned_map)

    widget.resize(1400, 500)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    widget.show()
    app.processEvents()

    path = os.path.join(OUT, "store-apps-new-users-column.png")
    widget.grab().save(path)
    print(f"wrote {path}")

    assert mod._table.rowCount() == 2, mod._table.rowCount()
    calc_row = mod._row_of("Microsoft.WindowsCalculator")
    ghost_row = mod._row_of("Clipchamp.Clipchamp")
    calc_status = mod._table.item(calc_row, 7).text()
    ghost_status = mod._table.item(ghost_row, 7).text()
    ghost_removable = mod._table.item(ghost_row, 4).text()
    print("Calculator New Users column:", calc_status.encode("ascii", "replace"))
    print("Clipchamp New Users column:", ghost_status.encode("ascii", "replace"))
    print("Clipchamp User-Removable column:", ghost_removable.encode("ascii", "replace"))
    assert calc_status == "Yes", calc_status
    assert "provisioned" in ghost_status.lower(), ghost_status
    assert "not installed" in ghost_removable.lower(), ghost_removable


if __name__ == "__main__":
    main()
