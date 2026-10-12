r"""Superseded third-party drivers, and why this one is written fail-closed.

`DriverStore\FileRepository` is 7.13 GB on the machine this was written for
— the largest single reclaimable figure found anywhere in the cleanup work,
and the one nothing could safely offer. The old `driver_store` catalog entry
pointed at the whole folder as a single deletable item; ticking it costs the
machine its drivers, so it is disabled with its reason kept.

Reclaiming that space properly means removing SUPERSEDED driver packages one
at a time, which is a set difference over `pnputil /enum-drivers`. Three
things about that command make this delicate, and all three are pinned here:

* **Unelevated it prints its HELP TEXT and exits 0.** No error, nothing on
  stderr. A parser trusting the return code reports "no superseded drivers"
  on every unelevated machine, forever — the same shape as `Get-Tpm`
  answering `{"TpmPresent":null}` and `gpresult /x` silently dropping the
  computer half. The refusal is detected by the ABSENCE of `Published Name:`
  records, never by rc.
* **The output is UTF-16.** Decoded as UTF-8 it is unparseable mojibake.
* **"Newer" is genuinely ambiguous sometimes.** On this machine `amdxe.inf`
  has oem60 at 09/09/2025 25.20.0.11 and oem53 at 09/23/2025 25.10.0.1 — a
  later DATE carrying a lower VERSION. Neither supersedes the other by both
  measures, so neither is offered. A wrong call here costs a driver.

Measured against the real 63-package output: 11 original names installed more
than once, and **11 packages superseded** — a naive all-but-the-newest rule
says 12, and the extra one is amdxe.inf's ambiguous pair, left alone.

The reclaimable figure is the other surprise. Of those 11, five have no
`DriverInfFiles` registry entry at all, so their size cannot be determined;
the six that can be measured come to **2 MB**. The 7.13 GB driver store is
almost entirely current, in-use drivers. Sizes that cannot be taken are
reported as unknown, never as zero.
"""
import pathlib

import pytest

from modules.cleanup.cleanup_scanner import driver_store

FIXTURE = pathlib.Path(__file__).resolve().parent / "data" / "pnputil_enum_drivers.txt"


@pytest.fixture
def packages():
    return driver_store.parse_enum_drivers(FIXTURE.read_text(encoding="utf-8"))


# ── parsing ────────────────────────────────────────────────────────────

def test_every_record_is_read(packages):
    assert len(packages) == 9
    assert packages[0].published == "oem49.inf"
    assert packages[0].original == "amd3dvcache.inf"
    assert packages[0].provider == "Advanced Micro Devices, Inc."


def test_the_version_and_date_are_split_apart(packages):
    first = packages[0]
    assert first.version == (1, 0, 0, 12)
    assert (first.date.year, first.date.month, first.date.day) == (2025, 9, 26)


def test_utf16_output_is_decoded(tmp_path):
    """pnputil writes UTF-16; decoded as UTF-8 it is unparseable."""
    raw = FIXTURE.read_text(encoding="utf-8").encode("utf-16")
    assert len(driver_store.parse_enum_drivers(raw)) == 9


def test_the_help_text_is_a_refusal_not_an_empty_driver_store():
    """Unelevated, pnputil prints usage and exits 0."""
    help_text = (
        "PNPUTIL [/add-driver <...> | /delete-driver <...> |\n"
        "         /enum-drivers [<...>] | /?]\n\nCommands:\n\n"
        "  /add-driver <filename.inf | *.inf> [/subdirs] [/install]\n")
    assert driver_store.parse_enum_drivers(help_text) is None


def test_empty_output_is_a_refusal_too():
    assert driver_store.parse_enum_drivers("") is None


# ── which packages are superseded ──────────────────────────────────────

def test_an_older_version_of_the_same_inf_is_superseded(packages):
    superseded = {p.published for p in driver_store.superseded_packages(packages)}
    assert "oem35.inf" in superseded, "1.0.0.11 not superseded by 1.0.0.12"
    assert "oem49.inf" not in superseded, "the newest was offered for removal"


def test_all_but_the_newest_of_a_three_way_group_are_superseded(packages):
    superseded = {p.published for p in driver_store.superseded_packages(packages)}
    assert {"oem7.inf", "oem56.inf"} <= superseded
    assert "oem62.inf" not in superseded


