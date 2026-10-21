"""WP3 contracts, with WP1 supplied as a deliberately small test stub."""

import html
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from modules.cleanup.cleanup_scanner import ScanItem


@dataclass(frozen=True)
class KnowledgeStub:
    what: str = "Downloaded packages"
    used_for: str = "Installing updates"
    verdict: str = "caution"
    risk: str = "A download may need repeating"
    before_cleaning: str = "Finish installing first"
    regenerates: bool = True
    sources: tuple = ("https://learn.microsoft.com/windows/",)
    catalog_concern: str = ""


@pytest.fixture
def kb(monkeypatch):
    entries = {"updates": KnowledgeStub()}

    def detail_html(entry, facts):
        # WP1 owns rendering; echo injected operation facts so the pane's
        # contract is exercised without depending on the parallel renderer.
        action = (
            (
                "Contents deleted; root folder kept"
                if facts["keep_root"]
                else "Folder and contents deleted"
            )
            if facts["is_dir"]
            else "File deleted"
        )
        return (
            f"<h2>{html.escape(facts['label'])}</h2><p>{action}</p>"
            f"<p>{facts['keep_newer_than_hours']} h; links never followed; Cloud-only files skipped</p>"
            f"<p>{html.escape(entry.what) if entry else 'Not yet researched'}</p>"
        )

    stub = SimpleNamespace(
        entry_for=entries.get,
        effective_safety=lambda catalog, entry: max(
            (catalog, entry.verdict if entry else catalog),
            key={"safe": 0, "caution": 1, "danger": 2}.__getitem__,
        ),
        tooltip_html=lambda entry, label, safety, size_text: (
            f"<b>{html.escape(label)}</b> {html.escape(size_text)} "
            + (html.escape(entry.what) if entry else "Not yet researched")
        ),
        detail_html=detail_html,
    )
    monkeypatch.setitem(sys.modules, "modules.cleanup.cleanup_scanner.knowledge", stub)
    return entries


@pytest.mark.parametrize(
    "safety,badge",
    [
        ("safe", "✓ Safe"),
        ("caution", "! Review"),
        ("danger", "✕ Risky"),
    ],
)
def test_badges_missing_research_and_numeric_share(kb, safety, badge):
    from modules.cleanup.components.item_rows import RowFacts

    item = ScanItem(r"C:\Cache", 1024, True, selected=False, safety=safety)
    row = RowFacts.from_item(item, "Cache", total_bytes=4096)
    assert row.badge_text == badge
    assert row.size_bytes == 1024 and row.share_percent == 25
    assert row.size_sort_value == 1024
    assert row.verdict == "Not yet researched"
    assert "Not yet researched" in row.tooltip
    assert row.selected is False and item.selected is False


@pytest.mark.parametrize(
    "catalog,research,expected",
    [
        ("safe", "caution", "caution"),
        ("danger", "safe", "danger"),
        ("caution", "danger", "danger"),
    ],
)
def test_safety_only_moves_towards_caution(kb, catalog, research, expected):
    from modules.cleanup.components.item_rows import RowFacts

    kb["test"] = KnowledgeStub(verdict=research, regenerates=False)
    item = ScanItem("cache", 15, True, safety=catalog)
    row = RowFacts.from_item(item, "Cache", catalog_id="test", total_bytes=0)
    assert row.safety == expected and row.catalog_safety == catalog
    assert item.safety == catalog
    assert row.share_percent == 0
    assert "does not come back" in row.verdict.lower()
    assert row.research_more_cautious == (catalog != expected)


