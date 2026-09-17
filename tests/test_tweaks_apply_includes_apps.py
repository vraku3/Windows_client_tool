"""Apply Selected has to include what is checked in the Apps tab.

`_on_apply` collected `self._tab_widgets.values()`, and the Apps tab is added
to the QTabWidget but never put into `_tab_widgets` -- so whatever was checked
there was invisible to the only button most people press, and the answer was
"Check at least one tweak to apply."
"""
import pytest

from modules.tweaks.tweaks_module import TweaksModule


class _Backup:
    def __init__(self):
        self.points = []

    def create_restore_point(self, label, module):
        self.points.append((label, module))
        return "rp-1"

    def record_steps(self, *a, **k):
        pass

    def backup_registry_key(self, *a, **k):
        pass

    def backup_appx_package(self, *a, **k):
        pass


class _Config:
    def get(self, key, default=None):
        return default

    def set(self, *a, **k):
        pass

    def save(self):
        pass


class _Pool:
    def __init__(self):
        self.started = []

    def start(self, worker):
        self.started.append(worker)


class _App:
    def __init__(self):
        self.backup = _Backup()
        self.config = _Config()
        self.thread_pool = _Pool()
        self.event_bus = None


@pytest.fixture
def module(qapp):
    mod = TweaksModule()
    mod.on_start(_App())
    mod._held = mod.create_widget()
    # No modal dialogs: a .exec() from a handler waits for a click that is
    # never coming.
    mod._confirm_app_changes = lambda changes: True
    mod._warn_nothing_selected = lambda: mod._warned.append(1)
    mod._warned = []
    yield mod
    mod.on_stop()


def _queue_an_app_removal(module, package="Microsoft.BingSearch"):
    module._app_tab.populate_installed({package})
    from PyQt6.QtCore import Qt
    item = module._app_tab._installed_list.item(0)
    item.setCheckState(Qt.CheckState.Checked)
    return package


def test_apply_with_nothing_checked_anywhere_still_says_so(module):
    module._on_apply()
    assert module._warned == [1]
    assert module.app.thread_pool.started == []


def test_a_queued_app_removal_is_enough_to_apply(module):
    """This is the reported bug: checked in Apps, and Apply Selected said
    nothing was selected."""
    _queue_an_app_removal(module)
    module._on_apply()
    assert module._warned == [], "it refused work that was actually queued"
    assert len(module.app.thread_pool.started) == 1


def test_the_apps_tabs_own_button_applies_too(module):
    """It was connected to nothing at all."""
    _queue_an_app_removal(module)
    module._app_tab.apply_requested.emit()
    assert len(module.app.thread_pool.started) == 1


def test_the_work_reaches_the_catalog(module):
    """`install_app` and `remove_appx` were implemented and never called."""
    removed, installed = [], []
    module._catalog.remove_appx = lambda pkg, on_output=None: removed.append(pkg) or True
    module._catalog.install_app = lambda wid, on_output=None, is_cancelled=None: installed.append(wid) or True

    _queue_an_app_removal(module, "Microsoft.ZuneMusic")
    module._app_tab._install_queue.add("VideoLAN.VLC")
    module._on_apply()

    worker = module.app.thread_pool.started[0]
    worker.run()
    assert removed == ["Microsoft.ZuneMusic"]
    assert installed == ["VideoLAN.VLC"]


def test_a_removal_that_failed_is_reported_not_swallowed(module):
    module._catalog.remove_appx = lambda pkg, on_output=None: False
    _queue_an_app_removal(module, "Microsoft.ZuneMusic")
    module._on_apply()
    worker = module.app.thread_pool.started[0]
    reported = []
    worker.signals.result.connect(reported.append)   # run() reports by signal
    worker.run()
    text = " ".join(str(e) for e in (reported[0] if reported else []))
    assert "Microsoft.ZuneMusic" in text


def test_a_restore_point_is_taken_before_touching_anything(module):
    _queue_an_app_removal(module)
    module._on_apply()
    assert module.app.backup.points, "no way back was recorded"


def test_destructive_app_changes_are_confirmed_first(module):
    """Removing somebody's applications is not a thing to do on one click."""
    asked = []
    module._confirm_app_changes = lambda changes: asked.append(changes) or False
    _queue_an_app_removal(module)
    module._on_apply()
    assert asked and asked[0]["remove"] == ["Microsoft.BingSearch"]
    assert module.app.thread_pool.started == [], "declined, but it ran anyway"


def test_the_queue_is_cleared_once_the_work_is_done(module):
    module._catalog.remove_appx = lambda pkg, on_output=None: True
    _queue_an_app_removal(module)
    module._on_apply()
    module.app.thread_pool.started[0].run()
    module._on_apply_result([])
    assert not module._app_tab.has_queued_changes()


