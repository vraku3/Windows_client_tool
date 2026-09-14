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
