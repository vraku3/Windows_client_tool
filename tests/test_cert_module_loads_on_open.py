r"""Opening Certificates must show certificates, not an empty table.

Found 2026-09-24 sweeping every tab against the real machine: the module's
on_activate() was `pass`, and MainWindow's refresh timer first ticks a full
interval (60s) after a tab is opened, so the list stayed empty until then or
until someone clicked Refresh. 110 certificates appeared the moment
refresh_data() ran.
"""
import pytest

from modules.certificate_viewer import cert_module as cm


class _Pool:
    def start(self, worker):
        pass                                     # never actually runs the read


@pytest.fixture
def module(qapp, monkeypatch):
    loads = []
    monkeypatch.setattr(cm._CertTab, "_load",
                        lambda self: (setattr(self, "_loaded_once", True),
                                      loads.append(self._store_name)))
    module = cm.CertModule()
    module.app = type("App", (), {"thread_pool": _Pool()})()
    module._test_root = module.create_widget()   # MainWindow holds this in the app
    return module, loads


def test_building_the_widget_loads_nothing(module):
    _module, loads = module
    assert loads == []


def test_activating_loads_the_visible_store(module):
    mod, loads = module
    mod.on_activate()
    assert len(loads) == 1


def test_activating_twice_does_not_reload(module):
    mod, loads = module
    mod.on_activate()
    mod.on_activate()
    assert len(loads) == 1


def test_another_store_loads_the_first_time_it_is_opened(module):
    mod, loads = module
    mod.on_activate()
    mod._tabs.setCurrentIndex(1)
    assert len(loads) == 2
    mod._tabs.setCurrentIndex(0)
    mod._tabs.setCurrentIndex(1)
    assert len(loads) == 2                       # already loaded, not again


def test_the_timer_refresh_still_reloads_every_store(module):
    mod, loads = module
    mod.on_activate()
    loads.clear()
    mod.refresh_data()
    assert len(loads) == mod._tabs.count()