# --- one apply at a time ----------------------------------------------------
#
# Reported: "could not install Mozilla.Firefox" -- while Firefox was in fact
# installing, and did install. The log shows two overlapping runs 12 seconds
# apart; winget will not run twice at once, so the second failed and reported
# a failure for work the first was busy completing successfully.

def test_a_second_apply_while_one_is_running_starts_nothing(module):
    _queue_an_app_removal(module)
    module._on_apply()
    module._on_apply()
    module._on_apply()
    assert len(module.app.thread_pool.started) == 1


def test_the_apps_tabs_button_is_disabled_while_applying_too(module):
    """The bottom bar's button was disabled and this one was not, so it was
    the way back in to a second run."""
    _queue_an_app_removal(module)
    module._on_apply()
    assert not module._apply_btn.isEnabled()
    assert not module._app_tab._apply_btn.isEnabled()


def test_a_finished_run_lets_the_next_one_start(module):
    """Counted as a delta: _on_apply_result also kicks off a status sweep, so
    the absolute number of started workers is not just the applies."""
    _queue_an_app_removal(module)
    module._on_apply()
    module._on_apply_result([])
    before = len(module.app.thread_pool.started)
    _queue_an_app_removal(module)
    module._on_apply()
    assert len(module.app.thread_pool.started) == before + 1


# --- desktop apps go to winget, not to Remove-AppxPackage -------------------

def _queue_a_desktop_removal(module, app_id="JAMSoftware.TreeSize"):
    from modules.tweaks.app_catalog import WingetApp
    from PyQt6.QtCore import Qt
    module._app_tab.populate_installed_desktop(
        [WingetApp("TreeSize V9.8.2", app_id, "9.8.2", "winget")])
    module._app_tab._desktop_list.item(0).setCheckState(Qt.CheckState.Checked)
    return app_id


def test_a_desktop_app_is_uninstalled_through_winget(module):
    """`remove_app_winget` was implemented and no UI path ever called it."""
    winget_removals, appx_removals = [], []
    module._catalog.remove_app_winget = (
        lambda app_id, on_output=None, is_cancelled=None: winget_removals.append(app_id) or True)
    module._catalog.remove_appx = (
        lambda pkg, on_output=None: appx_removals.append(pkg) or True)

    _queue_a_desktop_removal(module)
    module._on_apply()
    module.app.thread_pool.started[0].run()

    assert winget_removals == ["JAMSoftware.TreeSize"]
    assert appx_removals == [], "a Win32 app was sent to Remove-AppxPackage"


def test_a_desktop_removal_alone_is_enough_to_apply(module):
    _queue_a_desktop_removal(module)
    module._on_apply()
    assert module._warned == []
    assert len(module.app.thread_pool.started) == 1


def test_a_failed_desktop_removal_is_reported(module):
    module._catalog.remove_app_winget = lambda app_id, on_output=None, is_cancelled=None: False
    _queue_a_desktop_removal(module)
    module._on_apply()
    worker = module.app.thread_pool.started[0]
    reported = []
    worker.signals.result.connect(reported.append)
    worker.run()
    text = " ".join(str(e) for e in (reported[0] if reported else []))
    assert "JAMSoftware.TreeSize" in text


def test_the_confirmation_lists_the_desktop_apps_too(module):
    """Uninstalling somebody's programs is not a thing to do unannounced."""
    asked = []
    module._confirm_app_changes = lambda changes: asked.append(changes) or False
    _queue_a_desktop_removal(module)
    module._on_apply()
    assert asked and asked[0]["remove_winget"] == ["JAMSoftware.TreeSize"]
    assert module.app.thread_pool.started == []


# --- Performance Tuner discoverability nudge --------------------------------
#
# Performance Tuner was retired; its functionality lives on as the
# Performance preset here. Someone who used to reach for that module needs a
# pointer to where it went.

def test_the_nudge_is_hidden_when_the_performance_preset_is_selected(module):
    module._held.show()  # isVisible() reflects the whole ancestor chain
    module._preset_combo.setCurrentText("Performance")
    assert not module._perf_tuner_nudge_lbl.isVisible()
    module._held.hide()  # don't leave a shown top-level window for the rest of the process


def test_the_nudge_is_visible_for_any_other_preset(module):
    module._held.show()  # isVisible() reflects the whole ancestor chain
    module._preset_combo.setCurrentText("Balanced")
    assert module._perf_tuner_nudge_lbl.isVisible()
    module._held.hide()  # don't leave a shown top-level window for the rest of the process
