from modules.file_forensics.engine import reputation as rep


def test_no_api_key_returns_none_without_calling_vt(monkeypatch):
    called = []
    monkeypatch.setattr(rep, "compute_sha256", lambda p: called.append(p) or "abc")
    result = rep.check_reputation("C:\\f.exe", api_key="")
    assert result is None
    assert called == []  # never even hashed -- no point


def test_a_hash_failure_returns_none(monkeypatch):
    monkeypatch.setattr(rep, "compute_sha256", lambda p: None)
    result = rep.check_reputation("C:\\f.exe", api_key="realkey")
    assert result is None


def test_a_real_key_and_hash_calls_vt_client(monkeypatch):
    monkeypatch.setattr(rep, "compute_sha256", lambda p: "deadbeef")

    class _FakeResult:
        found = True
        malicious = 0

    class _FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
        def check(self, sha256):
            assert sha256 == "deadbeef"
            return _FakeResult()

    monkeypatch.setattr(rep, "VTClient", _FakeClient)
    result = rep.check_reputation("C:\\f.exe", api_key="realkey")
    assert result.found is True


def test_check_signature_delegates_to_procengine(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rep, "verify_signature", lambda path: sentinel)
    assert rep.check_signature("C:\\f.exe") is sentinel
