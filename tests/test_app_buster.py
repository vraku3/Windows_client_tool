"""App Buster: the engine's rules, its deletion guards, verified actions, and the pane.

Synthetic cases carry the shapes measured on the real machine (2026-10-10):
bundle vs main package full names, Chrome's cr.sb.* folders under Packages,
a WiX/Burn bundle whose Package Cache copy is gone, the HKCR product
99E80CA9B0328E74791254777B1F42AE.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from modules.app_buster.engine import actions as act  # noqa: E402
from modules.app_buster.engine import desktop_apps, extras, orphans, recommend, views  # noqa: E402
from modules.app_buster.engine import model as m  # noqa: E402
from modules.app_buster.engine import windows_apps as wa  # noqa: E402
from modules.software_inventory.software_reader import SoftwareEntry  # noqa: E402


def rec(**kw) -> m.AppRecord:
    base = dict(key="k", name="App", type=m.WINDOWS)
    base.update(kw)
    return m.AppRecord(**base)


# ---- names and families ------------------------------------------------------------------

def test_bundle_and_main_package_share_a_family():
    bundle = "Microsoft.WindowsCalculator_2021.2607.0.0_neutral_~_8wekyb3d8bbwe"
    main = "Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe"
    assert wa.family_of(bundle) == wa.family_of(main) == "Microsoft.WindowsCalculator_8wekyb3d8bbwe"
    assert wa.split_full_name(main) == ("Microsoft.WindowsCalculator", "11.2607.0.0", "x64", "8wekyb3d8bbwe")


@pytest.mark.parametrize("name,ok", [
    ("Microsoft.BingWeather_8wekyb3d8bbwe", True),
    ("5319275A.WhatsAppDesktop_cv1g1gvanyjgm", True),
    ("cr.sb.cdm0B416A62BCCA696DA753309CE9690CFEEEB87B21", False),   # Chrome sandbox profile
    ("windows_ie_ac_001", False),                                   # IE AppContainer
])
def test_only_real_family_names_count(name, ok):
    assert wa.is_family_name(name) is ok


def test_installable_compares_by_family_not_full_name():
    store = wa.StoreView(
        provisioned={"microsoft.windowscalculator_8wekyb3d8bbwe":
                     "Microsoft.WindowsCalculator_2021.2607.0.0_neutral_~_8wekyb3d8bbwe",
                     "microsoft.bingweather_8wekyb3d8bbwe":
                     "Microsoft.BingWeather_4.54.63045.0_neutral_~_8wekyb3d8bbwe"},
        staged=set(), per_user={}, provisioned_at={}, readable=True)
    rows = wa.installable_records(store, mine={"microsoft.windowscalculator_8wekyb3d8bbwe"})
    assert [r.package_name for r in rows] == ["Microsoft.BingWeather"]
    assert rows[0].status == m.INSTALLABLE and rows[0].can_install and not rows[0].can_uninstall
    assert rows[0].name == "Bing Weather"            # the catalog's name, not a guess


def test_publisher_name_keeps_quoted_commas_and_skips_guids():
    assert wa._publisher_name('CN="GIGA-BYTE TECHNOLOGY CO., LTD.", O="GIGA-BYTE TECHNOLOGY CO., LTD."',
                              "x") == "GIGA-BYTE TECHNOLOGY CO., LTD."
    assert wa._publisher_name("CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B", "8wekyb3d8bbwe") == \
        "Microsoft Corporation"


MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
  <Properties><DisplayName>Thing</DisplayName><PublisherDisplayName>Acme</PublisherDisplayName>
    <Description>Does things</Description><Logo>Assets\\StoreLogo.png</Logo></Properties>
  <Applications>{apps}</Applications>
</Package>"""


@pytest.mark.parametrize("apps,hidden", [
    ('<Application Id="A"><uap:VisualElements AppListEntry="none"/></Application>', True),
    ('<Application Id="A"><uap:VisualElements DisplayName="x"/></Application>', False),
    ('', True),
])
def test_manifest_hidden_means_no_start_entry(tmp_path, apps, hidden):
    (tmp_path / "AppxManifest.xml").write_text(MANIFEST.format(apps=apps), encoding="utf-8")
    info = wa.read_manifest(str(tmp_path))
    assert info["hidden"] is hidden
    assert (info["display_name"], info["publisher"], info["description"]) == ("Thing", "Acme", "Does things")


def test_unreadable_manifest_is_not_hidden(tmp_path):
    assert wa.read_manifest(str(tmp_path)) == {}


def test_logo_prefers_scale_100(tmp_path):
    assets = tmp_path / "Assets"
    assets.mkdir()
    for scale in (200, 100, 400):
        (assets / f"StoreLogo.scale-{scale}.png").write_bytes(b"x")
    assert wa.logo_file(str(tmp_path), "Assets\\StoreLogo.png").endswith("StoreLogo.scale-100.png")


