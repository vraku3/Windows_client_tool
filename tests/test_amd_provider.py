from unittest.mock import MagicMock

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import amd_provider as amp


def _driver(name="AMD Radeon RX 7900 XTX", version="32.0.31041.3013"):
    return DriverInfo(device_name=name, driver_class="Display", version=version,
                      date="", publisher="AMD", signed=True, error_code=0,
                      flags="", hardware_id="PCI\\VEN_1002&DEV_744C")


# A trimmed-down shape of the real, live-verified RX 7900 XTX product page:
# two WHQL builds, "Recommended" listed before "Optional" -- but the test
# below that matters most puts them in the OPPOSITE order, to prove the
# match is driven by the "WHQL Recommended" label, not document position.
_PRODUCT_PAGE_HTML = """
<div><p>Adrenalin 26.8.1 (WHQL Recommended)</p>
<a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.8.1-win11-b.exe">Download</a>
<p><a href="/en/resources/support-articles/release-notes/RN-RAD-WIN-26-8-1.html">Release Notes</a></p>
</div>
<div><p>Adrenalin 26.9.1 (WHQL Optional)</p>
<a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.9.1-win11-b.exe">Download</a>
</div>
"""

_REORDERED_HTML = """
<div><p>Adrenalin 26.9.1 (WHQL Optional)</p>
<a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.9.1-win11-b.exe">Download</a>
</div>
<div><p>Adrenalin 26.8.1 (WHQL Recommended)</p>
<a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.8.1-win11-b.exe">Download</a>
</div>
"""


def _fake_urlopen(body: bytes, capture=None):
    def fake(request, timeout=None):
        if capture is not None:
            capture.append(request)
        m = MagicMock()
        m.read.return_value = body
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m
    return fake


def test_product_page_url_is_constructed_from_the_device_name():
    assert amp._product_page_url("7900 XTX") == (
        "https://www.amd.com/en/support/downloads/drivers.html/graphics/"
        "radeon-rx/radeon-rx-7000-series/amd-radeon-rx-7900-xtx.html")
    assert amp._product_page_url("6600") == (
        "https://www.amd.com/en/support/downloads/drivers.html/graphics/"
        "radeon-rx/radeon-rx-6000-series/amd-radeon-rx-6600.html")


def test_check_for_update_returns_update_info_for_a_real_shaped_page(monkeypatch):
    captured = []
    monkeypatch.setattr(amp, "urlopen", _fake_urlopen(_PRODUCT_PAGE_HTML.encode("utf-8"), captured))
    provider = amp.AmdProvider()
    result = provider.check_for_update(_driver())
    assert result is not None
    assert result.vendor == "AMD"
    assert result.latest_version == "26.8.1"
    assert result.download_url == (
        "https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.8.1-win11-b.exe")
    assert result.installer_signer == "Advanced Micro Devices"
    assert result.current_version == "32.0.31041.3013"
    # the fetch went to the constructed product page, with the required
    # amd.com Referer -- without it drivers.amd.com's OWN CDN 302s to a
    # "Download Incomplete" page, but this asserts the fetch of the PAGE
    # itself also carries it (harmless, one code path instead of two).
    assert len(captured) == 1
    assert "radeon-rx-7900-xtx.html" in captured[0].full_url
    assert captured[0].get_header("Referer") == "https://www.amd.com/"


def test_prefers_the_recommended_build_regardless_of_document_order(monkeypatch):
    monkeypatch.setattr(amp, "urlopen", _fake_urlopen(_REORDERED_HTML.encode("utf-8")))
    provider = amp.AmdProvider()
    result = provider.check_for_update(_driver())
    assert result is not None
    assert result.latest_version == "26.8.1"


def test_falls_back_to_first_installer_link_when_no_recommended_label(monkeypatch):
    html = _PRODUCT_PAGE_HTML.replace("WHQL Recommended", "WHQL Beta")
    monkeypatch.setattr(amp, "urlopen", _fake_urlopen(html.encode("utf-8")))
    provider = amp.AmdProvider()
    result = provider.check_for_update(_driver())
    assert result is not None
    assert result.latest_version == "26.8.1"  # first link on the page


def test_returns_none_for_a_device_with_no_rx_model_number(monkeypatch):
    # An APU's integrated GPU -- no RX model number, so no product page
    # URL can be constructed at all. Must not guess.
    called = []
    monkeypatch.setattr(amp, "urlopen", lambda *a, **k: called.append(1))
    provider = amp.AmdProvider()
    result = provider.check_for_update(_driver(name="AMD Radeon(TM) Graphics"))
    assert result is None
    assert called == []  # never even tried to fetch anything


def test_returns_none_and_logs_when_the_page_fetch_fails(monkeypatch, caplog):
    def raising(request, timeout=None):
        raise OSError("network unreachable")
    monkeypatch.setattr(amp, "urlopen", raising)
    provider = amp.AmdProvider()
    with caplog.at_level("WARNING"):
        result = provider.check_for_update(_driver())
    assert result is None
    assert any("amd_provider" in r.name for r in caplog.records)


def test_returns_none_when_no_installer_link_is_on_the_page(monkeypatch):
    monkeypatch.setattr(amp, "urlopen", _fake_urlopen(b"<html>404 not found</html>"))
    provider = amp.AmdProvider()
    result = provider.check_for_update(_driver())
    assert result is None


def test_download_headers_carry_the_required_referer():
    provider = amp.AmdProvider()
    assert provider.download_headers["Referer"] == "https://www.amd.com/"
    assert "User-Agent" in provider.download_headers


def test_provider_registers_itself_on_import():
    from modules.driver_manager.vendor_updates import provider as pv
    assert "AMD" in pv._PROVIDERS
    assert isinstance(pv._PROVIDERS["AMD"], amp.AmdProvider)


# ---------------------------------------------------------------------
# silent FULL install (AMD's own Command Line Installation User Guide)
# ---------------------------------------------------------------------

def test_build_silent_install_args_uses_amds_documented_install_switch():
    provider = amp.AmdProvider()
    args = provider.build_silent_install_args("C:\\temp\\install.log")
    assert "-INSTALL" in args
    assert "-LOG" in args
    assert "C:\\temp\\install.log" in args
    assert "-UI" not in args  # -UI launches the interactive installer -- never here


def test_silent_install_succeeded_reads_result_code_zero_as_success(tmp_path):
    log = tmp_path / "install.log"
    log.write_text("[ResponseResult]\nResultCode = 0\n[Details]\n")
    provider = amp.AmdProvider()
    assert provider.silent_install_succeeded(str(log), exit_code=0) is True


def test_silent_install_succeeded_reads_nonzero_result_code_as_failure(tmp_path):
    log = tmp_path / "install.log"
    log.write_text("[ResponseResult]\nResultCode = 1\n[Details]\n")
    provider = amp.AmdProvider()
    # even if the process exit code looks fine, the LOG's ResultCode is
    # AMD's own documented signal -- exit_code must not override it
    assert provider.silent_install_succeeded(str(log), exit_code=0) is False


def test_silent_install_succeeded_returns_none_when_the_log_never_appeared(tmp_path):
    provider = amp.AmdProvider()
    assert provider.silent_install_succeeded(str(tmp_path / "missing.log"), exit_code=0) is None


def test_silent_install_succeeded_returns_none_when_the_log_has_no_result_code(tmp_path):
    log = tmp_path / "install.log"
    log.write_text("some unrelated content\n")
    provider = amp.AmdProvider()
    assert provider.silent_install_succeeded(str(log), exit_code=0) is None
