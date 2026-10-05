"""A module's widget is built when it is first shown, not at launch.

Building all 33 up front cost 1.78s of a 2.10s startup — measured against
0.17s to import every module package and 0.05s to stand up App. Widget
construction *was* the startup cost, and 32 of the 33 were for panes the
user was not looking at.

CompositeModule already builds its tabs this way; the trap it documents
applies here too, so a module's page is permanent and the real widget is
added into its layout. Never removeWidget/insertWidget on the current page:
that re-enters the handler that asked for the build.
"""
import tempfile
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from core.base_module import BaseModule


class _CountingModule(BaseModule):
    """Records how many times its widget was built."""

    icon = ""
    description = "test double"
    group = "TOOLS"

    def __init__(self, name: str = "Counter") -> None:
        super().__init__()
        self.name = name
        self.builds = 0
        self.activations = 0

    def create_widget(self) -> QWidget:
        self.builds += 1
        return QLabel(f"{self.name} built")

    def on_activate(self) -> None:
        self.activations += 1


class _ExplodingModule(_CountingModule):
    def create_widget(self) -> QWidget:
        self.builds += 1
        raise RuntimeError("this pane cannot be built")


@pytest.fixture
def window(qapp, monkeypatch):
    import ui.main_window as mw
    from app import App

    monkeypatch.setattr(mw, "is_admin", lambda: True)
    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    win = mw.MainWindow(app)
    yield win
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass


def test_registering_a_module_does_not_build_its_widget(window):
    second = _CountingModule("Second")
    window.register_module(_CountingModule("First"))
    window.register_module(second)

    assert second.builds == 0, "a pane nobody has opened must not be built"


def test_selecting_a_module_builds_it_once(window):
    module = _CountingModule()
    window.register_module(module)

    window._on_module_selected("Counter")
    assert module.builds == 1

    window._on_module_selected("Counter")
    assert module.builds == 1, "a second visit must reuse the widget"


def test_the_widget_is_reachable_after_it_is_built(window):
    """_navigate_to_module and the composite tab routing read
    _module_widgets; a lazily built widget has to land there too."""
    module = _CountingModule()
    window.register_module(module)
    window._on_module_selected("Counter")

    widget = window._module_widgets.get("Counter")
    assert widget is not None
    assert widget.parent() is not None, "it must be inside its page, not orphaned"


def test_the_first_module_is_built_by_the_time_the_window_is_shown(window):
    """The auto-selected first module must exist before the user sees the
    window, or the app opens on an empty pane."""
    module = _CountingModule()
    window.register_module(module)

    window.showEvent(None)

    assert module.builds == 1
    assert module.activations == 1


def test_a_module_that_cannot_be_built_says_so_instead_of_crashing(window):
    """create_widget() runs user-facing code that touches WMI, the registry
    and subprocesses. One pane failing must not take the window with it —
    it used to run during register_module, where nothing caught it."""
    module = _ExplodingModule("Broken")
    window.register_module(module)

    window._on_module_selected("Broken")

    widget = window._module_widgets.get("Broken")
    assert widget is not None
    assert "failed" in widget.text().lower()


def test_a_failed_build_is_not_retried_on_every_visit(window):
    module = _ExplodingModule("Broken")
    window.register_module(module)

    window._on_module_selected("Broken")
    window._on_module_selected("Broken")

    assert module.builds == 1


class _OpenPathModule(_CountingModule):
    """Records the path it was asked to jump to, like TreeSizeModule.open_path."""

    def __init__(self, name: str = "Pathable") -> None:
        super().__init__(name)
        self.opened_paths = []

    def open_path(self, path: str) -> None:
        self.opened_paths.append(path)


def test_navigate_to_module_forwards_a_path_to_open_path(window):
    """NavRequestData.path (Disk Space's folder-jump into TreeSize) must
    reach the resolved module's own open_path -- and only after the widget
    is built, since open_path on a real module (TreeSize) touches its
    widget."""
    module = _OpenPathModule()
    window.register_module(module)

    window._navigate_to_module("Pathable", "C:\\Users\\Someone\\Downloads")

    assert module.builds == 1, "open_path must run after the widget exists"
    assert module.opened_paths == ["C:\\Users\\Someone\\Downloads"]

    # No path given (every other existing caller) must not call it at all.
    window._navigate_to_module("Pathable")
    assert module.opened_paths == ["C:\\Users\\Someone\\Downloads"]


def test_treesize_module_open_path_starts_a_real_scan(qapp, tmp_path):
    """Real-machine assertion: TreeSizeModule.open_path on an actual folder on
    this disk must land in the shell's path combo and kick off a scan --
    exactly the jump Disk Space's folder rows rely on."""
    from modules.treesize.treesize_module import TreeSizeModule
    (tmp_path / "f").write_bytes(b"x")

    module = TreeSizeModule()
    module.on_start(SimpleNamespace(config=None))
    module.create_widget()

    module.open_path(str(tmp_path))

    assert module._shell.path_combo.currentText() == str(tmp_path)
    module.cancel_all_workers()


def test_treesize_open_path_during_a_scan_says_why_nothing_happened(qapp, tmp_path):
    """start_scan ignores a second request while one runs; open_path must
    say so in the status bar instead of leaving the double-click looking dead."""
    from modules.treesize.treesize_module import TreeSizeModule
    module = TreeSizeModule()
    module.on_start(SimpleNamespace(config=None))
    module.create_widget()
    module._shell._worker = object()          # a scan in flight
    try:
        module.open_path(str(tmp_path))
        assert str(tmp_path) in module._shell.status_bar._notice.text()
        assert module._shell.path_combo.currentText() != str(tmp_path)
    finally:
        module._shell._worker = None


def test_a_disabled_module_still_shows_its_admin_placeholder(window, monkeypatch):
    module = _CountingModule("NeedsAdmin")
    monkeypatch.setattr(
        type(window._app.module_registry), "disabled_modules",
        property(lambda self: [module]))

    window.register_module(module)
    window._on_module_selected("NeedsAdmin")

    assert module.builds == 0, "a disabled module's widget is never built"
    widget = window._module_widgets.get("NeedsAdmin")
    assert widget is not None and "administrator" in widget.text().lower()
