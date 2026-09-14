"""The preset combo box on Quick Cleanup's toolbar, and the mapping it
needs (category id -> real scanner function name) to let the presets
module apply NEVER_INCLUDED correctly.
"""


def test_preset_combo_exists_and_defaults_to_light(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    assert hasattr(tab, "_preset_combo")
    assert tab._preset_combo.currentData() == "light"


def test_preset_combo_offers_all_four_presets(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup.cleanup_presets import preset_names

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    offered = [tab._preset_combo.itemData(i) for i in range(tab._preset_combo.count())]
    assert set(offered) == set(preset_names())


def test_changing_preset_updates_the_clean_button_label(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    light_label = tab._clean_all_btn.text()
    idx = tab._preset_combo.findData("aggressive")
    assert idx != -1
    tab._preset_combo.setCurrentIndex(idx)

    assert tab._clean_all_btn.text() != light_label
    assert "Aggressive" in tab._clean_all_btn.text()


def test_id_to_scanner_name_maps_a_known_category(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build()  # defaults: real CLEANUP_CATEGORIES + ADVANCED_CATEGORIES

    assert tab._id_to_scanner_name.get("temp") == "scan_temp_files"
    # "browser" has no scanner function (handled specially) -- must map
    # to None, not raise or be silently absent.
    assert "browser" in tab._id_to_scanner_name
    assert tab._id_to_scanner_name["browser"] is None


def _built_tab_with_result(qapp, safe=0, caution=0, danger=0):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup.cleanup_scanner import ScanItem, ScanResult

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    result = ScanResult()
    for n, safety in ((safe, "safe"), (caution, "caution"), (danger, "danger")):
        for i in range(n):
            result.items.append(ScanItem(path=f"C:\\{safety}_{i}", size=100, is_dir=False, safety=safety))
    result.total_size = sum(i.size for i in result.items)
    tab._results = {"temp": result}
    return tab


def test_custom_preset_preserves_the_original_safe_only_behavior(qapp, monkeypatch):
    """Regression test: before this plan, _do_clean_all_safe always
    behaved like this. "Custom" must keep doing exactly this, unchanged."""
    from modules.cleanup import cleanup_scanner as cs
    from PyQt6.QtWidgets import QMessageBox

    tab = _built_tab_with_result(qapp, safe=2, caution=1, danger=1)
    idx = tab._preset_combo.findData("custom")
    tab._preset_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    def fake_run_clean_safe(widget, items, **kwargs):
        captured["items"] = items
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)

    tab._do_clean_all_safe()

    assert len(captured["items"]) == 2
    assert all(i.safety == "safe" for i in captured["items"])


def test_thorough_preset_includes_caution_items_in_the_clean_call(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    tab = _built_tab_with_result(qapp, safe=2, caution=3, danger=1)
    idx = tab._preset_combo.findData("thorough")
    tab._preset_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    def fake_run_clean_safe(widget, items, **kwargs):
        captured["items"] = items
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)

    tab._do_clean_all_safe()

    assert len(captured["items"]) == 5
    assert all(i.safety in ("safe", "caution") for i in captured["items"])


def test_aggressive_preset_still_excludes_never_included_scanners(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    tab = _built_tab_with_result(qapp, safe=1, danger=1)
    # Force the "temp" category's scanner name to a NEVER_INCLUDED one to
    # prove the exclusion is genuinely applied end-to-end, not just in
    # cleanup_presets.py's own unit tests.
    tab._id_to_scanner_name["temp"] = "scan_orphaned_user_profiles"
    idx = tab._preset_combo.findData("aggressive")
    tab._preset_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    def fake_run_clean_safe(widget, items, **kwargs):
        captured["items"] = items
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)

    tab._do_clean_all_safe()

    assert captured.get("items", []) == []


def test_has_cleanable_items_is_preset_aware(qapp):
    tab = _built_tab_with_result(qapp, caution=1)  # no "safe" items at all

    idx = tab._preset_combo.findData("light")
    tab._preset_combo.setCurrentIndex(idx)
    assert tab._has_cleanable_items() is False, "Light must not see a caution-only result as cleanable"

    idx = tab._preset_combo.findData("thorough")
    tab._preset_combo.setCurrentIndex(idx)
    assert tab._has_cleanable_items() is True, "Thorough must see the caution item as cleanable"