# ---- desktop: orphaned / defect ----------------------------------------------------------

def entry(tmp_path, **kw) -> SoftwareEntry:
    base = dict(name="X", version="1", publisher="P", install_date="", size_mb="", type_="64-bit",
                source="registry")
    base.update(kw)
    return SoftwareEntry(**base)


def test_burn_bundle_with_missing_cache_is_defect_not_orphaned(tmp_path):
    e = entry(tmp_path, uninstall_string=f'"{tmp_path}\\Package Cache\\gone\\vcredist_x64.exe" /uninstall')
    kind, reason = desktop_apps.judge(e, bundle=True)
    assert kind == m.DEFECT
    assert "components it installed are separate" in reason


def test_uninstaller_and_files_gone_is_orphaned(tmp_path):
    e = entry(tmp_path, uninstall_string=f'"{tmp_path}\\gone\\unins000.exe"',
              install_location=str(tmp_path / "gone"))
    assert desktop_apps.judge(e)[0] == m.ORPHANED


def test_uninstaller_gone_but_files_present_is_defect(tmp_path):
    (tmp_path / "app").mkdir()
    e = entry(tmp_path, uninstall_string=f'"{tmp_path}\\app\\unins000.exe"', install_location=str(tmp_path / "app"))
    assert desktop_apps.judge(e)[0] == m.DEFECT


def test_msi_with_missing_cached_package_is_defect(tmp_path):
    (tmp_path / "app").mkdir()
    e = entry(tmp_path, windows_installer=True, product_code="{9AC08E99-230B-47E8-9721-4577B7F124EA}",
              uninstall_string="MsiExec.exe /X{9AC08E99-230B-47E8-9721-4577B7F124EA}",
              install_location=str(tmp_path / "app"))
    assert desktop_apps.judge(e, local_package=str(tmp_path / "missing.msi"))[0] == m.DEFECT


def test_healthy_entry_is_a_desktop_app(tmp_path):
    exe = tmp_path / "unins000.exe"
    exe.write_bytes(b"MZ")
    e = entry(tmp_path, uninstall_string=f'"{exe}"')
    assert desktop_apps.judge(e) == (m.DESKTOP, "")


def test_guid_packing_matches_the_real_registration():
    code = "{9AC08E99-230B-47E8-9721-4577B7F124EA}"
    assert desktop_apps.squish_guid(code) == "99E80CA9B0328E74791254777B1F42AE"
    assert desktop_apps.unsquish_guid("99E80CA9B0328E74791254777B1F42AE") == code


# ---- orphaned app data -------------------------------------------------------------------

def test_orphaned_data_skips_non_packages_installed_and_provisioned(tmp_path):
    names = ["cr.sb.cdm0B416A62BCCA696DA753309CE9690CFEEEB87B21", "Microsoft.BingWeather_8wekyb3d8bbwe",
             "Microsoft.ZuneMusic_8wekyb3d8bbwe", "Acme.Gone_abcdefghjkmnp", "Spotify.X_8wekyb3d8bbwe"]
    for n in names:
        (tmp_path / n).mkdir()
    store = wa.StoreView({"microsoft.zunemusic_8wekyb3d8bbwe": "z"}, set(), {}, {}, True)
    found = orphans.find_orphaned_data({"spotify.x_8wekyb3d8bbwe"}, store, packages_dir=str(tmp_path))
    by_family = {r.family: r for r in found}
    assert set(by_family) == {"Microsoft.BingWeather_8wekyb3d8bbwe", "Acme.Gone_abcdefghjkmnp"}
    assert by_family["Microsoft.BingWeather_8wekyb3d8bbwe"].confidence == "High"
    assert by_family["Acme.Gone_abcdefghjkmnp"].name == "Unknown orphaned application entry"
    assert by_family["Acme.Gone_abcdefghjkmnp"].confidence == "Unknown"


# ---- recommendations ---------------------------------------------------------------------

def test_recommendation_matches_name_and_publisher_id():
    assert recommend.recommend(rec(package_name="Microsoft.BingWeather", publisher_id="8wekyb3d8bbwe"))[0] == m.REMOVE
    # The same name under another publisher is not the curated package.
    assert recommend.recommend(rec(package_name="Microsoft.BingWeather", publisher_id="zzzzzzzzzzzzz"))[0] == m.KEEP
    assert recommend.recommend(rec(package_name="king.com.CandyCrushSaga", publisher_id="kgqvnymyfvs32"))[0] == m.REMOVE
    assert recommend.recommend(rec(package_name="Microsoft.WindowsStore", publisher_id="8wekyb3d8bbwe"))[0] == m.KEEP
    assert recommend.recommend(rec(package_name="Some.Unknown", publisher_id="abc"))[0] == m.KEEP


