"""The System Restore pane's two new delete buttons, driven through the real widgets.

Renders the actual pane rather than asserting on helper functions: the bugs
this catches (a button that never enables, a sequence number that never
reaches the row) only exist in the wiring.
"""
from datetime import datetime, timezone

import pytest

from modules.restore_manager import restore_module as rm


def _local_text(y, mo, d, h, mi):
    """A DMTF '-000' stamp is UTC; the pane shows it in local time."""
    utc = datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


def _pt(seq, when, desc):
    return {
        "SequenceNumber": seq,
        "Description": desc,
        "RestorePointType": 0,
        "CreationTime": when,
    }


FOUR_POINTS = [
    _pt(11, "20260820100000.000000-000", "Before driver install"),
    _pt(12, "20260821100000.000000-000", "Windows Update"),
    _pt(13, "20260822100000.000000-000", "Windows Client Tool Restore Point"),
    _pt(14, "20260823100000.000000-000", "Before tweak sweep"),
]


class _AppStub:
    thread_pool = None


@pytest.fixture
def pane(qapp, monkeypatch):
    monkeypatch.setattr(rm, "is_admin", lambda: True)   # deletion is gated on elevation
    module = rm.RestoreManagerModule()
    module.on_start(_AppStub())
    module.create_widget()
    return module


def _select_row(pane, row):
    pane._table.clearSelection()
    pane._table.selectRow(row)


# ── initial state ───────────────────────────────────────────────────────────

def test_both_delete_buttons_start_disabled(pane):
    assert pane._delete_btn.isEnabled() is False
    assert pane._prune_btn.isEnabled() is False


# ── table population ────────────────────────────────────────────────────────

def test_four_points_render_with_their_sequence_numbers_attached(pane):
    pane._on_points_loaded(FOUR_POINTS)

    assert pane._table.rowCount() == 4
    assert pane._table.item(0, 0).text() == "Before driver install"
    assert pane._table.item(0, 1).text() == _local_text(2026, 8, 20, 10, 0)
    assert [pane._sequence_number_at(r) for r in range(4)] == [11, 12, 13, 14]


def test_keep_only_latest_enables_once_there_is_something_older(pane):
    pane._on_points_loaded(FOUR_POINTS)
    assert pane._prune_btn.isEnabled() is True


def test_keep_only_latest_stays_disabled_with_a_single_point(pane):
    pane._on_points_loaded(FOUR_POINTS[:1])
    assert pane._prune_btn.isEnabled() is False


def test_keep_only_latest_stays_disabled_with_no_points(pane):
    pane._on_points_loaded([])
    assert pane._prune_btn.isEnabled() is False


def test_a_point_with_no_sequence_number_still_shows_but_cannot_be_deleted(pane):
    pane._on_points_loaded([_pt(None, "20260820100000.000000-000", "Mystery point")])

    assert pane._table.rowCount() == 1
    assert pane._sequence_number_at(0) is None
    _select_row(pane, 0)
    assert pane._delete_btn.isEnabled() is False


def test_unparseable_creation_time_does_not_lose_the_row(pane):
    pane._on_points_loaded([_pt(1, "garbage", "Odd point")])
    assert pane._table.rowCount() == 1
    assert pane._table.item(0, 1).text() == "garbage"


# ── selection ───────────────────────────────────────────────────────────────

def test_selecting_a_row_enables_delete_selected(pane):
    pane._on_points_loaded(FOUR_POINTS)
    _select_row(pane, 1)
    assert pane._delete_btn.isEnabled() is True

    pane._table.clearSelection()
    assert pane._delete_btn.isEnabled() is False


# ── what actually gets deleted ──────────────────────────────────────────────

