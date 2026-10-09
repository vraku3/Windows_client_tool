"""The Services tab, as a widget, plus its data-layer reuse.

Runs against the real machine: WMI's Win32_Service list is always there (a
service host refuses to expose nothing), so these tests assert structure
rather than exact contents. The scan needs a COM-initialised thread, so the
widget tests drive the real COMWorker path through a fake app's pool exactly
as the Startup-apps sibling tests do, and the data-layer test spawns its own
thread that does the COM init itself.

No service is ever started, stopped or restarted here: that needs elevation
and would disturb a real machine. The action path is exercised only for its
refusal -- a monkeypatched `service_action` returning (False, []) -- to pin
that the tab reports the failure it is given instead of inventing success.
"""
import threading
import time

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QLabel, QMessageBox

from modules.dashboard.services_tab import ServicesTab
from modules.services_manager import services_module

#: Every test in this file reads the live Win32_Service list through a
#: COM-initialised worker, as the module docstring above says. That is a
#: real dependency on the machine, not a fixture: the scan takes ~12s of
#: fixture setup and, when the service host is slow to answer, fails with
#: "the service table never filled from WMI" — a red suite for a reason
#: that has nothing to do with the code under test.
pytestmark = [pytest.mark.real_machine, pytest.mark.slow]

#: The states Win32_Service.State can report; anything a real machine sends
#: must be one of these (the tab substitutes "Unknown" for an empty read).
VALID_STATES = {"Running", "Stopped", "Paused", "Start Pending",
                "Stop Pending", "Continue Pending", "Pause Pending",
                "Unknown"}


class _FakeApp:
    """Enough of the app singleton for the tab: a real thread pool, so the
    WMI read runs on a COMWorker the way it does in the product."""

    def __init__(self):
        self.thread_pool = QThreadPool()


def _settle(qapp, widget, predicate, seconds=12.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _scanned(qapp, view):
    """Start a scan and pump until it lands (or times out gracefully)."""
    view.set_app(_FakeApp())
    view.start()
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0), \
        "the service table never filled from WMI"
    assert view._busy is False


# ---- the data layer, as a Qt-free WMI read ------------------------------

def test_the_wmi_services_scan_is_a_list_of_service_dicts():
    """get_services() must answer from the real machine, run on a thread
    that initialises COM itself so the test does not depend on a pool."""
    captured = {}

    def run():
        import pythoncom

        pythoncom.CoInitialize()
        try:
            captured["services"] = services_module.get_services()
        except Exception as exc:  # surfaced below, never swallowed silently
            captured["error"] = exc
        finally:
            pythoncom.CoUninitialize()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=60)
    assert not thread.is_alive(), "the WMI service scan did not finish"
    assert "error" not in captured, captured["error"]

    services = captured["services"]
    assert isinstance(services, list)
    assert len(services) >= 1, "a Windows machine always has services"
    for svc in services:
        assert isinstance(svc, dict)
        for key in ("Name", "Status", "PID", "Display Name", "Start Type"):
            assert key in svc, f"a service row is missing '{key}'"
        assert isinstance(svc["Name"], str) and svc["Name"]
        assert isinstance(svc["Status"], str)
        assert isinstance(svc["PID"], str)


# ---- the widget ---------------------------------------------------------

@pytest.fixture
def tab(qapp):
    view = ServicesTab()
    _scanned(qapp, view)
    yield view
    view.stop()
    view.deleteLater()


def test_the_table_fills_with_the_machines_services(tab):
    assert tab._table.rowCount() > 10, "the machine runs more than 10 services"


def test_every_row_names_its_service(tab):
    """No row may be a silent blank; every service must carry a Name and a
    Display Name, and a status that is a real WMI state word."""
    for row in range(tab._table.rowCount()):
        assert tab._table.item(row, 1).text(), f"row {row} has no name"
        assert tab._table.item(row, 2).text(), \
            f"row {row} has no display name"
        status = tab._table.item(row, 0).text()
        assert status in VALID_STATES, f"row {row} status is {status!r}"


def test_the_status_column_uses_the_semantic_colours(tab):
    """Running rows are painted the theme's success colour, Stopped its
    error colour -- never a raw colour of the tab's own invention."""
    from PyQt6.QtGui import QColor

    from core.semantic_colors import semantic

    running_fg = None
    stopped_fg = None
    for row in range(tab._table.rowCount()):
        text = tab._table.item(row, 0).text()
        colour = tab._table.item(row, 0).foreground().color()
        if text == "Running":
            running_fg = colour
        elif text == "Stopped":
            stopped_fg = colour
    if running_fg is not None:
        assert running_fg == QColor(semantic("success"))
    if stopped_fg is not None:
        assert stopped_fg == QColor(semantic("error"))
    assert running_fg is not None and stopped_fg is not None, \
        "the machine must have both running and stopped services"