def test_system_and_framework_are_never_recommended_for_removal():
    for kind in (m.SYSTEM, m.FRAMEWORK):
        assert recommend.recommend(rec(type=kind, package_name="Microsoft.BingWeather",
                                       publisher_id="8wekyb3d8bbwe"))[0] == m.KEEP


# ---- views -------------------------------------------------------------------------------

def sample_rows():
    now = datetime.now()
    return [
        rec(key="a", name="Bing Weather", recommendation=m.REMOVE, installed=now - timedelta(days=2),
            files_bytes=10, publisher_id="8wekyb3d8bbwe", publisher="Microsoft Corporation"),
        rec(key="b", name="Zed Editor", type=m.DESKTOP, installed=now - timedelta(days=400), files_bytes=500,
            publisher="Zed", update="2.0", winget_id="Zed.Zed", description="a code editor"),
        rec(key="c", name="Settings", type=m.SYSTEM, status=m.UNREMOVABLE, removable=False),
        rec(key="d", name="Clipchamp", status=m.INSTALLABLE, removable=False, family="Clipchamp_x"),
        rec(key="e", name="Old leftover", type=m.ORPHANED, leftover_paths=("HKLM\\x",)),
    ]


def test_empty_views_are_hidden_but_all_never_is():
    counts = views.view_counts(sample_rows(), set(), views.ViewOptions())
    shown = views.visible_views(counts)
    assert shown[0] == "all" and "framework" not in shown and "defect" not in shown
    assert counts["remove"] == 1 and counts["updates"] == 1 and counts["orphaned"] == 1


def test_installable_apps_are_hidden_until_asked_for():
    opts = views.ViewOptions(show_installable=False)
    assert "d" not in [r.key for r in views.arrange(sample_rows(), "all", "", opts, set(), "name", False)]
    assert views.hidden_by_options(sample_rows(), opts) == 1
    opts.show_installable = True
    assert "d" in [r.key for r in views.arrange(sample_rows(), "all", "", opts, set(), "name", False)]


def test_hiding_microsoft_apps():
    opts = views.ViewOptions(show_microsoft=False)
    assert "a" not in [r.key for r in views.arrange(sample_rows(), "all", "", opts, set(), "name", False)]


def test_search_matches_description_and_moves_to_a_view_with_results():
    rows = sample_rows()
    counts = views.view_counts(rows, set(), views.ViewOptions(), "code editor")
    assert counts["all"] == 1
    assert views.pick_view("orphaned", counts) == "all"
    assert views.pick_view("updates", counts) == "updates"


def test_sorting_storage_descending_puts_unmeasured_last():
    rows = sample_rows()
    ordered = views.arrange(rows, "all", "", views.ViewOptions(True, True), set(), "storage", True)
    assert [r.key for r in ordered][:2] == ["b", "a"]


def test_select_never_picks_unremovable_or_installable_for_removal():
    rows = sample_rows()
    assert views.select(rows, "remove") == ["a"]
    assert "c" not in views.select(rows, "all")            # system: nothing to do with it
    assert "d" in views.select(rows, "all")                # installable: Install applies
    assert set(views.select(rows, "desktop")) == {"b"}


def test_properties_of_an_orphan_name_what_cleanup_removes():
    r = rec(type=m.ORPHANED, family="Acme.Gone_abcdefghjkmnp", confidence="Unknown",
            leftover_paths=("C:\\Users\\u\\AppData\\Local\\Packages\\Acme.Gone_abcdefghjkmnp",))
    text = views.properties_text(r)
    assert "Confidence: Unknown" in text and "Removes: C:\\Users" in text


# ---- extras ------------------------------------------------------------------------------

WINGET = """   - \r   \\ \r
Name                         Id                          Version      Available    Source
-------------------------------------------------------------------------------------------
7-Zip 26.02 (x64 edition)    7zip.7zip                   26.02        26.03        winget
Windows Terminal             Microsoft.WindowsTerminal   1.24.1       1.25.2733.0  winget
Thing                        ARP\\Machine\\X64\\Thing        Unknown      1.0          winget
3 upgrades available.
"""


def test_winget_table_is_parsed_by_column():
    rows = extras.parse_upgrade_table(WINGET)
    assert [(r.name, r.id, r.available) for r in rows][:2] == [
        ("7-Zip 26.02 (x64 edition)", "7zip.7zip", "26.03"),
        ("Windows Terminal", "Microsoft.WindowsTerminal", "1.25.2733.0")]


def test_winget_garbage_is_none_and_nothing_to_upgrade_is_empty():
    assert extras.parse_upgrade_table("winget is not recognized") is None
    assert extras.parse_upgrade_table("No installed package found matching input criteria.") == []


