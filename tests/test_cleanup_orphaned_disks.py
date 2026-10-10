r"""A virtual disk nothing on the machine can open is a different thing.

`scan_virtual_disk_images` reports every large .vhd/.vhdx/.vdi/.vmdk as
`danger`, unselected, on the reasoning that the space is real but the way
to reclaim it is to COMPACT the image rather than delete it — deleting one
destroys a VM.

That reasoning assumes there is still a VM. On the machine this was
written for there is not:

    E:\VMs\Ubuntu\Ubuntu.vdi   11.05 GB, dated Feb 2026
    HKLM\SOFTWARE\Oracle       does not exist
    ~\.VirtualBox              does not exist
    ~\VirtualBox VMs           does not exist
    VBoxManage.exe             not on PATH, not in either Program Files

VirtualBox was uninstalled and left 11 GB behind. Nothing on this machine
can open that file, there is no VM to destroy, and no compaction tool
exists to shrink it — so "danger: compact, do not delete" is exactly the
wrong advice. It is an orphan, and saying so is the whole value.

Two scanners rather than one flag, because a ScanItem carries no label of
its own: the tab takes its label from the scanner, so the distinction has
to live at that level to reach the screen.
"""
import os

import pytest

from _sparse import make_sparse
from modules.cleanup.cleanup_scanner import virtual_disks


def _make_disk(directory, name, gigabytes=2):
    path = directory / name
    make_sparse(path, int(gigabytes * 1024 ** 3))      # the size, without the 2 GB of zeros
    return path


# ── which hypervisors are really here ──────────────────────────────────

def test_a_hypervisor_that_is_not_installed_is_not_reported():
    """Against this machine, not a mock: VirtualBox is gone from it."""
    installed = virtual_disks.installed_hypervisors()
    assert isinstance(installed, set)
    if os.path.exists(r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"):
        pytest.skip("VirtualBox is installed here after all")
    assert "virtualbox" not in installed


def test_wsl_needs_more_than_wsl_exe_existing():
    r"""`wsl.exe` ships with Windows 11 whether or not WSL is installed.

    Treating its presence as "WSL is here" is the same mistake as treating
    a refused read as an answer — on this machine `wsl --list` says "The
    Windows Subsystem for Linux is not installed".
    """
    if virtual_disks._wsl_has_distros():
        pytest.skip("WSL really is installed here")
    assert "wsl" not in virtual_disks.installed_hypervisors()


@pytest.mark.parametrize("suffix,expected", [
    (".vdi", "virtualbox"),
    (".vmdk", "vmware"),
    (".qcow2", "qemu"),
])
def test_each_image_format_names_the_thing_that_opens_it(suffix, expected):
    assert virtual_disks.hypervisor_for(f"disk{suffix}") == expected


def test_vhdx_can_belong_to_more_than_one_thing():
    """Hyper-V and WSL2 both use .vhdx, so either being present counts."""
    assert virtual_disks.hypervisor_for("ext4.vhdx") in ("hyperv", "wsl")


# ── the two scanners ───────────────────────────────────────────────────

def test_an_image_with_no_hypervisor_is_reported_as_orphaned(
        tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    _make_disk(tmp_path, "Ubuntu.vdi")
    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(virtual_disks, "installed_hypervisors", lambda: set())

    orphaned = [i.path for i in ss.scan_orphaned_virtual_disks().items]
    in_use = [i.path for i in ss.scan_virtual_disk_images().items]
    assert any(p.endswith("Ubuntu.vdi") for p in orphaned)
    assert in_use == [], "an image nothing can open was called in-use"


def test_an_image_whose_hypervisor_is_installed_is_not_orphaned(
        tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    _make_disk(tmp_path, "Ubuntu.vdi")
    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(virtual_disks, "installed_hypervisors",
                        lambda: {"virtualbox"})

    orphaned = [i.path for i in ss.scan_orphaned_virtual_disks().items]
    in_use = [i.path for i in ss.scan_virtual_disk_images().items]
    assert orphaned == []
    assert any(p.endswith("Ubuntu.vdi") for p in in_use)


def test_neither_scanner_ever_pre_selects_anything(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    _make_disk(tmp_path, "Ubuntu.vdi")
    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    for installed in (set(), {"virtualbox"}):
        monkeypatch.setattr(virtual_disks, "installed_hypervisors",
                            lambda inst=installed: inst)
        for scanner in (ss.scan_orphaned_virtual_disks,
                        ss.scan_virtual_disk_images):
            for item in scanner().items:
                assert item.selected is False


def test_an_orphan_is_caution_not_danger(tmp_path, monkeypatch):
    """There is no VM to destroy, so `danger` overstates it — but it is
    still someone's disk image, so it is not `safe` either."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    _make_disk(tmp_path, "Ubuntu.vdi")
    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(virtual_disks, "installed_hypervisors", lambda: set())

    items = ss.scan_orphaned_virtual_disks().items
    assert items and all(i.safety == "caution" for i in items)


def test_the_orphan_scanner_is_offered_by_a_tab():
    from modules.cleanup.cleanup_module import LARGE_EXTRA

    assert "scan_orphaned_virtual_disks" in {fn.__name__ for fn in LARGE_EXTRA}


def test_this_machines_real_vdi_is_reported_as_an_orphan():
    """The finding that prompted all of this, asserted against the disk."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    vdi = r"E:\VMs\Ubuntu\Ubuntu.vdi"
    if not os.path.exists(vdi):
        pytest.skip("not this machine")
    if "virtualbox" in virtual_disks.installed_hypervisors():
        pytest.skip("VirtualBox has been installed since")
    found = {i.path.lower() for i in ss.scan_orphaned_virtual_disks().items}
    assert vdi.lower() in found
