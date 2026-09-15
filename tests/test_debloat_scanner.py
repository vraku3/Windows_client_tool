import json
import os

from modules.debloat import debloat_scanner as ds

_CATALOG = os.path.join(os.path.dirname(__file__), "..", "src", "modules",
                        "tweaks", "definitions", "debloat.json")


def _catalog_packages():
    with open(_CATALOG, encoding="utf-8") as f:
        entries = json.load(f)
    return {e["package"] for e in entries if e.get("package")}


def test_every_catalog_package_is_in_known_packages():
    """The bug: 12 debloat.json entries — Recall, Copilot, SecHealthUI
    among them — could never be detected as installed because
    KNOWN_PACKAGES never listed their package id."""
    missing = _catalog_packages() - ds.KNOWN_PACKAGES
    assert missing == set(), (
        f"in debloat.json but not KNOWN_PACKAGES (never detectable): "
        f"{sorted(missing)}")


def test_check_app_installed_was_removed():
    """Dead code: defined, called nowhere in src/."""
    assert not hasattr(ds, "check_app_installed")


def test_get_installed_packages_or_none_preserves_a_failed_enumeration(
        monkeypatch):
    """A failed AppX enumeration must not collapse into "0 apps
    installed" -- Debloat's scan needs to tell the two apart to report a
    failed scan honestly instead of a suspiciously clean one."""
    monkeypatch.setattr(ds, "installed_names_or_none", lambda: None)
    assert ds.get_installed_packages_or_none() is None


def test_get_installed_packages_or_none_filters_known_packages(monkeypatch):
    monkeypatch.setattr(
        ds, "installed_names_or_none",
        lambda: ["Microsoft.WindowsCalculator", "SomeThirdPartyApp.NotCatalogued"])
    result = ds.get_installed_packages_or_none()
    assert result == {"Microsoft.WindowsCalculator": "Microsoft.WindowsCalculator"}