def test_updates_attach_by_name():
    rows = [rec(key="t", name="Windows Terminal"), rec(key="z", name="Other", type=m.DESKTOP)]
    assert extras.attach_updates(rows, extras.parse_upgrade_table(WINGET)) == 1
    assert rows[0].update == "1.25.2733.0" and rows[0].winget_id == "Microsoft.WindowsTerminal"


def test_first_run_reports_nothing_as_new(tmp_path):
    store = extras.SeenStore(str(tmp_path / "seen.json"))
    assert store.load() is None
    assert extras.newly_discovered(sample_rows(), store.load()) == set()
    store.save(["a", "b"])
    assert extras.newly_discovered(sample_rows(), store.load()) == {"c", "e"}   # d is installable


def test_storage_reads_pending_then_unknown_never_zero():
    r = rec(type=m.DESKTOP)
    assert views.field_text(r, "storage") == "…"
    extras.measure(r)                      # no size recorded, no folder: nothing to measure
    assert r.storage is None and views.field_text(r, "storage") == "—"


def test_folder_size(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "f").write_bytes(b"x" * 1000)
    assert extras.folder_size(str(tmp_path)) == (1000, True)
    assert extras.folder_size(str(tmp_path / "missing")) == (None, True)


# ---- guards ------------------------------------------------------------------------------

