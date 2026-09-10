from core.procengine.signatures import SignatureFacts, VALID, NOT_SIGNED
from modules.driver_manager.vendor_updates import pipeline as pl
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
