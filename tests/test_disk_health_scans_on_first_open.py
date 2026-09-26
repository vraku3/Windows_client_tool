r"""Disk Health opened on "No drives scanned yet" and waited for a click.

Seen 2026-09-25, elevated. The tab's whole purpose is drive health, so it scans
the first time it is opened -- once, not on every visit.
"""
from modules.disk_health.disk_health_module import DiskHealthModule


from PyQt6.QtWidgets import QWidget
import pytest


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    return qapp


class _Widget(QWidget):
    """A real widget: the module guards with sip.isdeleted(), which rejects fakes."""

    def __init__(self):
        super().__init__()
        self.scans = 0
        self._report = None           # the module scans on activation while there is no report

    def refresh(self):
        self.scans += 1
        self._report = object()       # a scan produced a report: later visits must not rescan


def _module():
    module = DiskHealthModule()
    module._widget = _Widget()
    return module


def test_the_first_activation_scans():
    module = _module()
    module.on_activate()
    assert module._widget.scans == 1


def test_later_activations_do_not_rescan():
    module = _module()
    for _ in range(3):
        module.on_activate()
    assert module._widget.scans == 1


def test_activating_before_the_widget_exists_is_a_no_op_and_does_not_burn_the_first_load():
    module = DiskHealthModule()
    module.on_activate()                       # no widget yet
    module._widget = _Widget()
    module.on_activate()
    assert module._widget.scans == 1
