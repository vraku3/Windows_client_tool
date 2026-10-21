"""WP11: the cache leaves added 2026-10-21, short labels and KB-driven safety.

Each new target is a cache LEAF the deleter's guards accept, carries a
knowledge entry, and is never safer than its research verdict.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from modules.cleanup.cleanup_scanner import catalog, knowledge  # noqa: E402
from modules.cleanup.cleanup_scanner import scanners_system as ss  # noqa: E402

OWNED = ("apps", "games", "system", "dev")

NEW_LEAVES = {
    "amd_shader_caches": "caution",
    "steam_appcache_httpcache": "safe",
    "steam_depotcache": "caution",
    "squirrel_installer_temp": "safe",
}

#: Never a catalog entry: model downloads and a live tool runtime, not caches.
NEVER = ("\\.cache\\huggingface", "\\.cache\\codex-runtimes")

_LEVEL = {"safe": 0, "caution": 1, "danger": 2}


@pytest.fixture(scope="module")
def specs():
    return catalog.load_catalog(force=True)


@pytest.mark.parametrize("spec_id,safety", sorted(NEW_LEAVES.items()))
def test_new_leaf_is_defined_with_its_safety(specs, spec_id, safety):
    spec = specs[spec_id]
    assert spec.safety == safety
    assert not spec.disabled_reason
    assert spec.short_label and len(spec.short_label) <= 28


@pytest.mark.parametrize("spec_id", sorted(NEW_LEAVES))
def test_new_leaf_is_never_refused_by_the_deleter(specs, spec_id):
    """A target the guards refuse is dead weight; one they would refuse for
    holding sign-in or state must never be offered at all."""
    paths = catalog.targets_of(specs[spec_id])
    assert paths
    for path in paths:
        raw = path
        assert not ss._state_path_protected(path, True, contains=True, resolved=path), raw
        assert not ss._protected_entry(path, True, resolved=path), raw
        if os.path.isdir(path):
            outlook, sign_in = ss.held_user_data(path, True)
            assert not outlook and not sign_in, path


@pytest.mark.parametrize("spec_id", sorted(NEW_LEAVES))
def test_new_leaf_is_researched_and_not_safer_than_research(specs, spec_id):
    entry = knowledge.entry_for(spec_id)
    assert entry is not None and entry.what
    assert _LEVEL[specs[spec_id].safety] >= _LEVEL[entry.verdict]


def test_shader_caches_are_never_safe(specs):
    for spec in specs.values():
        if spec.disabled_reason:
            continue
        if any(name in p.lower() for p in spec.paths
               for name in ("\\amd\\dxccache", "\\amd\\vkcache", "\\amd\\dx9cache",
                            "\\amd\\oglpcache", "\\amd\\dxcache", "d3dscache")):
            assert spec.safety != "safe", spec.id


def test_never_targets_model_or_runtime_folders(specs):
    for spec in specs.values():
        for raw in spec.paths:
            assert not any(n in raw.lower() for n in NEVER), (spec.id, raw)


def test_every_enabled_owned_entry_has_a_short_label(specs):
    missing, too_long = [], []
    for spec in specs.values():
        if spec.category not in OWNED or spec.disabled_reason:
            continue
        if not spec.short_label:
            missing.append(spec.id)
        elif len(spec.short_label) > 28:
            too_long.append((spec.id, spec.short_label))
    assert missing == [] and too_long == []


def test_short_labels_are_unique_per_category(specs):
    seen = {}
    for spec in specs.values():
        if spec.short_label and not spec.disabled_reason:
            key = (spec.category, spec.short_label.lower())
            assert key not in seen, (spec.id, seen.get(key))
            seen[key] = spec.id


def test_no_owned_entry_is_safer_than_its_research(specs):
    looser = []
    for spec in specs.values():
        if spec.category not in OWNED:
            continue
        entry = knowledge.entry_for(spec.id)
        if entry and _LEVEL[entry.verdict] > _LEVEL[spec.safety]:
            looser.append((spec.id, spec.safety, entry.verdict))
    assert looser == []


def test_every_catalog_id_has_knowledge(specs):
    assert [i for i in specs if knowledge.entry_for(i) is None] == []


def test_short_label_defaults_to_empty():
    spec = catalog.ScannerSpec(id="x", label="X", paths=["%TEMP%\\x"])
    assert spec.short_label == ""


def test_steam_http_leaf_covers_fixed_drive_installs(specs, monkeypatch):
    monkeypatch.setattr(catalog.drives, "fixed_drive_roots", lambda: ["X:\\", "Y:\\"])
    paths = catalog.targets_of(specs["steam_appcache_httpcache"])
    assert len(paths) == len({os.path.normcase(path) for path in paths})
    leaf = "appcache\\httpcache"
    for drive in ("X", "Y"):
        assert f"{drive}:\\Steam\\{leaf}" in paths
        assert f"{drive}:\\Program Files (x86)\\Steam\\{leaf}" in paths


def test_wer_queue_is_researched_caution(specs):
    spec = specs["wer_reports"]
    assert spec.safety == "caution"
    assert any(path.endswith("\\ReportQueue") for path in spec.paths)
    assert "queued" in knowledge.entry_for(spec.id).risk.lower()


@pytest.mark.parametrize("spec_id", sorted(NEW_LEAVES))
def test_new_leaf_has_vendor_sources(spec_id):
    assert knowledge.entry_for(spec_id).sources


def test_store_whole_localcache_is_not_added(specs):
    assert not any("microsoft.windowsstore_" in p.lower() and p.lower().endswith("\\localcache")
                   for spec in specs.values() for p in spec.paths)


@pytest.mark.parametrize("spec_id", ("box_logs", "firefox_crash_reports", "google_drive_logs", "pcloud_logs"))
def test_safety_package_additions_have_sourced_knowledge(spec_id):
    entry = knowledge.entry_for(spec_id)
    assert entry and entry.what and entry.used_for and entry.risk and entry.before_cleaning
    assert entry.sources


@pytest.mark.parametrize("theme", ("dark", "light"))
def test_catalog_labels_and_tooltips_render_without_overlap(specs, qapp, tmp_path, theme):
    """Render WP11 data through WP3, without scanning or invoking deletion."""
    from pathlib import Path
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QHeaderView, QTableView, QVBoxLayout, QWidget
    from core.semantic_colors import set_theme
    from modules.cleanup.cleanup_scanner._common import ScanItem
    from modules.cleanup.components.item_rows import RowFacts
    from modules.cleanup.components.safety_delegate import ItemRowModel, SafetyDelegate

    set_theme(theme)
    host = QWidget()
    host.setStyleSheet((Path(__file__).parents[1] / f"src/ui/styles/{theme}.qss").read_text(encoding="utf-8"))
    layout = QVBoxLayout(host)
    table = QTableView(host)
    rows = []
    for index, key in enumerate((*sorted(NEW_LEAVES), "wer_reports")):
        spec = specs[key]
        rows.append(RowFacts.from_item(
            ScanItem(spec.paths[0], (index + 1) * 1024 * 1024, True,
                     selected=spec.safety == "safe", safety=spec.safety),
            spec.short_label, catalog_id=key, category=spec.category,
            description=spec.description, total_bytes=15 * 1024 * 1024))
    model = ItemRowModel(rows, table)
    table.setModel(model)
    table.setItemDelegate(SafetyDelegate(table))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    for column, width in enumerate((260, 100, 90, 120, 280, 110)):
        table.setColumnWidth(column, width)
    table.verticalHeader().setDefaultSectionSize(52)
    layout.addWidget(table)
    host.resize(1240, 400)
    host.show()
    qapp.processEvents()
    try:
        for row_index, row in enumerate(rows):
            rect = table.visualRect(model.index(row_index, 0))
            assert rect.width() >= table.fontMetrics().horizontalAdvance(row.label) + 45
            assert rect.height() >= table.fontMetrics().height() + 10
            assert table.viewport().rect().contains(rect)
            for column in range(model.columnCount()):
                assert model.data(model.index(row_index, column), Qt.ItemDataRole.ToolTipRole)
        shot = tmp_path / f"wp11-catalog-{theme}.png"
        assert host.grab().save(str(shot))
        image = QImage(str(shot))
        assert not image.isNull() and image.width() == host.width() and image.height() == host.height()
        print(f"WP11 render: {shot} ({image.width()}x{image.height()})")
    finally:
        host.close()
        host.deleteLater()
        set_theme("dark")
