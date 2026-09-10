import json
from unittest.mock import patch, MagicMock

import pytest

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import nvidia_provider as nvp

_PFID_XML = b"""<?xml version="1.0"?>
<LookupValueSearch><LookupValues>
<LookupValue ParentID="3"><Name>GeForce RTX 4090</Name><Value>995</Value></LookupValue>
<LookupValue ParentID="3"><Name>GeForce RTX 3080</Name><Value>877</Value></LookupValue>
</LookupValues></LookupValueSearch>"""

_DRIVER_FOUND_JSON = json.dumps({
    "Success": "10",
    "IDS": [{"downloadInfo": {
        "Success": "1", "Version": "616.92",
        "DownloadURL": "https://us.download.nvidia.com/Windows/616.92/616.92-desktop-win10-win11-64bit-international-dch-whql.exe",
        "DownloadURLFileSize": "990.85 MB", "IsWHQL": "1", "IsBeta": "0",
        "ReleaseDateTime": "Wed Sep 09, 2026",
        "DetailsURL": "https://www.nvidia.com/en-us/drivers/details/278453/",
    }}],
}).encode("utf-8")

_DRIVER_NOT_FOUND_JSON = json.dumps({
    "Success": "0", "MessageCode": "DriverDownloadIDNotFound",
    "MessageValue": "The Driver Download not found for Manual Lookup requested",
}).encode("utf-8")


def _driver(name="GeForce RTX 4090", version="612.10"):
    return DriverInfo(device_name=name, driver_class="Display", version=version,
                      date="", publisher="NVIDIA", signed=True, error_code=0,
                      flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")


def test_check_for_update_returns_update_info_when_a_newer_driver_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))
    responses = [_PFID_XML, _DRIVER_FOUND_JSON]

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = responses.pop(0)
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    result = provider.check_for_update(_driver())
    assert result is not None
    assert result.vendor == "NVIDIA"
    assert result.latest_version == "616.92"
    assert result.download_url == "https://us.download.nvidia.com/Windows/616.92/616.92-desktop-win10-win11-64bit-international-dch-whql.exe"
    assert result.installer_signer == "NVIDIA Corporation"


def test_check_for_update_returns_none_when_nvidia_reports_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))
    responses = [_PFID_XML, _DRIVER_NOT_FOUND_JSON]

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = responses.pop(0)
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    assert provider.check_for_update(_driver()) is None


def test_check_for_update_returns_none_when_gpu_name_has_no_pfid_match(tmp_path, monkeypatch):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = _PFID_XML
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    result = provider.check_for_update(_driver(name="Some Unlisted GPU Model"))
    assert result is None


def test_check_for_update_returns_none_and_logs_on_malformed_json(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))
    responses = [_PFID_XML, b"not json at all"]

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = responses.pop(0)
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    with caplog.at_level("WARNING"):
        result = provider.check_for_update(_driver())
    assert result is None
    assert any("nvidia" in r.message.lower() for r in caplog.records)


def test_pfid_cache_is_reused_when_fresh(tmp_path, monkeypatch):
    cache_path = tmp_path / "pfid.xml"
    cache_path.write_bytes(_PFID_XML)
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(cache_path))
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        m = MagicMock()
        m.read.return_value = _DRIVER_NOT_FOUND_JSON
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    provider.check_for_update(_driver())
    # only the driver-lookup call should have happened -- pfid cache hit,
    # no lookupValueSearch.aspx call
    assert len(calls) == 1
    assert "AjaxDriverService" in calls[0]


def test_provider_registers_itself_on_import():
    from modules.driver_manager.vendor_updates import provider as pv
    assert "NVIDIA" in pv._PROVIDERS
    assert isinstance(pv._PROVIDERS["NVIDIA"], nvp.NvidiaProvider)
