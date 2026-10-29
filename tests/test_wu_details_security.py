"""The Windows Update Details pane names published security identifiers."""
from types import SimpleNamespace

from modules.updates.wu_details_dialog import render


def _update(**overrides):
    base = dict(
        title="2026-10 Security Update (KB5123456)", classification="Security Updates",
        severity="Critical", reboot="1", release_date="2026-10-14",
        kb_ids=["5123456"], support_url="", more_info_urls=[], description="",
        security_bulletins=[], cve_ids=[], driver=False,
        driver_class="", driver_manufacturer="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_bulletin_links_to_the_msrc_archive():
    html_text = render(_update(security_bulletins=["MS17-010"]))
    assert "Security bulletins" in html_text
    assert ('https://learn.microsoft.com/en-us/security-updates/securitybulletins/'
            "2017/ms17-010") in html_text
    assert "MS17-010" in html_text


def test_a_cve_links_to_nvd():
    html_text = render(_update(cve_ids=["cve-2026-1234"]))
    assert "https://nvd.nist.gov/vuln/detail/CVE-2026-1234" in html_text
    assert "CVE-2026-1234" in html_text


def test_absent_identifiers_are_stated_not_invented():
    html_text = render(_update())
    assert "did not publish a security bulletin or CVE identifier" in html_text
    assert "nvd.nist.gov" not in html_text


def test_driver_metadata_is_shown_for_a_driver_offer():
    html_text = render(_update(classification="Drivers", driver=True,
                               driver_class="Display",
                               driver_manufacturer="Intel Corporation"))
    assert "Driver class" in html_text and "Display" in html_text
    assert "Driver manufacturer" in html_text and "Intel Corporation" in html_text


def test_bulletin_text_is_escaped_when_it_is_not_a_known_identifier():
    html_text = render(_update(security_bulletins=['<script>"x"']))
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text