def test_context_and_scanner_opt_out_are_preserved(kb):
    from modules.cleanup.components.item_rows import RowFacts

    row = RowFacts.from_item(
        ScanItem("cache", 2048, True, selected=False, keep_newer_than_hours=24),
        "Updates",
        catalog_id="updates",
        in_use_by=("chrome.exe",),
        also_on=("Windows Update",),
        cloud_skipped=True,
        unselected_reason="Virtual disk is user data",
    )
    assert row.subtitle == "Downloaded packages"
    assert "In use by chrome.exe" in row.chips
    assert "Also on Windows Update" in row.chips
    assert "Cloud files skipped" in row.chips
    assert "Kept: newer than 24 h" in row.chips
    assert row.unselected_reason == "Virtual disk is user data"


def test_large_file_group_collapses_without_losing_original_facts(kb):
    from modules.cleanup.components.item_rows import RowFacts, collapse_rows

    rows = [RowFacts.from_item(ScanItem(f"file{i}", 1024, False), f"File {i}") for i in range(30)]
    collapsed = collapse_rows(rows)
    assert len(collapsed) == 2
    summary, show_all = collapsed
    assert summary.kind == "summary" and summary.size_bytes == 30720
    assert "30 files" in summary.label
    assert summary.children == tuple(rows)
    assert show_all.kind == "show_all" and "Show all" in show_all.label
    assert not summary.selected and not show_all.selected
    assert collapse_rows(rows, expanded=True) == rows
    assert collapse_rows(rows[:20]) == rows[:20]


def test_row_model_sorts_bytes_and_exposes_delegate_roles(kb, qapp):
    from PyQt6.QtCore import Qt
    from modules.cleanup.components.item_rows import RowFacts, SIZE_ROLE, SAFETY_ROLE
    from modules.cleanup.components.safety_delegate import ItemRowModel

    model = ItemRowModel(
        [
            RowFacts.from_item(ScanItem("large", 2048, False), "Large"),
            RowFacts.from_item(ScanItem("small", 512, False, safety="danger"), "Small"),
        ]
    )
    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert model.data(model.index(0, 0)) == "Small"
    assert model.data(model.index(0, 1), SIZE_ROLE) == 512
    assert model.data(model.index(0, 3), SAFETY_ROLE) == "danger"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ForegroundRole) is None


def test_delegate_repaints_safety_and_share_in_every_theme(kb, qapp):
    from PyQt6.QtWidgets import QTableView
    from core.semantic_colors import current_theme, set_theme
    from modules.cleanup.components.item_rows import RowFacts
    from modules.cleanup.components.safety_delegate import ItemRowModel, SafetyDelegate

    view = QTableView()
    model = ItemRowModel(
        [
            RowFacts.from_item(ScanItem(s, 100, False, safety=s), s, total_bytes=300)
            for s in ("safe", "caution", "danger")
        ],
        view,
    )
    view.setModel(model)
    view.setItemDelegate(SafetyDelegate(view))
    view.resize(850, 240)
    view.show()
    before = current_theme()
    try:
        pictures = []
        for theme in ("dark", "light", "green"):
            set_theme(theme)
            assert current_theme() == theme
            view.viewport().update()
            qapp.processEvents()
            shot = view.grab().toImage()
            assert not shot.isNull()
            pictures.append(shot)
        assert pictures[0] != pictures[1]
    finally:
        set_theme(before)
        view.close()


@pytest.mark.parametrize(
    "is_dir,keep_root,expected",
    [
        (True, True, "Contents deleted; root folder kept"),
        (True, False, "Folder and contents deleted"),
        (False, True, "File deleted"),
    ],
)
def test_detail_states_real_deletion_facts_and_emits_exact_path(
    kb, qapp, is_dir, keep_root, expected
):
    from modules.cleanup.components.item_rows import RowFacts
    from modules.cleanup.components.detail_pane import DetailPane

    row = RowFacts.from_item(
        ScanItem(
            r"C:\cache\<unsafe>",
            1024,
            is_dir,
            selected=False,
            keep_root=keep_root,
            keep_newer_than_hours=24,
        ),
        "<Cache>",
        catalog_id="updates",
        unselected_reason="Scanner chose to keep this",
    )
    pane = DetailPane()
    pane.set_row(row)
    text = pane.browser.toPlainText()
    assert expected in text
    for phrase in (
        "24 h",
        "never followed",
        "Cloud-only files skipped",
        "Research rates this more cautiously",
        "Scanner chose to keep this",
        "<Cache>",
    ):
        assert phrase in text
    paths = []
    pane.open_folder.connect(paths.append)
    pane.copy_path.connect(paths.append)
    pane.exclude_path.connect(paths.append)
    buttons = pane.path_buttons
    for button in buttons:
        button.click()
    assert paths == [row.path] * 3
    assert pane.browser.openExternalLinks()
    pane.clear()
    assert not pane.path_buttons
    pane.deleteLater()


