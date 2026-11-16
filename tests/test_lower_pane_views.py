"""The DLLs and Handles tabs of the lower pane.

Both used to show an EMPTY TABLE when they could not read a process, and
in the handle view's case for every process on the machine. An empty table
is not a neutral state here: "no modules" and "no handles" are both
impossible for a running process, so a blank one reads as an answer while
being the absence of one. These tests are mostly about what the panes say
when they cannot say anything else.
"""
import os
import time


from modules.process_explorer.lower_pane.dll_view import DllView
from modules.process_explorer.lower_pane.handle_view import HandleView

MY_PID = os.getpid()


def _settle(qapp, widget, predicate, seconds=8.0):
    """Pump the loop until the background read lands."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ---- DLLs ---------------------------------------------------------------

def test_the_dll_pane_lists_our_modules(qapp):
    view = DllView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    assert "modules" in view._label.text()


def test_the_dll_pane_fills_size_company_and_version(qapp):
    """All three columns existed before and were always blank or zero --
    a column of zeros that looked like a measurement."""
    view = DllView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    filled = 0
    for row in range(view._table.rowCount()):
        company = view._table.item(row, 2).text()
        version = view._table.item(row, 3).text()
        size = view._table.item(row, 5).text()
        assert size != "—", "every loaded module has a size"
        if company != "—" and version != "—":
            filled += 1
    assert filled > view._table.rowCount() * 0.5


def test_the_dll_pane_counts_signed_modules(qapp):
    """The Signed column's whole reason to exist: a machine loads the same
    hundred system DLLs in every process, so most of our modules should
    verify as signed -- and the label must say how many did."""
    view = DllView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    signed = sum(1 for row in range(view._table.rowCount())
                 if view._table.item(row, 7).text() == "Yes")
    assert signed > view._table.rowCount() * 0.5, \
        "most system DLLs are Authenticode-signed"
    assert "signed" in view._label.text()


def test_the_signed_column_only_claims_what_the_engine_said(qapp):
    """The renderer maps the engine's statuses to Yes/No/Invalid and
    anything else to a dash -- it must never invent a claim of its own."""
    from core.procengine.signatures import clear_cache

    clear_cache()
    view = DllView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    for row in range(view._table.rowCount()):
        assert view._table.item(row, 7).text() in ("Yes", "No", "Invalid", "—")


def test_a_process_it_cannot_read_says_why_rather_than_showing_nothing(qapp):
    view = DllView()
    view.load_pid(4)          # the kernel; refuses
    assert _settle(qapp, view, lambda: "could not be read" in view._label.text())
    assert view._table.rowCount() == 0
    assert "elevated" in view._label.text()


def test_the_refusal_message_is_not_double_punctuated(qapp):
    """Win32 reasons already end in a full stop."""
    view = DllView()
    view.load_pid(4)
    assert _settle(qapp, view, lambda: "could not be read" in view._label.text())
    assert ".." not in view._label.text()


def test_a_stale_result_is_not_painted_over_a_new_selection(qapp):
    view = DllView()
    view.load_pid(MY_PID)
    view.load_pid(4)
    assert _settle(qapp, view, lambda: "could not be read" in view._label.text())
    assert view._table.rowCount() == 0


# ---- Handles ------------------------------------------------------------

def test_the_handle_pane_lists_our_handles(qapp):
    """The bug this replaces showed zero handles for every process on the
    machine, always -- the kernel said 185 and the pane said 0."""
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    assert view._table.rowCount() > 10


def test_handles_show_a_type_name_not_a_type_index(qapp):
    """It used to print the raw ObjectTypeIndex -- "15" rather than
    "File"."""
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    types = {view._table.item(row, 0).text()
             for row in range(view._table.rowCount())}
    assert not all(kind.isdigit() for kind in types)
    assert types & {"File", "Key", "Event", "Section", "Mutant"}


def test_some_handles_are_named(qapp):
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    names = [view._table.item(row, 1).text()
             for row in range(view._table.rowCount())]
    assert any("\\" in name for name in names)


def test_an_unnamed_handle_explains_itself_rather_than_being_blank(qapp):
    """Most kernel objects genuinely have no name. A blank cell says
    "this object is called nothing", which is a different claim."""
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    for row in range(view._table.rowCount()):
        text = view._table.item(row, 1).text()
        assert text.strip() != "", f"row {row} left the name blank"
        if text.startswith("—"):
            assert len(text) > 2, "a dash with no reason beside it"


def test_the_summary_counts_what_could_not_be_read(qapp):
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    assert "handles" in view._label.text()
    assert "named" in view._label.text()


def test_the_access_mask_is_decoded(qapp):
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)

    access = [view._table.item(row, 4).text()
              for row in range(view._table.rowCount())]
    assert any("SYNCHRONIZE" in text or "READ_CONTROL" in text
               for text in access)


def test_cancelling_drops_a_result_that_arrives_late(qapp):
    view = HandleView()
    view.load_pid(MY_PID)
    view.cancel()
    qapp.processEvents()
    time.sleep(0.5)
    qapp.processEvents()
    assert view._table.rowCount() == 0


def test_handle_view_filters_rows_by_query(qapp):
    view = HandleView()
    view.load_pid(MY_PID)
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    total = view._table.rowCount()
    # Query something improbable
    view._search.setText("___improbable_query_xyz___")
    assert view._table.rowCount() == 0
    # Clear filter
    view._search.clear()
    assert view._table.rowCount() == total