@pytest.mark.parametrize("key,ok", [
    (r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{042d26ef-3dbe-4c25-95d3-4c1b11b235a7}",
     True),
    (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Thing", True),
    (r"HKLM\SOFTWARE\Classes\Installer\Products\99E80CA9B0328E74791254777B1F42AE", True),
    (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData\S-1-5-18\Products"
     r"\99E80CA9B0328E74791254777B1F42AE", True),
    (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", False),        # the whole list
    (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run", False),
    (r"HKLM\SOFTWARE\Classes\Installer\Products", False),
])
def test_only_uninstall_and_installer_keys_may_be_deleted(key, ok):
    assert act.key_allowed(key) is ok


def test_delete_key_refuses_before_touching_the_registry():
    ok, why = act.delete_key(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run")
    assert not ok and why.startswith("refused")


@pytest.mark.parametrize("path", [
    "C:\\", "C:\\Windows\\SystemApps\\x", os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("LOCALAPPDATA", r"C:\Users\u\AppData\Local"), r"C:\Users\someone", r"C:\Games",
])
def test_folder_guard_refuses_roots_system_and_profiles(path):
    assert act.folder_allowed(path)[0] is False


def test_folder_guard_allows_a_package_data_folder_and_program_subfolder():
    assert act.folder_allowed(r"C:\Users\u\AppData\Local\Packages\Acme.Gone_abcdefghjkmnp")[0]
    assert act.folder_allowed(r"C:\Program Files\Vendor\App")[0]


def test_delete_folder_refuses_a_root_without_deleting(tmp_path):
    gone, stuck = act.delete_folder("C:\\")
    assert not gone and stuck[0].startswith("refused")


# ---- verified actions --------------------------------------------------------------------

class FakeRunner(act.Runner):
    def __init__(self, before, after, output="", all_users=None):
        self.lists = [before, after]
        self.output = output
        self.scripts = []
        self._all = all_users

    def installed_families(self):
        return self.lists.pop(0) if self.lists else None

    def all_user_families(self):
        return self._all

    def powershell(self, script, timeout=180):
        self.scripts.append(script)
        return 0, self.output, ""

    def run(self, args, timeout=180):
        self.scripts.append(args)
        return 0, self.output if args[1] == "upgrade" else self.list_output


def wapp(**kw):
    base = dict(key="appx:x", name="X", full_name="X_1.0.0.0_x64__8wekyb3d8bbwe",
                family="X_8wekyb3d8bbwe", package_name="X")
    base.update(kw)
    return rec(**base)


def test_removal_is_confirmed_by_reading_back():
    runner = FakeRunner({"x_8wekyb3d8bbwe"}, set())
    out = act.remove_windows_app(wapp(), act.SCOPE_USER, runner, lambda _l: None)
    assert out.state == act.REMOVED
    assert "-AllUsers" not in runner.scripts[0] and "X_1.0.0.0_x64__8wekyb3d8bbwe" in runner.scripts[0]


def test_still_listed_after_removal_is_skipped_not_removed():
    out = act.remove_windows_app(wapp(), act.SCOPE_USER, FakeRunner({"x_8wekyb3d8bbwe"}, {"x_8wekyb3d8bbwe"}),
                                 lambda _l: None)
    assert out.state == act.SKIPPED and not out.ok


def test_unreadable_list_after_removal_is_not_a_success():
    out = act.remove_windows_app(wapp(), act.SCOPE_USER, FakeRunner({"x_8wekyb3d8bbwe"}, None), lambda _l: None)
    assert out.state == act.SKIPPED and "unconfirmed" in out.reason


def test_in_use_is_locked_with_the_reason():
    runner = FakeRunner({"x_8wekyb3d8bbwe"}, {"x_8wekyb3d8bbwe"},
                        output="Deployment failed with HRESULT: 0x80073D02, The package could not be installed")
    out = act.remove_windows_app(wapp(), act.SCOPE_USER, runner, lambda _l: None)
    assert out.state == act.LOCKED and out.reason == act.REASON_IN_USE


def test_all_users_scope_also_removes_the_provisioned_copy():
    runner = FakeRunner({"x_8wekyb3d8bbwe"}, set(), all_users={"other_8wekyb3d8bbwe"})
    out = act.remove_windows_app(wapp(), act.SCOPE_ALL, runner, lambda _l: None)
    assert out.state == act.REMOVED
    assert "-AllUsers" in runner.scripts[0] and "Remove-AppxProvisionedPackage" in runner.scripts[0]


def test_still_installed_for_another_account_is_reported():
    runner = FakeRunner({"x_8wekyb3d8bbwe"}, set(), all_users={"x_8wekyb3d8bbwe"})
    assert act.remove_windows_app(wapp(), act.SCOPE_ALL, runner, lambda _l: None).state == act.SKIPPED


def test_protected_app_is_never_attempted():
    runner = FakeRunner(set(), set())
    out = act.remove_windows_app(wapp(removable=False), act.SCOPE_USER, runner, lambda _l: None)
    assert out.state == act.SKIPPED and out.reason.startswith(act.REASON_PROTECTED) and runner.scripts == []


def test_install_verified_by_family():
    runner = FakeRunner(None, None)
    runner.lists = [{"x_8wekyb3d8bbwe"}]
    out = act.install_windows_app(wapp(status=m.INSTALLABLE), runner, lambda _l: None)
    assert out.state == act.INSTALLED and "-RegisterByFamilyName" in runner.scripts[0]


def test_report_wording():
    a, b = wapp(), wapp(name="Y")
    assert act.summary([act.Outcome(a, act.REMOVED)]) == "App removed"
    assert act.summary([act.Outcome(a, act.REMOVED), act.Outcome(b, act.REMOVED)]) == "All 2 apps removed"
    mixed = [act.Outcome(a, act.REMOVED), act.Outcome(b, act.SKIPPED, act.REASON_IN_USE, [(42, "x.exe")])]
    assert act.summary(mixed) == "1 removed · 1 skipped"
    text = act.report_text(mixed, act.SCOPE_PC)
    assert "Entire PC" in text and "held by x.exe (PID 42)" in text and act.REASON_IN_USE in text


# ---- the pane ----------------------------------------------------------------------------

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_pane_shows_rows_chips_and_the_action_bar(qapp):
    from modules.app_buster import app_buster_module as abm
    from modules.app_buster.engine.scan import ScanResult

    mod = abm.AppBusterModule()
    w = mod.create_widget()
    result = ScanResult()
    result.rows = sample_rows()
    w._busy = True
    w._fill_extras = lambda: None          # no live storage walk / winget in a unit test
    w._scanned((result, None, (1,)))
    assert not w._busy and len(w._model.rows) == 4          # installable hidden by default
    assert not w._chips["framework"][0].isVisibleTo(w)
    assert w._chips["orphaned"][0].isVisibleTo(w)
    w._select("remove")
    assert w._model.checked == {"a"} and w._action_bar.isVisibleTo(w)
    w._set_layout("details")
    assert w._stack.currentIndex() == 1
    w._toggle_field("type", True)
    assert "type" in w._model.fields
    w._search.setText("no such app anywhere")
    assert w._stack.currentIndex() == 2
    w._reset_selection()
    assert not w._action_bar.isVisibleTo(w)
    mod.on_stop()


def test_dialogs_build(qapp):
    from modules.app_buster import dialogs
    rows = sample_rows()
    d = dialogs.RemovalDialog(rows, elevated=False)
    assert d.scope == act.SCOPE_USER
    dialogs.PropertiesDialog(rows[4])
    dialogs.ResultDialog([act.Outcome(rows[0], act.REMOVED), act.Outcome(rows[1], act.DEFERRED)], act.SCOPE_USER)
    dialogs.LockedDialog(act.Outcome(rows[0], act.LOCKED, act.REASON_IN_USE, [(1, "a.exe")]))


@pytest.mark.skipif(sys.platform != "win32", reason="reads this machine")
def test_live_scan_reads_every_source():
    from modules.app_buster.engine import scan
    result = scan.scan()
    if result.problems == ["Windows apps could not be read: Get-AppxPackage returned nothing"]:
        pytest.skip("Get-AppxPackage is unavailable in this test session")
    assert result.problems == []
    kinds = {r.type for r in result.rows}
    assert {m.WINDOWS, m.DESKTOP, m.FRAMEWORK} <= kinds
    assert all(r.key for r in result.rows) and len({r.key for r in result.rows}) == len(result.rows)


def test_global_search_finds_apps_once_the_list_exists(qapp):
    from modules.app_buster import app_buster_module as abm
    from modules.app_buster.engine.scan import ScanResult
    from core.search_provider import SearchQuery

    mod = abm.AppBusterModule()
    provider = mod.get_search_provider()
    assert provider.search(SearchQuery(text="weather")) == []          # never opened: no scan in a keystroke
    w = mod.create_widget()
    result = ScanResult()
    result.rows = sample_rows()
    w._fill_extras = lambda: None
    w._busy = True
    w._scanned((result, None, (1,)))
    hits = provider.search(SearchQuery(text="weather"))
    assert len(hits) == 1 and "recommended for removal" in hits[0].summary
    assert provider.search(SearchQuery(text="code editor"))[0].summary.startswith("Zed Editor")
    mod.on_stop()


@pytest.mark.parametrize("scope", [act.SCOPE_ALL, act.SCOPE_PC])
def test_review_unreadable_all_users_never_deletes_profiles(monkeypatch, scope):
    monkeypatch.setattr(act, "profile_data_folders", lambda f: pytest.fail("profile cleanup attempted"))
    out = act.remove_windows_app(wapp(), scope, FakeRunner(set(), set()), lambda l: None)
    assert out.state == act.SKIPPED and "other accounts is unconfirmed" in out.reason


@pytest.mark.parametrize("scope", [act.SCOPE_ALL, act.SCOPE_PC, act.SCOPE_USER])
def test_review_absent_current_user_still_removes_all_users(monkeypatch, scope):
    runner = FakeRunner(set(), set(), all_users=set())
    monkeypatch.setattr(act, "profile_data_folders", lambda f: [])
    act.remove_windows_app(wapp(), scope, runner, lambda l: None)
    assert bool(runner.scripts) is (scope != act.SCOPE_USER)


@pytest.mark.parametrize("error, expected", [(FileNotFoundError(), False), (PermissionError(), None),
                                             (OSError("refused"), None)])
def test_review_registry_reads_are_tristate(monkeypatch, caplog, error, expected):
    def refused(*args):
        raise error
    monkeypatch.setattr(act.winreg, "OpenKey", refused)
    assert act.key_exists(r"HKLM\fake") is expected
    if expected is None:
        assert caplog.records and caplog.records[-1].levelname == "WARNING"


def test_review_uninstall_unknown_registry_keeps_polling(monkeypatch):
    reads = []
    monkeypatch.setattr(act, "key_exists", lambda k: reads.append(k))
    monkeypatch.setattr(act.time, "sleep", lambda s: None)
    runner = FakeRunner(None, None)
    runner.interactive = lambda *a: 0
    out = act.remove_desktop_app(rec(type=m.DESKTOP, uninstall_string="fake", registry_key="fake"),
                                 runner, lambda l: None, lambda: False)
    assert len(reads) == 20
    assert out.state == act.SKIPPED and "registry back" in out.reason


def test_review_clean_keys_unknown_requires_backup(monkeypatch):
    key = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Fake"
    monkeypatch.setattr(act, "key_exists", lambda k: None)
    monkeypatch.setattr(act, "backup_key", lambda *a: (False, "refused"))
    assert act._clean_keys([key], FakeRunner(None, None), lambda l: None)[0] is False


def test_review_delete_key_unknown_is_unconfirmed(monkeypatch):
    from types import SimpleNamespace
    from contextlib import nullcontext
    key = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Fake"
    monkeypatch.setattr(act.winreg, "OpenKey", lambda *a: nullcontext(SimpleNamespace(handle=1)))
    monkeypatch.setattr(act.ctypes.windll.advapi32, "RegDeleteTreeW", lambda *a: 0)
    monkeypatch.setattr(act, "key_exists", lambda k: None)
    assert act.delete_key(key)[0] is False


@pytest.mark.parametrize("scope", [act.SCOPE_ALL, act.SCOPE_PC, act.SCOPE_USER])
def test_review_deferred_removal_is_current_user_only(monkeypatch, scope):
    from contextlib import nullcontext
    commands = []
    monkeypatch.setattr(act.winreg, "CreateKeyEx", lambda *a: nullcontext(1))
    monkeypatch.setattr(act.winreg, "SetValueEx", lambda *a: commands.append(a[-1]))
    out = act.remove_at_restart(wapp(), scope)
    assert out.state == act.DEFERRED and "-AllUsers" not in commands[0]
    assert ("other accounts" in out.reason) is (scope != act.SCOPE_USER)



def test_review_folder_guard_resolves_extended_paths(tmp_path, monkeypatch):
    windows = tmp_path / "Windows"
    monkeypatch.setattr(act, "system_root", lambda: str(windows))
    monkeypatch.setattr(act.os.path, "realpath", lambda p: "\\\\?\\" + str(windows / "SystemApps"))
    assert act.folder_allowed(str(tmp_path / "alias"))[0] is False


@pytest.mark.parametrize("link_kind", ["isjunction", "islink"])
def test_review_folder_guard_refuses_link_ancestors(tmp_path, monkeypatch, link_kind):
    ancestor = tmp_path / "alias"
    monkeypatch.setattr(act.os.path, link_kind, lambda p: os.path.normcase(p) == os.path.normcase(str(ancestor)))
    assert act.folder_allowed(str(ancestor / "Vendor" / "App"))[0] is False


def test_review_profile_registry_roots_and_parents_are_protected(tmp_path, monkeypatch):
    from contextlib import nullcontext
    profile = tmp_path / "relocated" / "person"
    monkeypatch.setattr(act.winreg, "OpenKey", lambda *a: nullcontext(1))
    monkeypatch.setattr(act.winreg, "QueryInfoKey", lambda k: (1, 0, 0))
    monkeypatch.setattr(act.winreg, "EnumKey", lambda *a: "sid")
    monkeypatch.setenv("REVIEW_PROFILE", str(profile))
    monkeypatch.setattr(act.winreg, "QueryValueEx", lambda *a: ("%REVIEW_PROFILE%", 2))
    assert not act.folder_allowed(str(profile))[0]
    assert not act.folder_allowed(str(profile.parent))[0]
    assert act.folder_allowed(str(profile / "AppData" / "Local" / "Packages" / "Acme.Gone_abcdefghjkmnp"))[0]


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_review_restart_walk_schedules_links_without_contents(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    from contextlib import nullcontext
    folder = tmp_path / "vendor" / "app"
    link = str(folder / "link")
    scans, scheduled = [], []
    entry = SimpleNamespace(path=link, is_junction=lambda: kind == "junction",
                            is_symlink=lambda: kind == "symlink", is_dir=lambda **k: True)
    def scan(path):
        scans.append(str(path))
        return nullcontext(iter([entry] if str(path) == str(folder) else []))
    monkeypatch.setattr(act, "folder_allowed", lambda p: (True, ""))
    monkeypatch.setattr(act.os, "scandir", scan)
    monkeypatch.setattr(act.ctypes.windll.kernel32, "MoveFileExW", lambda p, *a: scheduled.append(p) or 1)
    assert act.delete_at_restart(str(folder)) == (True, "")
    assert scans == [str(folder)] and scheduled == [link, str(folder)]



def test_review_registry_poll_recovers_from_unknown(monkeypatch):
    values = iter([None, True, False])
    monkeypatch.setattr(act, "key_exists", lambda k: next(values))
    monkeypatch.setattr(act.time, "sleep", lambda s: None)
    runner = FakeRunner(None, None)
    runner.interactive = lambda *a: 0
    out = act.remove_desktop_app(rec(type=m.DESKTOP, uninstall_string="fake", registry_key="fake"),
                                 runner, lambda l: None, lambda: False)
    assert out.state == act.REMOVED


def test_review_profile_read_error_is_logged(monkeypatch, caplog):
    def denied(*a):
        raise PermissionError("denied")
    monkeypatch.setattr(act.winreg, "OpenKey", denied)
    assert act._profile_roots() is None
    assert "cannot read profile roots" in caplog.text


@pytest.mark.parametrize("operation", ["OpenKey", "QueryInfoKey", "EnumKey"])
def test_profile_list_read_failure_refuses_allowed_folder(tmp_path, monkeypatch, operation):
    from contextlib import nullcontext
    monkeypatch.setattr(act.winreg, "OpenKey", lambda *a: nullcontext(1))
    monkeypatch.setattr(act.winreg, "QueryInfoKey", lambda *a: (1, 0, 0))
    def denied(*a):
        raise PermissionError("denied")
    monkeypatch.setattr(act.winreg, operation, denied)
    assert act.folder_allowed(str(tmp_path / "vendor" / "app")) == (
        False, "the list of user profiles could not be read, so no folder can be checked against it")


@pytest.mark.parametrize("operation", ["OpenKey", "QueryValueEx"])
def test_bad_profile_entry_keeps_other_roots_and_package_allowance(tmp_path, monkeypatch, caplog, operation):
    from contextlib import nullcontext
    profiles = {"first": str(tmp_path / "relocated" / "first"),
                "last": str(tmp_path / "relocated" / "last")}
    def open_key(parent, name):
        if name == "broken" and operation == "OpenKey":
            raise PermissionError("denied")
        return nullcontext(name)
    def read_value(key, name):
        if key == "broken":
            raise FileNotFoundError("missing ProfileImagePath")
        return profiles[key], 1
    monkeypatch.setattr(act.winreg, "OpenKey", open_key)
    monkeypatch.setattr(act.winreg, "QueryInfoKey", lambda *a: (3, 0, 0))
    monkeypatch.setattr(act.winreg, "EnumKey", lambda key, index: ("first", "broken", "last")[index])
    monkeypatch.setattr(act.winreg, "QueryValueEx", read_value)
    for profile in profiles.values():
        assert not act.folder_allowed(profile)[0]
        assert not act.folder_allowed(os.path.dirname(profile))[0]
        package = os.path.join(profile, "AppData", "Local", "Packages", "Acme.Gone_abcdefghjkmnp")
        assert act.folder_allowed(package)[0]
    assert act.folder_allowed(str(tmp_path / "vendor" / "app"))[0]
    assert any(r.levelname == "WARNING" and "broken" in r.message for r in caplog.records)


def test_review_resolved_unc_prefix_is_stripped(monkeypatch):
    monkeypatch.setattr(act.os.path, "realpath", lambda p: r"\\?\UNC\server\share\folder")
    assert act._resolved_path("alias") == os.path.normcase(r"\\server\share\folder")


UPDATE_LISTS = [
    ("OCBase.OCCT.Personal", "17.1.7.0", "17.1.3.0", "Name Id                   Version  Available Source\n---------------------------------------------------\nOCCT OCBase.OCCT.Personal 17.1.3.0 17.1.7.0  winget"),
    ("Sidekick-Poe.Sidekick", "v2026.8.7", "26.08.04.01", "Name                 Id                    Version     Available Source\n-----------------------------------------------------------------------\nSidekick 26.08.04.01 Sidekick-Poe.Sidekick 26.08.04.01 v2026.8.7 winget"),
]


@pytest.mark.parametrize("winget_id,target,installed,listed", UPDATE_LISTS)
def test_update_available_version_is_not_installed(winget_id, target, installed, listed):
    runner = FakeRunner(None, None)
    runner.list_output = listed
    result = act.update_app(rec(winget_id=winget_id, update=target), runner, lambda line: None)
    assert result.state == act.FAILED
    assert result.reason == f"winget finished (exit 0), but {installed} is still installed ({target} available)."
    assert act.parse_list_row("  -\n  /\n" + listed, winget_id) == (installed, target)
    assert "--include-unknown" in runner.scripts[0]


@pytest.mark.parametrize("version,available,target,state", [
    ("2.0", "", "3.0", act.SKIPPED),        # nothing newer listed is not proof
    ("V2.0", "3.0", "v2.0", act.UPDATED),
    ("26.02", "", "26.02.0", act.UPDATED),
    ("Unknown", "", "2.0", act.SKIPPED),
    ("Unknown", "1.19.3.3219", "1.19.3.3219", act.SKIPPED),   # Battle.net: no version ever recorded
    ("< 1.0", "", "2.0", act.SKIPPED),
    ("1.0", "2.0", "2.0", act.FAILED),
])
def test_update_verifies_installed_columns(version, available, target, state):
    runner = FakeRunner(None, None)
    runner.list_output = f"Name Id       Version Available Source\n--------------------------------------\nApp  Acme.App {version:<8}{available:<10}winget"
    result = act.update_app(rec(winget_id="Acme.App", update=target), runner, lambda line: None)
    assert result.state == state


@pytest.mark.parametrize("output,reason", [
    ("The package installed for user scope cannot be uninstalled when running with administrator privileges.",
     "Installed for your account only, and winget will not update it from an app running as administrator. Start the app without admin to update it."),
    ("No applicable upgrade found.", "winget found no update that applies to this PC."),
    ("", "Could not read the installed version back, so the update is unconfirmed."),
])
def test_update_refusals_and_unconfirmed_list(output, reason):
    runner = FakeRunner(None, None, output=output)
    runner.list_output = "unreadable 2.0"
    result = act.update_app(rec(winget_id="Acme.App", update="2.0"), runner, lambda line: None)
    assert result.state == act.SKIPPED
    assert result.reason == reason


@pytest.mark.parametrize("listed,expected", [
    ("Name Id       Version Source\n----------------------------\nApp  Acme.App 2.0     winget", ("2.0", "")),
    ("Name Id       Version\n---------------------\nApp  Acme.App 2.0", ("2.0", "")),
    ("Name Id       Version Available\n-------------------------------\nApp  Acme.App 1.0     2.0", ("1.0", "2.0")),
    ("Name Id       Version\n---------------------\nApp  Acme.App.Other 2.0", None),
    ("No installed package found", None),
])
def test_parse_list_optional_columns_and_exact_id(listed, expected):
    assert act.parse_list_row(listed, "Acme.App") == expected
