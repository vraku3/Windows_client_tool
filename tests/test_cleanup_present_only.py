r"""A tab should offer the scanners that apply to this machine.

Measured: 71 of 456 catalog specs match anything here.

    dev      8 / 121      games    3 / 83       apps   8 / 75
    media    1 / 50       cloud    3 / 38       comms  0 / 35
    browsers 7 / 13       system  41 / 41

The App & Game Caches tab listed 301 scanners for about a dozen that
apply, and Dev Tools listed 122 for eight. That is not a display quibble:
it is what a scan iterates, and it buries the entries that found something
under hundreds that never could.

The catalog keeps all of them — knowledge about where an application
caches things is worth having whether or not that application is installed
today — but a tab is built from the ones whose targets are actually on
this machine. The filter is applied when the tab is built, so installing
something new means restarting the app; that is a fair trade for not
walking 385 dead paths on every sweep.
"""
import os

import pytest

from modules.cleanup.cleanup_scanner import catalog


CATEGORIES = ("system", "browsers", "apps", "games", "media", "comms",
              "cloud", "dev")


@pytest.mark.parametrize("category", CATEGORIES)
def test_present_only_is_a_subset_of_everything_defined(category):
    everything = {fn.__name__ for fn in catalog.scanners_for(category)}
    present = {fn.__name__ for fn in
               catalog.scanners_for(category, present_only=True)}
    assert present <= everything


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_offered_scanner_has_a_target_on_this_machine(category):
    specs = catalog.load_catalog()
    for fn in catalog.scanners_for(category, present_only=True):
        spec = specs[fn.__name__[len("scan_"):]]
        assert any(os.path.exists(t) for t in catalog.targets_of(spec)), (
            f"{spec.id} was offered but points at nothing that exists")


def test_a_spec_pointing_nowhere_is_not_offered(monkeypatch):
    absent = catalog.ScannerSpec(
        id="ghost", label="Ghost", category="apps",
        paths=[r"%LOCALAPPDATA%\definitely-not-here-9f3ab"])
    real = dict(catalog.load_catalog())
    real["ghost"] = absent
    monkeypatch.setattr(catalog, "_catalog", real)

    offered = {fn.__name__ for fn in
               catalog.scanners_for("apps", present_only=True)}
    assert "scan_ghost" not in offered
    assert "scan_ghost" in {fn.__name__ for fn in catalog.scanners_for("apps")}


def test_the_catalog_still_defines_everything():
    """Filtering is a display and scan decision, never a deletion.

    The floor was lowered from 456 to 455 on 2026-09-22, in the same
    commits that deliberately removed 12 catalog entries for real safety
    reasons (net_sdk_cache's overclaimed/duplicated path; 8 password-manager/
    wallet vaults and 2 cloud-CLI credential stores marked "safe"/"caution"
    over their whole app-data root; phantom_cache -- see
    test_cleanup_no_credential_vault_scanners.py) -- an intentional,
    reviewed removal, not the silent present_only-filtering shrinkage
    this test exists to catch.

    Lowered again, 455 to 448, on 2026-09-23: 7 more entries removed for
    the same reason, this time reaching real save games or significant
    user-created content instead of credentials (scrivener_cache,
    factorio_cache, stardew_cache, godot_cache, gta_v_cache,
    snowrunner_cache, warcraft_3_cache).

    Lowered again, 448 to 446, same day: joplin_cache (local-first note
    app, real notes database) and microsoft_templates (real, user-saved
    Office templates, never actually cache) removed for the same reason.

    Lowered again, 446 to 440, same day: cinema4d_cache, dbeaver_cache,
    qgis_cache, sas_cache, kontakt_cache, logic_pro_cache removed --
    real saved work, connection credentials, or license/activation state
    reached via a whole-folder "safe" tier scope.
    """
    assert len(catalog.load_catalog()) >= 440


def test_the_app_tab_is_not_mostly_scanners_that_cannot_apply(qapp):
    from modules.cleanup.cleanup_module import _with_catalog

    offered = _with_catalog(
        {}, "apps", "games", "media", "comms", "cloud", "browsers")
    defined = sum(
        len(catalog.scanners_for(category))
        for category in ("apps", "games", "media", "comms", "cloud", "browsers"))

    assert len(offered) < defined / 2, (
        f"the tab still offers {len(offered)} of {defined} scanners")


def test_hand_written_scanners_are_never_filtered_out():
    """They build their own targets, so there is no path list to test."""
    from modules.cleanup.cleanup_module import SYSTEM_EXTRA, _with_catalog

    offered = {fn.__name__ for fn in _with_catalog(dict(SYSTEM_EXTRA), "system")}
    for fn in SYSTEM_EXTRA:
        assert fn.__name__ in offered
