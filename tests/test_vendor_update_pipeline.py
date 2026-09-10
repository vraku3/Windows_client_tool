import os

from core.procengine.signatures import SignatureFacts, VALID, NOT_SIGNED
from modules.driver_manager.vendor_updates import pipeline as pl
from modules.driver_manager.vendor_updates.pipeline import InstallResult
from modules.driver_manager.vendor_updates.provider import UpdateInfo


def _update(url="https://us.download.nvidia.com/x.exe", signer="NVIDIA Corporation"):
    return UpdateInfo(vendor="NVIDIA", current_version="1.0", latest_version="2.0",
                      download_url=url, installer_signer=signer)


def test_download_and_verify_refuses_a_url_outside_the_allowed_domain(tmp_path):
    update = _update(url="https://evil.example.com/x.exe")
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "domain" in result.reason.lower()


def test_download_and_verify_succeeds_for_a_validly_signed_file(tmp_path, monkeypatch):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=VALID,
                                                    signer="NVIDIA Corporation"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is not None
    assert result.reason == ""


def test_download_and_verify_refuses_a_wrong_signer(tmp_path, monkeypatch):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=VALID,
                                                    signer="Some Other Company"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "signer" in result.reason.lower()


def test_download_and_verify_refuses_an_unsigned_file(tmp_path, monkeypatch):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=NOT_SIGNED))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "not_signed" in result.reason.lower() or "unsigned" in result.reason.lower()


def test_download_and_verify_reports_a_download_failure_distinctly(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_download_file", lambda url, dest: False)
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "download" in result.reason.lower()


def test_download_and_verify_refuses_cleanly_when_the_cache_directory_cannot_be_created(monkeypatch):
    # No cache_dir passed -- forces the default-cache-dir path, which is
    # made to fail here rather than actually touching a real directory.
    monkeypatch.setattr(pl, "_default_cache_dir", lambda: None)
    update = _update()
    result = pl.download_and_verify(update, allowed_domains=["download.nvidia.com"])
    assert result.path is None
    assert "cache directory" in result.reason.lower()


def test_download_and_verify_refuses_cleanly_on_a_malformed_url(tmp_path):
    # urlparse(...).hostname raises ValueError for a malformed IPv6-style
    # URL -- confirmed empirically against this Python's urllib. A
    # malformed download_url is a plausible real-world failure (a bad
    # vendor API response), and this pipeline's whole point is that a
    # refusal is a clean DownloadResult, never an uncaught exception.
    update = _update(url="https://[::1/bad")
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "url" in result.reason.lower()


def _driver_for_install():
    from modules.driver_manager.driver_reader import DriverInfo
    return DriverInfo(device_name="Test GPU", driver_class="Display",
                      version="1.0", date="", publisher="V", signed=True,
                      error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")


def test_install_light_refuses_when_no_restore_point_can_be_created(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (False, "policy disabled"))
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is False
    assert result.restore_point_taken is False
    assert "restore point" in result.reason.lower()


def test_install_light_refuses_when_extraction_finds_no_inf(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(pl, "_extract_with_7zip", lambda installer, dest: True)
    # extraction "succeeds" but leaves no usable inf/sys pair
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is False
    assert result.restore_point_taken is True
    assert "not available" in result.reason.lower() or "no usable" in result.reason.lower()


def test_install_light_runs_pnputil_when_an_inf_is_found(tmp_path, monkeypatch):
    extract_dir_holder = {}

    def fake_extract(installer, dest):
        extract_dir_holder["dir"] = dest
        os.makedirs(os.path.join(dest, "display.driver"), exist_ok=True)
        with open(os.path.join(dest, "display.driver", "nv_disp.inf"), "w") as f:
            f.write("; fake inf")
        with open(os.path.join(dest, "display.driver", "nv_disp.sys"), "wb") as f:
            f.write(b"fake sys")
        return True

    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(pl, "_extract_with_7zip", fake_extract)
    ran = {}

    def fake_run_pnputil(inf_path):
        ran["inf_path"] = inf_path
        return True, ""

    monkeypatch.setattr(pl, "_run_pnputil_install", fake_run_pnputil)
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert isinstance(result, InstallResult)
    assert result.ok is True
    assert result.restore_point_taken is True
    assert ran["inf_path"].endswith("nv_disp.inf")


def test_install_light_reports_pnputil_failure_distinctly(tmp_path, monkeypatch):
    def fake_extract(installer, dest):
        os.makedirs(os.path.join(dest, "d"), exist_ok=True)
        open(os.path.join(dest, "d", "x.inf"), "w").close()
        open(os.path.join(dest, "d", "x.sys"), "wb").close()
        return True

    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(pl, "_extract_with_7zip", fake_extract)
    monkeypatch.setattr(pl, "_run_pnputil_install", lambda inf: (False, "pnputil exited 3"))
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is False
    assert "pnputil" in result.reason.lower()


def test_install_light_refuses_cleanly_when_the_temp_dir_cannot_be_created(monkeypatch):
    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))

    class _FailingTempDir:
        def __enter__(self):
            raise OSError("disk full")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(pl.tempfile, "TemporaryDirectory", lambda prefix=None: _FailingTempDir())
    result = pl.install_light("installer.exe", _driver_for_install())
    assert result.ok is False
    assert result.restore_point_taken is True
    assert "temporary directory" in result.reason.lower()
