"""Construction smoke test: every module registered in main.py must survive
on_start(app) followed by create_widget() without raising — the same order
BaseModule's lifecycle contract requires in real use (see CLAUDE.md).

This bypasses ModuleRegistry.start_all()'s admin gating on purpose: that gate
only decides whether a module gets *activated* in the real app, and about
half of these modules are requires_admin=True, so gating here would silently
skip testing them on every unelevated CI run. The goal is narrower — catch
plain construction bugs (NameError, AttributeError, bad imports) in every
module's create_widget(), regardless of admin state.

Module list is built at collection time (parametrize needs it), but the real
App() must NOT be — constructing it (QThreadPool.globalInstance() specifically)
during pytest's collection phase reliably comes back with a None thread pool
in this codebase's import graph; building it inside a fixture at test-execution
time instead does not. So collection only builds a throwaway module_registry
stub (no QThreadPool involved) to get the class list/ids; the real App used
in each test is built lazily by the `app_instance` fixture below.
"""
import sys
import tempfile

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

# Needed at collection time (parametrize below needs the module class list),
# which runs before pytest fixtures — including conftest's session qapp
# fixture — so make sure one exists here too. Idempotent: QApplication is
# itself a singleton, and .instance() returns the existing one if any.
QApplication.instance() or QApplication(sys.argv)

from core.module_registry import ModuleRegistry  # noqa: E402
from main import register_all_modules  # noqa: E402


class _RegistryOnlyStub:
    """Stands in for App during collection-time registration — module
    __init__()/register() need nothing beyond `.module_registry`, and this
    avoids constructing a real App (and its QThreadPool) at collection time."""

    def __init__(self):
        self.module_registry = ModuleRegistry()


_stub = _RegistryOnlyStub()
register_all_modules(_stub)
_MODULE_CLASSES = [type(m) for m in _stub.module_registry.modules]


@pytest.fixture(scope="module")
def app_instance():
    from app import App

    tmpdir = tempfile.mkdtemp()
    App.instance = None  # in case an earlier test left the singleton set
    app = App(app_data_dir=tmpdir)
    yield app
    try:
        app.shutdown()
    except Exception:
        pass
    App.instance = None


def test_registers_a_substantial_module_set():
    # Loose floor, not an exact count — just catches "registration silently
    # broke and nothing came back" without hardcoding the current total.
    # Lowered from 30 to 25 by the Management + Network consolidation's
    # Task 2, which folded two more sidebar entries into an existing
    # composite (32 -> 30 -> 28 registered modules); see
    # tests/test_module_inventory.py's test_the_sidebar_is_28_entries for
    # the exact count and its history.
    assert len(_MODULE_CLASSES) >= 25


@pytest.mark.parametrize("module_cls", _MODULE_CLASSES, ids=[c.__name__ for c in _MODULE_CLASSES])
def test_module_on_start_and_create_widget_survive(app_instance, module_cls):
    module = module_cls()
    try:
        module.on_start(app_instance)
        widget = module.create_widget()
    except Exception as e:
        pytest.fail(f"{module_cls.__name__}: on_start()/create_widget() raised {e!r}")
    assert isinstance(widget, QWidget), f"{module_cls.__name__}: create_widget() did not return a QWidget"
    try:
        module.on_stop()
    except Exception as e:
        pytest.fail(f"{module_cls.__name__}: on_stop() raised {e!r}")