def test_the_header_note_explains_elevation(tab):
    for child in tab.findChildren(QLabel):
        if "Administrator" in child.text():
            return
    raise AssertionError("the note must say actions need Administrator")


def test_the_context_menu_offers_the_actions(tab):
    """Right-clicking a row offers start/stop/restart and go-to-process."""
    svc = tab._services[0]
    menu = tab._menu_for(svc)
    labels = [action.text() for action in menu.actions()]
    for expected in ("Start service", "Stop service", "Restart service",
                     "Go to process"):
        assert expected in labels, f"{expected} is missing from the menu"


def test_go_to_process_is_enabled_for_a_running_service(tab):
    running = next((svc for svc in tab._services
                    if str(svc.get("PID") or "").strip().isdigit()
                    and int(svc.get("PID") or 0) > 0), None)
    assert running is not None, "no running service with a pid was found"
    pid = int(running["PID"])

    menu = tab._menu_for(running)
    go = next(action for action in menu.actions()
              if action.text().startswith("Go to process"))
    assert go.isEnabled(), "go to process should work for a running service"
    assert go.text() == f"Go to process (PID {pid})"


def test_go_to_process_is_disabled_without_a_pid(tab):
    stopped = next((svc for svc in tab._services
                    if not str(svc.get("PID") or "").strip()), None)
    assert stopped is not None, "no stopped service was found"

    menu = tab._menu_for(stopped)
    go = next(action for action in menu.actions()
              if action.text().startswith("Go to process"))
    assert not go.isEnabled(), \
        "go to process must be disabled for a service with no pid"


def test_go_to_process_emits_the_pid(qapp):
    view = ServicesTab()
    _scanned(qapp, view)
    try:
        running = next((svc for svc in view._services
                        if str(svc.get("PID") or "").isdigit()
                        and int(svc.get("PID") or 0) > 0), None)
        if running is None:
            return  # nothing running to ask for -- the tab is still honest
        pid = int(running["PID"])
        got = []
        view.goto_process.connect(got.append)
        view._go_to_process(running, pid)
        assert got == [pid]
    finally:
        view.stop()
        view.deleteLater()


# ---- the action refusal path --------------------------------------------

def test_a_refused_service_action_is_reported_not_swallowed(tab, qapp,
                                                            monkeypatch):
    """service_action returns (False, []) -> the tab must say so. The real
    OS path raises instead (access denied unelevated) and lands on the error
    signal; both must reach the user, never a silent empty row."""
    shown = []

    def fake_exec(self):
        shown.append(self.text())
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(services_module, "service_action",
                        lambda name, action, check_dependents=False:
                        (False, []))

    svc = tab._services[0]
    tab._confirmed_action("start", svc)

    # The worker result arrives on the pool; pump until the tab surfaces it.
    assert _settle(qapp, tab,
                   lambda: any("Could not start" in text
                               for text in shown)), \
        f"the refusal was not surfaced; saw {shown!r}"


def test_declining_the_confirmation_runs_no_action(tab, monkeypatch):
    """A No on the confirm dialog must stop the whole action."""
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.No)
    calls = []
    monkeypatch.setattr(services_module, "service_action",
                        lambda name, action, check_dependents=False:
                        calls.append((name, action)) or (True, []))

    svc = tab._services[0]
    tab._confirmed_action("stop", svc)

    assert not calls, "an action ran after the confirmation was declined"


def test_the_next_read_waits_for_the_last_one_to_finish(qapp):
    """A Win32_Service read takes ~4.2 s here; a 5 s interval timer kept WMI
    busy ~85% of the time and, under load, started the next read the moment
    the last one landed. The timer is single-shot, armed only by a result."""
    from modules.dashboard import services_tab as st
    view = ServicesTab()
    try:
        view.set_app(_FakeApp())
        view.start()
        assert view._timer.isSingleShot()
        assert not view._timer.isActive(), "armed before the first read finished"
        assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
        assert view._timer.isActive() and view._timer.interval() == st.REFRESH_MS
        view.stop()
        assert not view._timer.isActive()
    finally:
        view.stop()
