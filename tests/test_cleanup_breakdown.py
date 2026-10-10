r"""A 47 GB item you cannot see inside is one checkbox, not a choice.

%TEMP% on this machine is 47.36 GB across 46,825 directories, and the tab
presented it as a single row: "Temp Files — 47.4 GB". You could not see
what was in it, could not pick part of it, and ticking it deleted a
directory a running process may be writing to. The contents turned out to
be ~100 near-identical `wct_*` folders at 0.44 GB each — this project's own
pytest temp dirs — which is exactly the kind of thing someone would want to
see before agreeing to anything.

Measured lazily, on expand: the parent's size is already known from the
scan, and walking every oversized item's children up front would repeat
the most expensive part of the sweep for rows nobody opens.
"""
import os
import time

import pytest
from PyQt6.QtCore import QThreadPool

from _sparse import make_sparse
from modules.cleanup.cleanup_scanner import ScanItem, breakdown


def test_children_come_back_biggest_first(tmp_path):
    for name, size in (("small", 1024), ("huge", 400_000), ("mid", 50_000)):
        child = tmp_path / name
        child.mkdir()
        (child / "payload.bin").write_bytes(b"x" * size)

    rows = breakdown.children_by_size(str(tmp_path))
    assert [os.path.basename(path) for path, _ in rows] == \
        ["huge", "mid", "small"]


def test_loose_files_are_listed_alongside_directories(tmp_path):
    (tmp_path / "a_dir").mkdir()
    (tmp_path / "a_dir" / "x.bin").write_bytes(b"x" * 100)
    (tmp_path / "loose.bin").write_bytes(b"y" * 5000)

    names = {os.path.basename(p) for p, _ in
             breakdown.children_by_size(str(tmp_path))}
    assert names == {"a_dir", "loose.bin"}


def test_the_breakdown_is_capped(tmp_path):
    for index in range(40):
        (tmp_path / f"d{index:02d}").mkdir()
        (tmp_path / f"d{index:02d}" / "f").write_bytes(b"x" * (index + 1))

    rows = breakdown.children_by_size(str(tmp_path), limit=10)
    assert len(rows) == 10
    assert os.path.basename(rows[0][0]) == "d39", "the cap kept the wrong ones"


def test_an_unreadable_directory_yields_nothing_rather_than_raising():
    assert breakdown.children_by_size(r"C:\definitely-not-here-9f3ab") == []


def test_only_a_big_enough_directory_is_worth_breaking_down():
    big = ScanItem(path=r"C:\big", size=2 * 1024**3, is_dir=True)
    small = ScanItem(path=r"C:\small", size=1024, is_dir=True)
    a_file = ScanItem(path=r"C:\file", size=2 * 1024**3, is_dir=False)

    assert breakdown.is_worth_expanding(big) is True
    assert breakdown.is_worth_expanding(small) is False
    assert breakdown.is_worth_expanding(a_file) is False


# ── the tab ────────────────────────────────────────────────────────────

def _settle(qapp):
    """The breakdown is measured on a worker; let it land."""
    QThreadPool.globalInstance().waitForDone(30_000)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _tree_rows(tab):
    rows = []
    for i in range(tab._tree.topLevelItemCount()):
        parent = tab._tree.topLevelItem(i)
        for j in range(parent.childCount()):
            rows.append(parent.child(j))
    return rows


@pytest.fixture
def big_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("bulky")
    for name, size in (("alpha", 700_000_000), ("beta", 400_000_000)):
        child = root / name
        child.mkdir()
        make_sparse(child / "payload.bin", size)
    return root


def test_an_oversized_item_offers_a_breakdown(qapp, big_dir):
    from modules.cleanup.cleanup_scanner import ScanResult
    from modules.cleanup.tabs._scan_tab import _ScanTab

    def scan_bulky(min_age_days: int = 0) -> ScanResult:
        result = ScanResult()
        result.items = [ScanItem(path=str(big_dir), size=2 * 1024**3,
                                 is_dir=True)]
        result.total_size = 2 * 1024**3
        return result

    tab = _ScanTab({scan_bulky: ("Bulky", "safe")})
    tab._on_scan_result((scan_bulky(), {scan_bulky: scan_bulky()}))

    row = _tree_rows(tab)[0]
    assert row.childCount() > 0, (
        "a 2 GB directory was presented as one checkbox with nothing inside")


def test_expanding_an_oversized_item_names_what_is_inside(qapp, big_dir):
    from modules.cleanup.cleanup_scanner import ScanResult
    from modules.cleanup.tabs._scan_tab import _ScanTab

    def scan_bulky(min_age_days: int = 0) -> ScanResult:
        result = ScanResult()
        result.items = [ScanItem(path=str(big_dir), size=2 * 1024**3,
                                 is_dir=True)]
        return result

    tab = _ScanTab({scan_bulky: ("Bulky", "safe")})
    tab._on_scan_result((scan_bulky(), {scan_bulky: scan_bulky()}))
    row = _tree_rows(tab)[0]

    tab._fill_breakdown(row)          # what itemExpanded triggers
    _settle(qapp)

    names = [row.child(k).text(0) for k in range(row.childCount())]
    assert any("alpha" in n for n in names), names
    assert any("beta" in n for n in names), names
    assert "alpha" in names[0], "children are not biggest-first"


def test_a_child_is_not_deleted_twice_when_its_parent_is_also_ticked(
        qapp, big_dir):
    from PyQt6.QtCore import Qt

    from modules.cleanup.cleanup_scanner import ScanResult
    from modules.cleanup.tabs._scan_tab import _ScanTab

    def scan_bulky(min_age_days: int = 0) -> ScanResult:
        result = ScanResult()
        result.items = [ScanItem(path=str(big_dir), size=2 * 1024**3,
                                 is_dir=True)]
        return result

    tab = _ScanTab({scan_bulky: ("Bulky", "safe")})
    tab._on_scan_result((scan_bulky(), {scan_bulky: scan_bulky()}))
    row = _tree_rows(tab)[0]
    tab._fill_breakdown(row)
    _settle(qapp)
    for k in range(row.childCount()):
        row.child(k).setCheckState(0, Qt.CheckState.Checked)
    row.setCheckState(0, Qt.CheckState.Checked)

    selected = [i.path for i in tab._get_selected_items() if i.selected]
    assert selected == [str(big_dir)], (
        f"the same bytes were queued for deletion more than once: {selected}")
