"""Discover tab polish (WP10): wording, owner guess, research, stub, read-only."""
import json
import os
import time
from datetime import date

import pytest

from modules.cleanup.cleanup_scanner import catalog, discover
from modules.cleanup.tabs import _discover_tab as ui

ROOTS = [("%LOCALAPPDATA%", r"C:\Users\u\AppData\Local"),
         ("%APPDATA%", r"C:\Users\u\AppData\Roaming"),
         ("%PROGRAMDATA%", r"C:\ProgramData")]


def _roots(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")
    return ui.expanded_roots(["%LOCALAPPDATA%", "%APPDATA%", "%PROGRAMDATA%"])


def test_plural():
    assert ui.plural(1, "folder") == "1 folder"
    assert ui.plural(2, "folder") == "2 folders"
    assert ui.plural(0, "folder") == "0 folders"


@pytest.mark.parametrize("path, owner", [
    (r"C:\Users\u\AppData\Local\Discord\Cache", "Discord"),
    (r"C:\Users\u\AppData\Local\Google\Chrome\User Data\Default\Cache", "Google \u203a Chrome"),
    (r"C:\Users\u\AppData\Local\Vendor\User Data\GPUCache", "Vendor"),
    (r"C:\Users\u\AppData\Local\Packages\Microsoft.Foo_8wekyb3d8bbwe\LocalCache\Cache",
     "Microsoft.Foo"),
    (r"C:\Users\u\AppData\Local\Programs\Thing\logs", "Thing"),
    (r"C:\ProgramData\Acme\Logs", "Acme"),
    (r"C:\Users\u\AppData\Local\Cache", ""),
    (r"D:\elsewhere\Cache", ""),
])
def test_owner_guess(monkeypatch, path, owner):
    assert ui.guess_owner(path, _roots(monkeypatch)) == owner


def test_root_label_and_portable_path(monkeypatch):
    roots = _roots(monkeypatch)
    path = r"C:\Users\u\AppData\Roaming\Acme\App\Cache"
    assert ui.root_label(path, roots) == "Roaming AppData"
    assert ui.portable_path(path, roots) == r"%APPDATA%\Acme\App\Cache"
    assert ui.portable_path(r"D:\x\Cache", roots) == r"D:\x\Cache"


def test_why_listed_names_the_token():
    assert ui.why_listed("GPUCache") == "GPUCache: GPU cache (Chromium)"
    assert ui.why_listed("Logs") == "Logs: log folder"
    assert ui.why_listed("Weird") == "Weird: cache-like name"
    # Every token the engine matches has a description of its own.
    assert set(discover.CACHE_NAMES) <= set(ui.TOKEN_KINDS)


def test_research_key_needs_evidence_for_common_names():
    assert ui.research_key("GPUCache") == "browser:GPUCache"
    assert ui.research_key("Code Cache") == "browser:Code Cache"
    # "Cache" alone is anyone's name: Chromium research needs a Chromium profile beside it.
    assert ui.research_key("Cache") == ""
    assert ui.research_key("Cache", ["Settings", "Cache"]) == ""
    assert ui.research_key("Cache", ["Cache", "Local Storage", "Preferences"]) == "browser:Cache"
    assert ui.research_key("Logs", ["Local Storage"]) == ""


def test_research_keys_exist_in_the_knowledge_base():
    from modules.cleanup.cleanup_scanner import knowledge
    for key in list(ui._CHROMIUM_ONLY.values()) + list(ui._CHROMIUM_IF_PROFILE.values()):
        assert knowledge.entry_for(key) is not None, key


def test_annotate_lists_siblings_only_when_needed(monkeypatch):
    roots = _roots(monkeypatch)
    asked = []

    def siblings(path, cancelled):
        asked.append(path)
        return ["Cache", "Network"]

    candidates = [discover.Candidate(r"C:\Users\u\AppData\Local\App\Cache", 5, "Cache"),
                  discover.Candidate(r"C:\Users\u\AppData\Local\App\GPUCache", 7, "GPUCache")]
    assert roots
    rows = ui.annotate(candidates, roots=["%LOCALAPPDATA%", "%APPDATA%", "%PROGRAMDATA%"],
                       siblings=siblings)
    assert asked == [r"C:\Users\u\AppData\Local\App\Cache"]
    assert [r.research_key for r in rows] == ["", "browser:GPUCache"]
    assert rows[0].owner == "App" and rows[0].location == "Local AppData"


def test_sibling_names_tolerates_missing_parent(tmp_path):
    assert ui.sibling_names("big") == []
    assert ui.sibling_names(str(tmp_path / "missing" / "Cache")) == []
    (tmp_path / "Local Storage").mkdir()
    (tmp_path / "Cache").mkdir()
    assert sorted(ui.sibling_names(str(tmp_path / "Cache"))) == ["Cache", "Local Storage"]


def _row(**kw):
    values = dict(path=r"C:\Users\u\AppData\Local\Acme\Cache", size=3 * 2**20, name="Cache",
                  why=ui.why_listed("Cache"), owner="Acme", location="Local AppData")
    values.update(kw)
    return ui.DiscoverRow(**values)


def test_catalog_stub_is_disabled_and_valid(monkeypatch):
    roots = _roots(monkeypatch)
    text = ui.catalog_stub(_row(), roots=["%LOCALAPPDATA%", "%APPDATA%", "%PROGRAMDATA%"],
                           today=date(2026, 10, 21))
    stub = json.loads(text)
    assert stub["paths"] == [r"%LOCALAPPDATA%\Acme\Cache"]
    assert stub["id"] == "acme_cache"
    assert stub["safety"] == "caution"
    assert stub["disabled_reason"]  # pasted unread it measures and deletes nothing
    assert "2026-10-21" in stub["description"]
    spec = catalog.ScannerSpec(**stub)
    assert spec.disabled_reason
    assert roots  # env was set for the expansion


def test_tooltip_and_detail_escape_and_disclose():
    hostile = _row(owner="<script>x</script>", path=r"C:\a\<b>\Cache")
    tip = ui.tooltip_for(hostile, None)
    assert "<script>" not in tip and "Not yet researched" in tip and "never deletes" in tip
    detail = ui.detail_markup(hostile, None)
    assert "<script>" not in detail and "&lt;b&gt;" in detail
    assert "Discover never deletes" in detail


def test_detail_with_research_says_it_is_generic():
    from modules.cleanup.cleanup_scanner import knowledge
    entry = knowledge.entry_for("browser:GPUCache")
    row = _row(name="GPUCache", research_key="browser:GPUCache")
    detail = ui.detail_markup(row, entry)
    assert "not about this particular app" in detail
    assert escape_text(entry.what) in detail
    assert ui.research_text(row, entry) == "Chromium research"
    assert ui.research_text(_row(), None) == "Not yet researched"


def escape_text(text):
    from html import escape
    return escape(text)


# -- Qt ---------------------------------------------------------------------

def _wait(qapp, tab):
    deadline = time.monotonic() + 5
    while not tab._scan_btn.isEnabled() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _fake(monkeypatch, candidates, unreadable=0):
    monkeypatch.setattr(ui, "find_uncovered_caches",
                        lambda **kwargs: discover.DiscoverResult(list(candidates), unreadable))


def test_empty_state_before_and_after_scan(qapp, monkeypatch):
    tab = ui._DiscoverTab()
    assert tab._stack.currentWidget() is tab._empty
    assert tab._empty.objectName() == "emptyState"
    _fake(monkeypatch, [])
    tab._scan_btn.click()
    _wait(qapp, tab)
    assert tab._stack.currentWidget() is tab._empty
    assert "Nothing uncovered above 20 MB" in tab._empty.text()
    tab.close()


def test_one_folder_is_singular(qapp, monkeypatch):
    _fake(monkeypatch, [discover.Candidate("only", 64, "GPUCache")], unreadable=1)
    tab = ui._DiscoverTab()
    tab._scan_btn.click()
    _wait(qapp, tab)
    text = tab._status.text()
    assert "Found 1 folder," in text and "1 folders" not in text
    assert "1 protected folders were skipped" in text  # pinned by test_cleanup_discover.py
    assert tab._stack.currentWidget() is tab._splitter
    assert tab._table.item(0, 2).text() == "GPUCache: GPU cache (Chromium)"
    assert tab._table.item(0, 4).text() == "Chromium research"
    assert tab._table.item(0, 0).toolTip()
    tab.close()


def test_selection_fills_detail_and_hides_exclude(qapp, monkeypatch):
    _fake(monkeypatch, [discover.Candidate(r"C:\nowhere\Acme\Cache", 64, "Cache")])
    tab = ui._DiscoverTab()
    tab._scan_btn.click()
    _wait(qapp, tab)
    assert not any(b.isEnabled() for b in tab._action_btns)
    tab._table.selectRow(0)
    assert all(b.isEnabled() for b in tab._action_btns)
    html = tab._detail.browser.toPlainText()
    assert "Discover never deletes" in html and "Not yet researched" in html
    assert "What this tool will do" not in html  # the deleter's wording would be false here
    excludes = [b for b in tab._detail.path_buttons if b.text().startswith("Exclude")]
    assert excludes and not any(b.isVisibleTo(tab) for b in excludes)
    tab._copy_stub()
    stub = json.loads(qapp.clipboard().text())
    assert stub["disabled_reason"] and stub["paths"] == [r"C:\nowhere\Acme\Cache"]
    tab._copy_path()
    assert qapp.clipboard().text() == r"C:\nowhere\Acme\Cache"
    tab.close()


def test_every_control_has_a_tooltip(qapp):
    tab = ui._DiscoverTab()
    for widget in [tab._minimum, tab._scan_btn, tab._stop_btn, *tab._action_btns]:
        assert widget.toolTip(), widget
    for column in range(tab._table.columnCount()):
        assert tab._table.horizontalHeaderItem(column).toolTip()
    tab.close()


def test_tab_never_deletes(qapp, monkeypatch, tmp_path):
    """Read-only: the tab reaches no deleter and its freed signal never fires."""
    target = tmp_path / "Acme" / "Cache"
    target.mkdir(parents=True)
    (target / "f.bin").write_bytes(b"x" * 1024)
    from modules.cleanup import cleanup_scanner
    calls = []
    for name in ("delete_items", "delete_items_report"):
        if hasattr(cleanup_scanner, name):
            monkeypatch.setattr(cleanup_scanner, name, lambda *a, **k: calls.append(a))
    monkeypatch.setattr(os, "remove", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(os, "rmdir", lambda *a, **k: calls.append(a))
    _fake(monkeypatch, [discover.Candidate(str(target), 1024, "Cache")])
    tab = ui._DiscoverTab()
    freed = []
    tab.freed_bytes.connect(freed.append)
    tab._scan_btn.click()
    _wait(qapp, tab)
    tab._table.selectRow(0)
    tab._copy_stub()
    tab._copy_path()
    assert calls == [] and freed == []
    assert (target / "f.bin").exists()
    source = open(ui.__file__, encoding="utf-8").read()
    for forbidden in ("delete_items", "rmtree", "os.remove", "unlink(", "rmdir"):
        assert forbidden not in source
    tab.close()


def test_stop_cancels_a_running_scan(qapp, monkeypatch):
    import threading
    release = threading.Event()

    def slow(**kwargs):
        release.wait(5)
        return discover.DiscoverResult([discover.Candidate("late", 1, "Cache")], 0)

    monkeypatch.setattr(ui, "find_uncovered_caches", slow)
    tab = ui._DiscoverTab()
    tab._scan_btn.click()
    assert tab._stop_btn.isEnabled() and not tab._scan_btn.isEnabled()
    tab._stop_btn.click()
    assert tab._scan_btn.isEnabled() and "cancelled" in tab._status.text()
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert tab._table.rowCount() == 0  # a late result after Stop is ignored
    tab.close()
