"""App Buster: every row says what it is, what it does and what removing it
costs; parts of Windows that cannot be removed stay out of the list of things
to remove unless asked for."""
from modules.app_buster.engine import app_info, model as m, views
from modules.app_buster.engine.app_info_data import DESKTOP_RAW, PACKAGE_PREFIXES, PACKAGES


def _rec(**kw):
    base = dict(key=kw.get("package_name", kw.get("name", "k")), name="App", type=m.WINDOWS)
    base.update(kw)
    return m.AppRecord(**base)


def _rows():
    return [_rec(key="a", name="Calc", package_name="Microsoft.WindowsCalculator"),
            _rec(key="s", name="Start", type=m.SYSTEM, status=m.UNREMOVABLE, removable=False,
                 package_name="Microsoft.Windows.StartMenuExperienceHost"),
            _rec(key="f", name="VCLibs", type=m.FRAMEWORK, status=m.UNREMOVABLE, removable=False,
                 package_name="Microsoft.VCLibs.140.00"),
            _rec(key="d", name="Steam", type=m.DESKTOP)]


def test_system_and_framework_are_hidden_from_all_by_default():
    opts = views.ViewOptions()
    shown = views.arrange(_rows(), "all", "", opts, set(), "name", False)
    assert [r.key for r in shown] == ["a", "d"]


def test_their_own_chips_still_list_them():
    opts = views.ViewOptions()
    assert [r.key for r in views.arrange(_rows(), "system", "", opts, set(), "name", False)] == ["s"]
    assert [r.key for r in views.arrange(_rows(), "framework", "", opts, set(), "name", False)] == ["f"]
    counts = views.view_counts(_rows(), set(), opts)
    assert counts["all"] == 2 and counts["system"] == 1 and counts["framework"] == 1


def test_the_view_option_brings_them_back_and_the_notice_says_why_they_were_hidden():
    rows = _rows()
    assert "2 parts of Windows that cannot be removed" in views.hidden_notice(rows, views.ViewOptions())
    opts = views.ViewOptions(show_system=True)
    assert len(views.arrange(rows, "all", "", opts, set(), "name", False)) == 4
    assert views.hidden_notice(rows, opts) == ""


def test_every_row_gets_three_parts_and_curated_ones_win():
    rows = _rows()
    app_info.apply(rows)
    for r in rows:
        assert len(r.about) == 3 and all(part.strip() for part in r.about)
    start = rows[1]
    assert start.about[0] == "The Start menu." and start.extra["about_curated"] == "yes"
    assert "Visual C++" in rows[2].about[0]
    assert "steamapps" in rows[3].about[2]


def test_an_unknown_app_is_described_from_facts_and_says_so():
    r = _rec(name="Mystery Tool", type=m.DESKTOP, publisher="Acme", install_location=r"C:\Acme")
    app_info.apply([r])
    assert r.extra["about_curated"] == "no"
    assert "Acme" in r.about[0] and r"C:\Acme" in r.about[1]
    assert "not in App Buster's catalog" in r.about[1]
    assert "not in App Buster's catalog" in views.about_html(r)


def test_a_guid_named_system_app_gets_a_readable_name():
    r = _rec(name="1527c705-839a-4832-9118-54d4Bd6a0c89", type=m.SYSTEM,
             package_name="1527c705-839a-4832-9118-54d4Bd6a0c89")
    app_info.apply([r])
    assert r.name == "File Picker"
    assert r.extra["original identifier"] == "1527c705-839a-4832-9118-54d4Bd6a0c89"


def test_a_readable_name_is_never_replaced():
    r = _rec(name="GIGABYTE thing", package_name="65d483df-b37e-4fcf-94de-8b795233db63")
    app_info.apply([r])
    assert r.name == "GIGABYTE thing"


def test_system_parts_sharing_a_name_are_told_apart():
    rows = [_rec(key=p, name="Windows Feature Experience Pack", type=m.SYSTEM, package_name=p)
            for p in ("MicrosoftWindows.Client.CBS", "MicrosoftWindows.Client.Core")]
    app_info.apply(rows)
    assert [r.name for r in rows] == ["Windows Feature Experience Pack (Client.CBS)",
                                      "Windows Feature Experience Pack (Client.Core)"]


def test_search_finds_an_app_by_what_it_does():
    rows = _rows()
    app_info.apply(rows)
    found = views.arrange(rows, "all", "pinned apps", views.ViewOptions(show_system=True),
                          set(), "name", False)
    assert [r.key for r in found] == ["s"]


def test_properties_label_the_removal_paragraph_by_kind():
    rows = _rows()
    app_info.apply(rows)
    labels = dict(views.properties(rows[1]))
    assert "Why it cannot be removed" in labels and "If you remove it" not in labels
    assert "If you remove it" in dict(views.properties(rows[0]))


def test_the_catalog_is_well_formed():
    assert all(k == k.lower() for k in PACKAGES)
    assert all(p == p.lower() for p, _a in PACKAGE_PREFIXES)
    for about in list(PACKAGES.values()) + [a for _p, a in PACKAGE_PREFIXES] + \
            [a for _p, a in DESKTOP_RAW]:
        assert about.what.strip() and about.does.strip() and about.remove.strip()


def test_about_html_escapes_what_the_machine_said():
    r = _rec(name="<script>x</script>", type=m.DESKTOP, publisher="A & B")
    app_info.apply([r])
    html = views.about_html(r)
    assert "<script>" not in html and "&lt;script&gt;" in html and "A &amp; B" in html