def test_a_lone_package_is_never_superseded(packages):
    superseded = {p.published for p in driver_store.superseded_packages(packages)}
    assert "oem10.inf" not in superseded
    assert "oem18.inf" not in superseded


def test_a_newer_date_with_an_older_version_is_left_alone(packages):
    """amdxe.inf, from this machine. Ambiguous, so neither is offered."""
    superseded = {p.published for p in driver_store.superseded_packages(packages)}
    assert "oem60.inf" not in superseded
    assert "oem53.inf" not in superseded


def test_packages_of_different_classes_never_supersede_each_other():
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "Class GUID:         {4d36e968-e325-11ce-bfc1-08002be10318}\n"
        "Driver Version:     11/12/2025 32.0.21036.18",
        "Class GUID:         {00000000-0000-0000-0000-000000000000}\n"
        "Driver Version:     11/12/2025 32.0.21036.18", 1)
    parsed = driver_store.parse_enum_drivers(text)
    superseded = {p.published for p in driver_store.superseded_packages(parsed)}
    assert "oem7.inf" not in superseded


def test_a_refused_enumeration_supersedes_nothing():
    assert driver_store.superseded_packages(None) == []


# ── mapping a package to its bytes ─────────────────────────────────────

def test_the_store_folder_comes_from_the_driver_database():
    r"""HKLM\SYSTEM\DriverDatabase\DriverInfFiles\<published>\Active, which
    is readable WITHOUT elevation — only pnputil needs it."""
    # oemNN numbers are reassigned as drivers come and go (oem49 was AMD's
    # 3D V-Cache driver until a spacedesk install on 2026-10-12), so this pins
    # the RULE -- the registry names a real FileRepository folder -- not a package.
    import os
    import re
    folder = driver_store.store_folder_for("oem49.inf")
    if folder is None:
        pytest.skip("oem49.inf is not published on this machine")
    assert re.fullmatch(r"[\w.\-]+\.inf_[a-z0-9]+_[0-9a-f]{16}", folder.lower()), folder
    repo = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "DriverStore", "FileRepository")
    assert os.path.isdir(os.path.join(repo, folder)), folder


def test_an_unknown_published_name_has_no_folder():
    assert driver_store.store_folder_for("oem99999.inf") is None


def test_a_package_with_no_folder_reports_UNKNOWN_not_zero():
    """None, never 0 — "could not measure" is not "is empty".

    Five of the eleven superseded packages on this machine have no
    DriverInfFiles entry at all, so folding them in as zero understated
    the reclaimable total without saying so.
    """
    from modules.cleanup.cleanup_scanner.driver_store import DriverPackage
    import datetime

    ghost = DriverPackage(
        published="oem99999.inf", original="ghost.inf", provider="Nobody",
        class_guid="{0}", version=(1, 0), date=datetime.date(2020, 1, 1))
    assert driver_store.package_size(ghost) is None


def test_packages_that_cannot_be_sized_are_reported_separately(
        monkeypatch, packages):
    monkeypatch.setattr(driver_store, "enumerate_packages", lambda: packages)
    monkeypatch.setattr(
        driver_store, "package_size",
        lambda pkg: None if pkg.published == "oem7.inf" else 1000)

    report = driver_store.superseded_report()
    assert [p.published for p in report.unsized] == ["oem7.inf"]
    assert report.total_bytes == 2000, "an unmeasurable package was counted"
    assert "could not be located" in report.reason


# ── the report ─────────────────────────────────────────────────────────

def test_the_report_says_when_it_could_not_look(monkeypatch):
    monkeypatch.setattr(driver_store, "enumerate_packages", lambda: None)
    report = driver_store.superseded_report()
    assert report.available is False
    assert report.packages == []
    assert "elevat" in report.reason.lower()


def test_the_report_totals_only_the_superseded(monkeypatch, packages):
    monkeypatch.setattr(driver_store, "enumerate_packages", lambda: packages)
    monkeypatch.setattr(driver_store, "package_size", lambda pkg: 1000)

    report = driver_store.superseded_report()
    assert report.available is True
    assert {p.published for p in report.packages} == {
        "oem35.inf", "oem7.inf", "oem56.inf"}
    assert report.total_bytes == 3000