def test_no_loader_fallback_still_explains_facts(monkeypatch, qapp):
    from modules.cleanup.components import item_rows
    from modules.cleanup.components.detail_pane import DetailPane

    monkeypatch.setattr(item_rows, "knowledge_api", lambda: None)
    monkeypatch.setattr("modules.cleanup.components.detail_pane.knowledge_api", lambda: None)
    row = item_rows.RowFacts.from_item(
        ScanItem("<path>", 123, True, keep_newer_than_hours=7),
        "<Cache>",
        description="Catalog <description>",
    )
    assert "Catalog &lt;description&gt;" in row.tooltip
    pane = DetailPane()
    pane.set_row(row)
    text = pane.browser.toPlainText()
    assert "Catalog <description>" in text and "Not yet researched" in text
    assert "Contents deleted; root folder kept" in text and "7 h" in text
    pane.deleteLater()


def test_detail_path_actions_do_not_touch_filesystem(kb, monkeypatch, qapp):
    import builtins
    import os
    import subprocess
    from modules.cleanup.components.item_rows import RowFacts
    from modules.cleanup.components.detail_pane import DetailPane

    row = RowFacts.from_item(ScanItem(r"C:\missing\cache", 45, False), "Cache")
    pane = DetailPane()
    requested = []
    pane.open_folder.connect(requested.append)
    pane.copy_path.connect(requested.append)
    pane.exclude_path.connect(requested.append)

    def forbidden(*args, **kwargs):
        pytest.fail("A facts-only detail pane performed filesystem or process I/O")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        for name in ("stat", "lstat", "scandir", "listdir", "startfile", "remove", "unlink"):
            guard.setattr(os, name, forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        pane.set_row(row)
        for button in pane.path_buttons:
            button.click()
    assert requested == [r"C:\missing\cache"] * 3
    pane.deleteLater()


def test_checking_row_does_not_mutate_scanner_and_summary_is_not_checkable(kb, qapp):
    from PyQt6.QtCore import Qt
    from modules.cleanup.components.item_rows import RowFacts, collapse_rows
    from modules.cleanup.components.safety_delegate import ItemRowModel

    source = ScanItem("cache", 10, False, selected=False)
    facts = RowFacts.from_item(source)
    model = ItemRowModel([facts])
    index = model.index(0, 0)
    assert model.setData(index, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    assert model.rows[0].selected and not source.selected
    model.set_rows(collapse_rows([facts] * 30))
    assert not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsUserCheckable
    assert not model.setData(
        model.index(0, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
    )


def test_presenter_has_no_qt_imports_and_owned_files_are_utf8_lf():
    import ast
    from pathlib import Path

    base = Path(__file__).parents[1] / "src/modules/cleanup/components"
    code = (base / "item_rows.py").read_text(encoding="utf-8")
    imports = [n.module or "" for n in ast.walk(ast.parse(code)) if isinstance(n, ast.ImportFrom)]
    imports += [
        a.name for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Import) for a in n.names
    ]
    assert not any("Qt" in name or "PyQt" in name for name in imports)
    for path in [
        base / name for name in ("item_rows.py", "safety_delegate.py", "detail_pane.py")
    ] + [Path(__file__)]:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
