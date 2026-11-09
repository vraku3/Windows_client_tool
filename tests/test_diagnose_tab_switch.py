import os
import sys
import unittest

from PyQt6.QtWidgets import QApplication

# Ensure offscreen platform for headless Qt test
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from app import App
from modules.diagnose.diagnose_module import DiagnoseModule


class TestDiagnoseModuleSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication(sys.argv)

    def test_diagnose_create_and_activate(self):
        app = App.instance or App()
        diag = DiagnoseModule()
        diag.on_start(app)
        widget = diag.create_widget()
        self.assertIsNotNone(widget)

        # Activate the module (simulates user clicking 'Diagnose' sidebar entry)
        diag.on_activate()

        # Check all child tabs
        self.assertEqual(len(diag.children), 7)
        self.assertEqual(diag.children[0].name, "Event Viewer")
        self.assertEqual(diag.children[5].name, "Crash Dumps")
        self.assertEqual(diag.children[6].name, "Timeline")

        class DummySignals:
            class DummySig:
                def emit(self, val): pass
            progress = DummySig()
        class DummyWorker:
            signals = DummySignals()

        # Test load_entries for each child module
        worker = DummyWorker()
        for idx, child in enumerate(diag.children):
            # Select tab
            diag._tab_widget.setCurrentIndex(idx)
            child.on_activate()
            if hasattr(child, "load_entries"):
                from core.types import LogReadResult
                entries = child.load_entries(worker)
                self.assertTrue(isinstance(entries, (list, LogReadResult)))
            child.on_deactivate()

        # Deactivate
        diag.on_deactivate()


if __name__ == "__main__":
    unittest.main()
