import os

from core.procengine.signatures import SignatureFacts, VALID, NOT_SIGNED
from modules.driver_manager.vendor_updates import pipeline as pl
from modules.driver_manager.vendor_updates.pipeline import InstallResult
from modules.driver_manager.vendor_updates.provider import UpdateInfo


def _update(url="https://us.download.nvidia.com/x.exe", signer="NVIDIA Corporation"):
    return UpdateInfo(vendor="NVIDIA", current_version="1.0", latest_version="2.0",
                      download_url=url, installer_signer=signer)


def test_download_file_streams_rather_than_reading_the_whole_response(tmp_path, monkeypatch):
    # Finding I4: must not call resp.read() and materialize the whole body
    # in memory (NVIDIA packages run 600-900MB) -- shutil.copyfileobj streams
    # it instead. A fake response object whose .read() raises proves the
    # code path never calls it.
    class _StreamingResponse:
        def __init__(self, data: bytes):
            self._buf = data

        def read(self, size=-1):
            if size is None or size < 0:
                raise AssertionError("must not read the whole body at once")
            chunk = self._buf[:size]
            self._buf = self._buf[size:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    resp = _StreamingResponse(b"x" * 1000)
    monkeypatch.setattr(pl, "urlopen", lambda url, timeout=None: resp)
    dest = str(tmp_path / "out.exe")
    ok = pl._download_file("https://download.nvidia.com/x.exe", dest)
    assert ok is True
    with open(dest, "rb") as f:
        assert f.read() == b"x" * 1000


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


def test_download_and_verify_deletes_the_file_when_the_signer_is_wrong(tmp_path, monkeypatch):
    # Finding I3: a rejected download (the pipeline itself just concluded
    # it isn't from the claimed vendor) must not be left on disk forever.
    written = {}

    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        written["path"] = dest_path
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=VALID,
                                                    signer="Some Other Company"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert os.path.exists(written["path"]) is False


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


def test_download_and_verify_deletes_the_file_when_unsigned(tmp_path, monkeypatch):
    written = {}

    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        written["path"] = dest_path
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=NOT_SIGNED))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert os.path.exists(written["path"]) is False


def test_download_and_verify_survives_a_delete_failure_on_rejection(tmp_path, monkeypatch, caplog):
    # A failed cleanup must never mask the real refusal reason.
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=NOT_SIGNED))
    monkeypatch.setattr(pl.os, "remove",
                        lambda path: (_ for _ in ()).throw(OSError("locked")))
    update = _update()
    with caplog.at_level("WARNING"):
        result = pl.download_and_verify(
            update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "not_signed" in result.reason.lower() or "unsigned" in result.reason.lower()
    assert any("delete" in r.message.lower() for r in caplog.records)


def test_download_and_verify_leaves_a_verified_file_in_place(tmp_path, monkeypatch):
    # The success path is the CALLER's cleanup responsibility (driver_module),
    # never the pipeline's -- the caller still needs the file to install it.
    written = {}

    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        written["path"] = dest_path
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status=VALID,
                                                    signer="NVIDIA Corporation"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is not None
    assert os.path.exists(written["path"]) is True


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


def test_find_inf_with_sys_prefers_the_stem_matching_inf_over_an_unrelated_one(tmp_path):
    # "aaa_setup_tool.inf" sorts before "zzz_nvidia_disp.inf" in a plain
    # directory listing, so a naive "first .inf found in a directory that
    # has ANY .sys" implementation would hand back the wrong one. Only
    # "zzz_nvidia_disp.inf" shares a stem with the .sys actually present.
    directory = tmp_path / "pkg"
    directory.mkdir()
    (directory / "aaa_setup_tool.inf").write_text("; unrelated tool inf")
    (directory / "zzz_nvidia_disp.inf").write_text("; the real driver inf")
    (directory / "zzz_nvidia_disp.sys").write_bytes(b"fake sys")

    result = pl._find_inf_with_sys(str(directory))

    assert result is not None
    assert os.path.basename(result) == "zzz_nvidia_disp.inf"


def test_find_inf_with_sys_falls_back_to_any_inf_when_no_stem_matches(tmp_path):
    # Some real driver packages split the .inf and .sys into differently
    # named files -- a directory with an .inf and an unrelated .sys is
    # still worth trying, just as a fallback, never a hard refusal.
    directory = tmp_path / "pkg"
    directory.mkdir()
    (directory / "driver.inf").write_text("; the inf")
    (directory / "other_name.sys").write_bytes(b"fake sys")

    result = pl._find_inf_with_sys(str(directory))

    assert result is not None
    assert os.path.basename(result) == "driver.inf"


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
