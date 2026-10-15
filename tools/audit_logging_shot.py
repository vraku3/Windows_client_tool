"""Off-screen screenshot of the Security Dashboard / Startup tabs.

    python tools/audit_logging_shot.py <out_dir>
Runs against a THROWAWAY app-data dir; read-only.
"""
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))


def pump(qapp, ms):
    from PyQt6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def find_tabs(widget):
    from PyQt6.QtWidgets import QTabWidget
    return widget.findChildren(QTabWidget)


def main() -> int:
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    from PyQt6.QtWidgets import QApplication
    qapp = QApplication(sys.argv)
    from app import App
    from main import register_all_modules
    from ui.main_window import MainWindow
    app = App(app_data_dir=tempfile.mkdtemp())
    register_all_modules(app)
    app.start()
    win = MainWindow(app)
    for m in app.module_registry.modules:
        win.register_module(m)
    win.resize(1500, 950)
    win.move(-20000, 0)
    win.show()
    pump(qapp, 1500)

    def shoot(module_name, tab_text, fname):
        win._on_module_selected(module_name)
        pump(qapp, 1500)
        page = win._module_pages[module_name]
        for tw in find_tabs(page):
            for i in range(tw.count()):
                if tw.tabText(i).replace("&&", "&") == tab_text:
                    tw.setCurrentIndex(i)
        pump(qapp, 6000)
        path = os.path.join(out, fname)
        print(win.grab().save(path), path)

    shoot("Security Dashboard", "Audit & Logging", "audit_logging_tab.png")
    try:
        from PyQt6.QtWidgets import QLineEdit
        win._on_module_selected("Startup & Boot")
        pump(qapp, 4000)
        for e in win._module_pages["Startup & Boot"].findChildren(QLineEdit):
            if "Search" in e.placeholderText() or "search" in e.placeholderText():
                e.setText("Active Setup")
        pump(qapp, 1500)
        print(win.grab().save(os.path.join(out, "startup_persistence.png")))
    except Exception as exc:  # report, do not hide
        print("startup shot failed:", exc)
    app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
