"""Software inventory: runtimes, duplicates, end of life, chips, winget, exports."""
import csv
import io
from datetime import date

import pytest

from modules.software_inventory import software_analysis as sa
from modules.software_inventory.software_reader import SoftwareEntry

TODAY = date(2026, 9, 26)


def _e(name, version="1.0", publisher="Pub", install_date="", type_="64-bit", **kw):
    return SoftwareEntry(name=name, version=version, publisher=publisher, install_date=install_date,
                         size_mb="", type_=type_, source="registry", **kw)


def test_original_constructor_shape_still_works():
    e = SoftwareEntry("n", "1", "p", "20260101", "1.0 MB", "64-bit", "registry", "x.exe")
    assert e.uninstall_string == "x.exe" and e.product_code == "" and e.msi_uninstall == ""


def test_public_import_path_is_kept():
    from modules.software_inventory.software_module import SoftwareEntry as Old, fetch_software
    assert Old is SoftwareEntry and callable(fetch_software)


@pytest.mark.parametrize("text,expected", [
    ("20260924", date(2026, 9, 24)), ("2026/09/24", date(2026, 9, 24)), ("", None),
    ("not a date", None), ("20261399", None)])
def test_install_date(text, expected):
    assert sa.parse_install_date(text) == expected


def test_version_key_orders_numerically():
    assert sa.version_key("1.18.32") > sa.version_key("1.18.9")
    assert sa.version_key("10.0") > sa.version_key("9.9")
    assert sa.version_key("Unknown") == ()


@pytest.mark.parametrize("name,family", [
    ("Microsoft Visual C++ 2010  x64 Redistributable - 10.0.40219", "Visual C++ 2010"),
    ("Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247", "Visual C++ 2015-2022"),
    ("Microsoft Visual C++ 2022 X86 Minimum Runtime - 14.51.36247", "Visual C++ 2015-2022"),
    ("Microsoft Windows Desktop Runtime - 8.0.10 (x64)", ".NET 8"),
    ("Microsoft .NET Runtime - 6.0.36 (x64)", ".NET 6"),
    ("Microsoft ASP.NET Core 7.0.20 - Shared Framework", ".NET 7"),
    ("Java 8 Update 401 (64-bit)", "Java"),
    ("Eclipse Temurin JDK with Hotspot 17.0.9+9 (x64)", "Java"),
    ("Microsoft Edge WebView2 Runtime", "WebView2"),
    ("Visual Studio Code", ""), ("JavaScript Toolkit", ""), ("Microsoft Visual C++ Build Tools", "")])
def test_runtime_family(name, family):
    assert sa.runtime_family(name) == family


def test_duplicates_need_distinct_versions_and_keep_architectures_apart():
    ents = [_e("Java 8 Update 381 (64-bit)", "8.0.3810"), _e("Java 8 Update 401 (64-bit)", "8.0.4010"),
            _e("Java 8 Update 401", "8.0.4010", type_="32-bit"),          # x86 build: different product
            _e("Same App", "2.0"), _e("Same App", "2.0", type_="32-bit"),  # identical versions: not a dup
            _e("Microsoft Visual C++ 2010 x64 Redistributable - 10.0.40219", "10.0.40219"),
            _e("Microsoft Visual C++ 2013 x64 Redistributable - 12.0.30501", "12.0.30501")]
    groups = sa.find_duplicates(ents)
    assert len(groups) == 1
    assert [e.version for e in groups[0].entries] == ["8.0.4010", "8.0.3810"]


def test_updates_and_versionless_entries_are_not_duplicates():
    ents = [_e("KB123 Security Update", "1", is_update=True), _e("KB123 Security Update", "2", is_update=True),
            _e("NoVersion", ""), _e("NoVersion", "")]
    assert sa.find_duplicates(ents) == []


def test_eol_rules():
    assert sa.eol_for(_e("Microsoft Visual C++ 2010 x86 Redistributable")).end_of_support == date(2020, 7, 14)
    assert sa.eol_for(_e("Microsoft Visual C++ 2022 X64 Minimum Runtime")) is None
    assert sa.eol_for(_e("Microsoft .NET Runtime - 6.0.36 (x64)")).end_of_support == date(2024, 11, 12)
    assert sa.eol_for(_e("Microsoft Windows Desktop Runtime - 10.0.1 (x64)")).end_of_support == date(2028, 11, 14)
    assert sa.eol_for(_e("Python 2.7.18", publisher="Python")).product == "Python 2.7"
    assert sa.eol_for(_e("Python 3.12.10 (64-bit)")) is None
    assert sa.eol_for(_e("Java 7 Update 80")).product == "Java 7"
    assert sa.eol_for(_e("Java 8 Update 401")) is None
    assert sa.eol_for(_e("Adobe Flash Player 32 NPAPI")).product == "Adobe Flash Player"
    assert sa.eol_for(_e("Microsoft Office Professional Plus 2013", publisher="Microsoft Corporation")).product \
        == "Microsoft Office 2013"
    assert sa.eol_for(_e("Notepad++")) is None


