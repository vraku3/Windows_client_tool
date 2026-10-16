"""An exclusion INSIDE a selected item survives the clean.

filter_items() drops only items that are, or sit inside, an excluded path. An
item that CONTAINS one -- %TEMP% selected while %TEMP%\\keep is excluded -- used
to reach the deleting walk whole and take the excluded folder with it.
"""
import os

from modules.cleanup.cleanup_scanner import exclusions, scanners_system
from modules.cleanup.cleanup_scanner.scanners_system import ScanItem


def test_an_excluded_child_survives_cleaning_its_parent(tmp_path, monkeypatch):
    parent = tmp_path / "Temp"
    keep = parent / "keep"
    keep.mkdir(parents=True)
    (keep / "important.txt").write_text("mine")
    (parent / "junk.tmp").write_text("x" * 100)
    monkeypatch.setattr(exclusions, "list_exclusions", lambda: [str(keep)])
    monkeypatch.setattr(scanners_system, "_refusal_reason", lambda item: None)

    item = ScanItem(path=str(parent), size=100, is_dir=True, selected=True, safety="safe")
    scanners_system.delete_items_report([item])

    assert not (parent / "junk.tmp").exists()
    assert (keep / "important.txt").read_text() == "mine"
