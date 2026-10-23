"""Render a representative pane offscreen using memory HKCU; no real entries are changed."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QThreadPool
from PyQt6.QtGui import QColor, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication
from core.semantic_colors import chrome
from modules.nav_pane.engine.model import Health, Kind, NavEntry
from modules.nav_pane.engine.operations import Operations
from modules.nav_pane.engine.registry import MemoryRegistry
from modules.nav_pane.nav_pane_module import NavPane


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    # The offscreen Windows backend may enumerate zero installed families.
    for filename in ("segoeui.ttf", "segoeuib.ttf", "seguiemj.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(chrome("surface")))
    app.setPalette(palette)
    stylesheet = Path(__file__).resolve().parents[1] / "src/ui/styles/dark.qss"
    app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="wct-navpane-render-") as scratch:
        engine = Operations(MemoryRegistry(), Path(scratch) / "journal", profile=scratch,
                            account_check=lambda: "", root_paths=[], notify=lambda: None)
        pane = NavPane(engine)
        entries = [NavEntry("{FE3197AB-09E3-49B4-84BA-AA194A0FDC32}", "Aplicații",
                            r"C:\Users\Owner\Aplicații", r"C:\Users\Owner\OneDrive\1 Personal\Aplicații",
                            r"C:\Users\Owner\Aplicații", kind=Kind.HANDMADE),
                   NavEntry("{018D5C66-4533-4307-9B53-224DE2ED1FE6}", "OneDrive — Personal", sort_order=66)]
        pane.set_context((entries, True, [("User profile", scratch)], "", engine.journal.read()))
        pane.path.setText(str(Path(__file__).resolve().parents[1]))
        pane.display_name.setText("Projects")
        pane.resize(1120, 1080)
        pane.show()
        app.processEvents()
        pane._validation_timer.stop()
        pane.validate_path()
        QThreadPool.globalInstance().waitForDone(10000)
        app.processEvents()
        destination = Path(tempfile.gettempdir()) / "navpane_shot.png"
        if not pane.grab().save(str(destination)):
            raise OSError("Could not save screenshot")
        pane.cancel()
        QThreadPool.globalInstance().waitForDone(10000)
        app.processEvents()
        pane.close()
        print(destination)


if __name__ == "__main__":
    main()