def test_chips_and_counts():
    ents = [
        _e("App A", "1", publisher="A", install_date="20150101"),                  # old app
        _e("Hidden Thing", "1", system_component=True),
        _e("Microsoft Visual C++ 2010 x64 Redistributable - 10.0.40219", "10.0.40219"),
        _e("Tool", "1", publisher="", type_="User"),                                # no publisher, user
        _e("Legacy 32", "1", type_="32-bit"),
        _e("Dup", "1"), _e("Dup", "2"),
    ]
    rows = sa.analyze(ents, today=TODAY, winget={"tool": "2.0"})
    counts = sa.chip_counts(rows)
    assert counts["All"] == 7
    assert counts["Apps"] == 6 and counts["System components"] == 1
    assert counts["Runtimes"] == 1 and counts["End of life"] == 1
    assert counts["Duplicates"] == 1 and counts["User install"] == 1 and counts["32-bit"] == 1
    assert counts[f"Old ({sa.OLD_YEARS}+ yr)"] == 1 and counts["No publisher"] == 1
    assert counts["Updates available"] == 1
    assert [r.name for r in sa.filter_rows(rows, "Duplicates")] == ["Dup"]


def test_no_winget_check_leaves_the_chip_empty_not_up_to_date():
    rows = sa.analyze([_e("Tool")], today=TODAY, winget=None)
    assert sa.chip_counts(rows)["Updates available"] == 0 and rows[0].winget_available == ""


def test_search_matches_any_field_with_all_terms():
    rows = sa.analyze([_e("VLC media player", "3.0.23", publisher="VideoLAN"),
                       _e("Other", "3.0.23", publisher="X", product_code="{ABC}")], today=TODAY)
    assert [r.name for r in sa.filter_rows(rows, "All", "vlc 3.0")] == ["VLC media player"]
    assert [r.name for r in sa.filter_rows(rows, "All", "{abc}")] == ["Other"]
    assert len(sa.filter_rows(rows, "All", "")) == 2


def test_future_install_date_gives_no_age():
    rows = sa.analyze([_e("X", install_date="20300101")], today=TODAY)
    assert rows[0].age_years is None


def test_findings():
    ents = [_e("Microsoft Visual C++ 2010 x64 Redistributable - 10.0.40219", "10.0.40219"),
            _e("Java 8 Update 381 (64-bit)", "8.0.3810"), _e("Java 8 Update 401 (64-bit)", "8.0.4010")]
    f = sa.software_findings(sa.analyze(ents, today=TODAY))
    titles = " | ".join(x.title for x in f)
    assert "Visual C++ 2010 runtime is past end of support" in titles
    assert "older Java version" in titles


def test_eol_soon_is_info_not_warning():
    ents = [_e("Microsoft Windows Desktop Runtime - 8.0.10 (x64)", "8.0.10")]
    f = sa.software_findings(sa.analyze(ents, today=date(2026, 9, 26)))
    assert f and f[0].severity == "info" and "ends in 45 days" in f[0].title


WINGET_OUT = """Name                                Id                         Version       Available     Source
-------------------------------------------------------------------------------------------------
Battle.net                          Blizzard.BattleNet         Unknown       1.19.3.3219   winget
Microsoft Edge                      Microsoft.Edge             153.0.4234.48 154.0.4258.37 winget
OCCT                                OCBase.OCCT.Personal       17.1.3.0      17.1.5.0      winget
3 upgrades available.
"""


def test_parse_winget_upgrades():
    got = sa.parse_winget_upgrades(WINGET_OUT)
    assert got == {"battle.net": "1.19.3.3219", "microsoft edge": "154.0.4258.37", "occt": "17.1.5.0"}


def test_winget_failure_is_none_and_no_upgrades_is_empty():
    assert sa.parse_winget_upgrades("winget not found. Please install App Installer.") is None
    assert sa.parse_winget_upgrades("") is None
    assert sa.parse_winget_upgrades("No installed package found matching input criteria.") == {}


def test_exports_round_trip():
    ents = [_e("A, Inc \"Tool\"", "1.0", product_code="{11111111-1111-1111-1111-111111111111}",
               windows_installer=True, uninstall_string="MsiExec.exe /I{...}")]
    rows = sa.analyze(ents, today=TODAY)
    parsed = list(csv.reader(io.StringIO(sa.rows_to_csv(rows))))
    assert parsed[0] == sa.EXPORT_COLUMNS
    assert parsed[1][0] == 'A, Inc "Tool"'
    assert parsed[1][sa.EXPORT_COLUMNS.index("Quiet uninstall")].endswith("/qn")
    md = sa.rows_to_markdown(rows, "H")
    assert md.startswith("### Installed software: H (1)") and "| A, Inc" in md


