r"""The categories that hold real bytes and had no scanner.

Measured against the two fixed drives on the machine this was written for,
diffing what the 537 scanners cover against what is actually on disk:

    NO       1.02 GB  C:\Windows\Installer      (only $PatchCache$ covered)
    NO       0.03 GB  C:\ProgramData\Package Cache
    NO           --   C:\Windows\Minidump
    NO      11.05 GB  E:\vms\ubuntu             (10 files: virtual disks)

Two of the gaps I expected to fill turned out to be covered elsewhere in
the app and are deliberately NOT duplicated here: hibernation is a Tweaks
power definition (`powercfg /h off`, not a file delete), and restore points
belong to the Restore Manager module, which already lists and deletes them.

The Windows Installer orphan scanner carries the one genuinely dangerous
question in this file, and it gets the most attention below: the cache
holds the .msi and .msp files Windows needs to REPAIR or UNINSTALL every
installed product. A file in there is junk only if no installed product
references it. If the reference list cannot be read, every file looks
orphaned — so a refused registry read must produce NOTHING, never 1 GB of
"safe to delete".
"""
import os

import pytest

from _sparse import make_sparse
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import catalog


# ── Windows Installer orphans ──────────────────────────────────────────

def test_only_the_unreferenced_packages_are_offered(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    installer = tmp_path / "Installer"
    installer.mkdir()
    referenced = installer / "referenced.msi"
    orphan = installer / "orphan.msi"
    for package in (referenced, orphan):
        package.write_bytes(b"x" * 2048)

    monkeypatch.setattr(ss, "_installer_cache_dir", lambda: str(installer))
    monkeypatch.setattr(ss, "_referenced_installer_packages",
                        lambda: {os.path.normcase(str(referenced))})

    found = {os.path.normcase(item.path)
             for item in ss.scan_orphaned_installer_packages().items}
    assert found == {os.path.normcase(str(orphan))}


def test_an_unreadable_reference_list_offers_nothing(tmp_path, monkeypatch):
    """The whole safety property. Empty set = we could not look."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    installer = tmp_path / "Installer"
    installer.mkdir()
    (installer / "a.msi").write_bytes(b"x" * 2048)
    (installer / "b.msp").write_bytes(b"x" * 2048)

    monkeypatch.setattr(ss, "_installer_cache_dir", lambda: str(installer))
    monkeypatch.setattr(ss, "_referenced_installer_packages", lambda: None)

    result = ss.scan_orphaned_installer_packages()
    assert result.items == [], (
        "a refused registry read offered the whole installer cache for "
        "deletion — that is every product's repair and uninstall data")


def test_the_reference_reader_finds_real_packages_on_this_machine():
    """Not a mock: if this returns nothing here, the scanner is inert."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    referenced = ss._referenced_installer_packages()
    if referenced is None:
        pytest.skip("installer registry not readable unelevated")
    assert referenced, "no installed product references any cached package"


def test_orphans_are_caution_never_safe():
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    for item in ss.scan_orphaned_installer_packages().items:
        assert item.safety == "caution"


# ── virtual disk images ────────────────────────────────────────────────

def test_virtual_disks_are_reported_but_never_pre_selected(
        tmp_path, monkeypatch):
    """An image whose hypervisor IS installed. The other case — nothing on
    the machine can open it — is tests/test_cleanup_orphaned_disks.py."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    from modules.cleanup.cleanup_scanner import virtual_disks

    disk = tmp_path / "ext4.vhdx"
    make_sparse(disk, 2 * 1024 * 1024 * 1024)

    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(virtual_disks, "installed_hypervisors",
                        lambda: {"hyperv"})

    items = ss.scan_virtual_disk_images().items
    assert any(item.path.lower().endswith("ext4.vhdx") for item in items)
    for item in items:
        assert item.selected is False, (
            "a virtual disk was pre-ticked — deleting one destroys a VM")
        assert item.safety == "danger"


def test_a_small_virtual_disk_is_not_worth_reporting(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    from modules.cleanup.cleanup_scanner import virtual_disks

    small = tmp_path / "tiny.vhdx"
    small.write_bytes(b"\0" * 1024)
    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(virtual_disks, "installed_hypervisors",
                        lambda: {"hyperv"})
    assert ss.scan_virtual_disk_images().items == []
    assert ss.scan_orphaned_virtual_disks().items == []


# ── new catalog entries ────────────────────────────────────────────────

@pytest.mark.parametrize("spec_id,expected_path_fragment", [
    ("minidump", r"Minidump"),
])
def test_the_new_catalog_entries_point_where_they_say(
        spec_id, expected_path_fragment):
    spec = catalog.load_catalog().get(spec_id)
    assert spec is not None, f"{spec_id} is not in the catalog"
    joined = " ".join(spec.paths)
    assert expected_path_fragment.lower() in joined.lower()


@pytest.mark.parametrize("scanner_name", [
    "scan_orphaned_installer_packages",
    "scan_virtual_disk_images",
])
def test_the_new_scanners_are_offered_by_a_tab(scanner_name):
    from modules.cleanup.cleanup_module import (
        LARGE_EXTRA, LOGS_EXTRA, SYSTEM_EXTRA)

    offered = {fn.__name__ for fn in
               (*SYSTEM_EXTRA, *LOGS_EXTRA, *LARGE_EXTRA)}
    assert scanner_name in offered


def test_the_new_scanners_are_exported():
    for name in ("scan_orphaned_installer_packages", "scan_virtual_disk_images"):
        assert callable(getattr(cs, name, None))
