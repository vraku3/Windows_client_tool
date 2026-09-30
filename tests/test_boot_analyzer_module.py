"""BootAnalyzerModule's real worker lifecycle, including the boot-history
fallback wired into it.

Runs the module's actual on_start -> create_widget -> on_activate sequence
against a real QThreadPool, the same pattern test_startup_tab.py uses, so
`_load_info`'s worker really runs and `_display_info` really fires.
"""
import time

from PyQt6.QtCore import QThreadPool

from core.admin_utils import is_admin
from modules.boot_analyzer.boot_analyzer_module import BootAnalyzerModule


class _FakeApp:
    def __init__(self):
        self.thread_pool = QThreadPool()


def _settle(qapp, predicate, seconds=15.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_boot_analyzer_loads_against_the_real_machine(qapp):
    module = BootAnalyzerModule()
    module.on_start(_FakeApp())
    widget = module.create_widget()
    try:
        module.on_activate()
        settled = _settle(qapp, lambda: not module._scanning)
        assert settled, "boot analysis never completed"
        assert module._info_cards.count() > 0

        panel = module._history_panel
        # Unelevated on this real machine, the Performance log is refused
        # (see tests/test_boot_history.py's test_real_logs_are_plausible)
        # and the System-log fallback must be what fills the panel instead
        # of the primary boots table -- both dark at once would mean the
        # fallback wiring silently did nothing.
        if not is_admin():
            if panel._boots.rowCount() == 0:
                assert panel._fallback.rowCount() > 0, (
                    "primary boot table empty and the fallback table is "
                    "also empty -- boot history is showing nothing at all")
                assert not panel._fallback.isHidden()
    finally:
        module.on_stop()
        widget.deleteLater()