def test_detail_text_names_product_code_and_commands():
    e = _e("Thing", "2.0", product_code="{22222222-2222-2222-2222-222222222222}",
           uninstall_string="MsiExec.exe /I{x}", quiet_uninstall="q.exe /S")
    text = sa.detail_text(sa.analyze([e], today=TODAY)[0])
    assert "msiexec /x {22222222" in text and "Quiet uninstall: q.exe /S" in text


def test_reader_size_blank_when_not_recorded():
    from modules.software_inventory.software_reader import _size_text
    dash = chr(0x2014)      # never "0.0 MB": a measurement that was never taken
    assert _size_text(0) == dash and _size_text("x") == dash and _size_text(2048) == "2.0 MB"


def test_real_machine_inventory_is_plausible():
    from modules.software_inventory import software_reader as sr
    full = sr.fetch_software_inventory()
    legacy = sr.fetch_software()
    assert len(full) >= len(legacy) > 5
    assert all(e.name for e in full)
    rows = sa.analyze(full)
    counts = sa.chip_counts(rows)
    assert counts["All"] == len(full) and counts["Apps"] + counts["System components"] >= counts["Apps"]
    for e in full:
        if e.product_code:
            assert e.msi_uninstall.startswith("msiexec /x {")


# ---------------------------------------------------------------------------
# Broken uninstallers
# ---------------------------------------------------------------------------

def test_uninstaller_target_status_none_when_not_applicable():
    assert sa.uninstaller_target_status(_e("No string", uninstall_string="")) is None
    assert sa.uninstaller_target_status(
        _e("MSI", uninstall_string="MsiExec.exe /I{x}", windows_installer=True)) is None
    assert sa.uninstaller_target_status(
        _e("Winget", uninstall_string="winget uninstall --product-code X")) is None
    assert sa.uninstaller_target_status(
        _e("MsiexecRaw", uninstall_string="MsiExec.exe /X{22222222-2222-2222-2222-222222222222}")) is None


def test_uninstaller_target_status_true_when_file_genuinely_missing(tmp_path):
    missing = tmp_path / "does_not_exist" / "uninst.exe"
    e = _e("Gone", uninstall_string=f'"{missing}" /S')
    assert sa.uninstaller_target_status(e) is True


def test_uninstaller_target_status_false_for_real_file(tmp_path):
    real = tmp_path / "uninst.exe"
    real.write_bytes(b"x")
    e = _e("Present", uninstall_string=f'"{real}" /S')
    assert sa.uninstaller_target_status(e) is False


def test_uninstaller_target_status_unquoted_path_with_spaces(tmp_path):
    sub = tmp_path / "Program Files" / "Thing"
    sub.mkdir(parents=True)
    real = sub / "uninst.exe"
    real.write_bytes(b"x")
    e = _e("Unquoted", uninstall_string=f"{real} /S")
    assert sa.uninstaller_target_status(e) is False


def test_broken_uninstaller_tag_and_finding_and_note(tmp_path):
    missing = tmp_path / "ghost.exe"
    e = _e("Ghost App", uninstall_string=f'"{missing}" /S')
    rows = sa.analyze([e], today=TODAY)
    row = rows[0]
    assert row.uninstaller_missing is True
    assert "Broken uninstaller" in row.tags
    assert sa.chip_counts(rows)["Broken uninstaller"] == 1
    assert "uninstaller program missing" in sa._note(row)
    findings = sa.software_findings(rows)
    assert any("no longer exists" in f.title and "Ghost App" in f.detail for f in findings)
    assert "BROKEN" in sa.detail_text(row)


def test_broken_uninstaller_absent_for_healthy_entry():
    e = _e("Healthy", uninstall_string="MsiExec.exe /I{x}", windows_installer=True)
    rows = sa.analyze([e], today=TODAY)
    row = rows[0]
    assert row.uninstaller_missing is None
    assert "Broken uninstaller" not in row.tags
    assert not sa.software_findings(rows)


def test_real_machine_broken_uninstaller_check_runs_clean():
    """Every non-MSI, non-winget uninstall string on THIS machine currently
    resolves to a real file -- confirmed by direct registry probe (2026-10-01)
    before building this check. Asserts the detector agrees, not that it finds
    something: a module this mature having zero broken uninstallers right now
    is the expected, correct answer."""
    from modules.software_inventory import software_reader as sr
    full = sr.fetch_software_inventory()
    rows = sa.analyze(full)
    broken = [r for r in rows if r.uninstaller_missing is True]
    assert broken == [], f"unexpected broken uninstallers found: {[r.name for r in broken]}"