def test_delete_selected_passes_exactly_the_selected_sequence_numbers(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS)
    _select_row(pane, 2)

    monkeypatch.setattr(rm, "confirm_destructive", lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(pane, "_start_delete", sent.append)

    pane._delete_selected()
    assert sent == [[13]]


def test_delete_selected_deletes_nothing_when_the_dialog_is_declined(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS)
    _select_row(pane, 2)

    monkeypatch.setattr(rm, "confirm_destructive", lambda *a, **k: False)
    monkeypatch.setattr(
        pane, "_start_delete", lambda seqs: pytest.fail("declined but deleted anyway")
    )
    pane._delete_selected()


def test_keep_only_latest_spares_the_newest_point(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS)

    monkeypatch.setattr(rm, "confirm_destructive", lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(pane, "_start_delete", sent.append)

    pane._delete_all_but_latest()
    assert len(sent) == 1
    assert sorted(sent[0]) == [11, 12, 13]      # 14 is the newest, kept


def test_keep_only_latest_never_asks_when_there_is_one_point(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS[:1])
    monkeypatch.setattr(
        rm.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        rm, "confirm_destructive",
        lambda *a, **k: pytest.fail("asked to delete the only restore point"),
    )
    pane._delete_all_but_latest()


# ── result reporting ────────────────────────────────────────────────────────

def test_a_failed_delete_is_reported_not_swallowed(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS)
    shown = []
    monkeypatch.setattr(
        rm.QMessageBox, "warning",
        staticmethod(lambda parent, title, text, *a, **k: shown.append((title, text))),
    )
    monkeypatch.setattr(pane, "_load_restore_points", lambda: None)

    pane._on_deleted((2, [(13, "Access denied — run the tool as Administrator.")]))

    assert shown, "a partial failure must surface in the UI"
    title, text = shown[0]
    assert title == "Partially Deleted"
    assert "Administrator" in text
    assert "failed 1" in pane._status_label.text()


def test_a_clean_delete_re_reads_from_windows(pane, monkeypatch):
    reloaded = []
    monkeypatch.setattr(pane, "_load_restore_points", lambda: reloaded.append(True))
    pane._on_deleted((3, []))

    assert reloaded == [True], "the table must be re-read, not patched locally"
    assert "Deleted 3" in pane._status_label.text()
    assert pane._delete_running is False


def test_buttons_are_locked_while_a_delete_is_running(pane):
    pane._on_points_loaded(FOUR_POINTS)
    _select_row(pane, 0)
    assert pane._delete_btn.isEnabled() is True

    pane._delete_running = True
    pane._update_delete_buttons()
    assert pane._delete_btn.isEnabled() is False
    assert pane._prune_btn.isEnabled() is False


# ── read-back and gating ────────────────────────────────────────────────────

def test_a_deletion_is_verified_by_reading_the_list_back(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS)
    pane._pending_verify = [11, 12]
    pane._on_points_loaded(FOUR_POINTS[1:])          # Windows still lists 12
    text = pane._status_label.text()
    assert "1 confirmed gone" in text and "still lists 1" in text and "12" in text
    assert pane._pending_verify == []


def test_a_clean_read_back_says_so(pane):
    pane._on_points_loaded(FOUR_POINTS)
    pane._pending_verify = [11]
    pane._on_points_loaded(FOUR_POINTS[1:])
    assert "confirmed gone" in pane._status_label.text() and "still lists" not in pane._status_label.text()


def test_deleting_without_elevation_is_refused_with_the_reason(pane, monkeypatch):
    pane._on_points_loaded(FOUR_POINTS)
    _select_row(pane, 1)
    monkeypatch.setattr(rm, "is_admin", lambda: False)
    shown = []
    monkeypatch.setattr(rm.QMessageBox, "information",
                        staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    monkeypatch.setattr(rm, "confirm_destructive", lambda *a, **k: pytest.fail("asked without admin"))
    pane._delete_selected()
    assert shown and "administrator" in shown[0].lower()


def test_a_failed_list_read_does_not_blank_the_table(pane):
    pane._on_points_loaded(FOUR_POINTS)
    pane._on_snapshot(rm._Snapshot(points=None, points_error="boom"))
    assert pane._table.rowCount() == 4
    assert "boom" in pane._status_label.text()


def test_findings_render_for_a_stale_unprotected_machine(pane):
    from modules.restore_manager import restore_analysis as ra
    snap = rm._Snapshot(points=FOUR_POINTS, protection=ra.Protection(mounts=["E:"]),
                        storage=[], mounts=["C:", "E:"])
    pane._on_snapshot(snap)
    labels = [pane._findings_box.itemAt(i).widget().text() for i in range(pane._findings_box.count())]
    assert any("days old" in t for t in labels)
    assert pane._drive_table.rowCount() == 2
    assert pane._drive_table.item(0, 1).text() == "Off" and pane._drive_table.item(1, 1).text() == "On"
