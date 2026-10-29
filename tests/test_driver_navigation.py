"""Updates -> Driver Manager navigation carries a filter, not just a jump."""
from types import SimpleNamespace

from core.events import NavRequestData
from modules.driver_manager.driver_module import DriverModule


class _Edit:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


def test_nav_payload_is_optional_and_defaults_to_none():
    assert NavRequestData("Updates").payload is None
    assert NavRequestData("Updates", payload={"filter": "Display"}).payload == {"filter": "Display"}


def test_receive_navigation_stores_a_filter_before_the_widget_exists():
    module = DriverModule()
    module.receive_navigation({"filter": "Intel Corporation"})
    assert module._pending_nav_filter == "Intel Corporation"


def test_receive_navigation_applies_the_filter_when_built(qapp):
    module = DriverModule()
    module._filter_edit = _Edit()
    module._status_lbl = SimpleNamespace(setText=lambda text: setattr(
        module, "_status_text", text))
    module.receive_navigation({"filter": "Display"})
    assert module._filter_edit.text == "Display"
    assert module._status_text == "Filtered to Display from Updates."


def test_empty_payload_changes_nothing(qapp):
    module = DriverModule()
    module._filter_edit = _Edit()
    module.receive_navigation({})
    module.receive_navigation(None)
    assert module._filter_edit.text == ""
