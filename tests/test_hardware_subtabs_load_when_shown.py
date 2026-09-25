r"""Hardware Info's CPU/Memory/Storage/GPU/Network/BIOS tabs sat on
"Click Refresh to load." until each was clicked -- only the tab that was current
when the module opened ever loaded (seen 2026-09-25)."""
import pytest

from modules.hardware_inventory import hardware_module as hm


@pytest.fixture
def module(qapp, monkeypatch):
    loads = []
    monkeypatch.setattr(hm._LoadingTab, "_load",
                        lambda self: (loads.append(self._refresh_btn.parent() and id(self)),
                                      self._status.setText("Loading...")))
    module = hm.HardwareModule()
    module.app = type("App", (), {"thread_pool": None})()
    module._test_root = module.create_widget()      # the main window holds this
    return module, loads


def test_building_loads_nothing(module):
    _m, loads = module
    assert loads == []


def test_opening_loads_the_first_tab_only(module):
    mod, loads = module
    mod.on_activate()
    assert len(loads) == 1


def test_each_other_tab_loads_the_first_time_it_is_shown(module):
    mod, loads = module
    mod.on_activate()
    for i in range(1, mod._hw_tabs.count()):
        mod._hw_tabs.setCurrentIndex(i)
    assert len(loads) == mod._hw_tabs.count()


def test_going_back_to_a_loaded_tab_does_not_reload_it(module):
    mod, loads = module
    mod.on_activate()
    mod._hw_tabs.setCurrentIndex(2)
    mod._hw_tabs.setCurrentIndex(0)
    mod._hw_tabs.setCurrentIndex(2)
    assert len(loads) == 2
