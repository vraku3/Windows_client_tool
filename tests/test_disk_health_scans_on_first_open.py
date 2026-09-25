r"""Disk Health opened on "No drives scanned yet" and waited for a click.

Seen 2026-09-25, elevated. The tab's whole purpose is drive health, so it scans
the first time it is opened -- once, not on every visit.
"""
from modules.disk_health.disk_health_module import DiskHealthModule


class _Widget:
    def __init__(self):
        self.scans = 0

    def _do_scan(self):
        self.scans += 1


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
