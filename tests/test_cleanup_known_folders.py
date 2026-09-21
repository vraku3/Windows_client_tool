r"""User-data scanners must look where the user's data actually is.

Six scanners built their target list from `os.path.expanduser("~")` plus a
hardcoded folder name. Documents, Desktop and Pictures are routinely
redirected — OneDrive Backup does it by default, and Windows has always
allowed it. Measured on this machine:

    ~/Desktop        DOES NOT EXIST     OneDrive/Desktop     57 entries
    ~/Pictures       DOES NOT EXIST     OneDrive/Pictures  1989 entries
    ~/Documents      10 entries         OneDrive/Documents  225 entries

So Large Items, duplicates, old files and empty folders were all reporting
on a near-empty shadow of the real profile, silently — a missing directory
is not an error to any of them.

`SHGetKnownFolderPath` is what Explorer itself asks, and it answers with the
redirected path.

The second half is the trap that comes with the fix: once these scanners
reach the real OneDrive folders, anything that OPENS a file hydrates it —
Files On-Demand downloads the whole thing from the cloud. `_hash_file_fast`
opens every file in a size-collision group, so duplicate detection would
quietly pull gigabytes over the network. Placeholders are skipped by
attribute, without opening them.
"""
import os

import pytest

from modules.cleanup.cleanup_scanner import known_folders as kf


def _registry_shell_folder(value_name: str):
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
    try:
        raw, _ = winreg.QueryValueEx(key, value_name)
    finally:
        key.Close()
    return os.path.expandvars(raw)


@pytest.mark.parametrize("folder,registry_name", [
    ("Documents", "Personal"),
    ("Desktop", "Desktop"),
    ("Pictures", "My Pictures"),
    ("Downloads", "{374DE290-123F-4565-9164-39C4925E467B}"),
])
def test_known_folder_agrees_with_what_explorer_was_told(folder, registry_name):
    """Independent source: the registry the shell reads redirection from."""
    expected = _registry_shell_folder(registry_name)
    assert os.path.normcase(os.path.normpath(kf.known_folder(folder))) == \
        os.path.normcase(os.path.normpath(expected))


def test_an_unknown_folder_name_is_none_not_a_guess():
    assert kf.known_folder("NotARealKnownFolder") is None


def test_user_scan_dirs_are_the_resolved_folders_not_home_plus_a_name():
    dirs = {os.path.normcase(os.path.normpath(d))
            for d in kf.user_data_dirs()}
    documents = os.path.normcase(os.path.normpath(kf.known_folder("Documents")))
    assert documents in dirs


@pytest.fixture(scope="module")
def redirected_folder(tmp_path_factory):
    """A stand-in for a redirected known folder, with bait for all six.

    Built once: one member is a 105 MB file, and writing that per test
    would dominate the run.
    """
    import time

    target = tmp_path_factory.mktemp("Redirected")

    big = target / "big.iso"                       # large files, iso/vhd,
    with open(big, "wb") as handle:                # downloads-folder-old
        handle.seek(105 * 1024 * 1024 - 1)
        handle.write(b"\0")

    payload = b"d" * (200 * 1024)                  # duplicates: same size,
    (target / "copy-one.bin").write_bytes(payload)  # same content
    (target / "copy-two.bin").write_bytes(payload)

    stale = target / "stale.txt"                   # old files (> 6 months)
    stale.write_text("old")
    two_years_ago = time.time() - 730 * 86400
    os.utime(stale, (two_years_ago, two_years_ago))

    (target / "outer" / "inner").mkdir(parents=True)   # empty folders,
    return target                                       # at depth 2


@pytest.mark.parametrize("scanner_name", [
    "scan_large_files",
    "scan_duplicate_files",
    "scan_old_files",
    "scan_empty_folders",
    "scan_downloads_folder_old",
    "scan_iso_vhd_files",
])
def test_the_user_data_scanners_find_a_redirected_folder(
        scanner_name, redirected_folder, monkeypatch):
    """Point the known folders at a temp tree; the scanner must look there."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    target = str(redirected_folder)
    monkeypatch.setattr(ss.known_folders, "known_folder", lambda name: target)
    # scan_large_files/scan_duplicate_files/scan_old_files now call
    # user_data_dirs(FILE_SWEEP_SAFE_FOLDERS) with an explicit folder list
    # (excluding Pictures/Videos -- see FILE_SWEEP_SAFE_FOLDERS's own
    # docstring), so the stub must accept an optional positional arg too.
    monkeypatch.setattr(ss.known_folders, "user_data_dirs",
                        lambda folders=None: [target])

    result = getattr(ss, scanner_name)(min_age_days=0)
    looked_here = any(target.lower() in item.path.lower()
                      for item in result.items)
    assert looked_here, (
        f"{scanner_name} did not look in the redirected folder; "
        f"it reported {[i.path for i in result.items][:5]}")


def test_a_cloud_placeholder_is_not_opened_when_hashing(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    placeholder = tmp_path / "in-the-cloud.bin"
    placeholder.write_bytes(b"x" * 4096)

    monkeypatch.setattr(ss.known_folders, "is_cloud_placeholder",
                        lambda path: True)

    def _explode(*args, **kwargs):
        raise AssertionError(
            "hashing opened a cloud placeholder — that downloads the file")

    monkeypatch.setattr("builtins.open", _explode)
    assert ss._hash_file_fast(str(placeholder)) is None


def test_a_normal_file_is_still_hashed(tmp_path):
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    local = tmp_path / "local.bin"
    local.write_bytes(b"y" * 4096)
    assert ss._hash_file_fast(str(local))


def test_is_cloud_placeholder_says_no_for_an_ordinary_file(tmp_path):
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("hello")
    assert kf.is_cloud_placeholder(str(ordinary)) is False


def test_is_cloud_placeholder_says_no_for_a_path_that_is_not_there(tmp_path):
    assert kf.is_cloud_placeholder(str(tmp_path / "nope")) is False
