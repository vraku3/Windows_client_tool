import os

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


def test_check_own_signature_skips_non_signable_extensions(monkeypatch):
    """A real .txt/.md on this machine comes back `could_not_verify` from
    WinVerifyTrust itself (TRUST_E_SUBJECT_FORM_UNKNOWN) -- see the
    docstring on `_SIGNABLE_EXTENSIONS`. check_own_signature must never
    even ask for a type that can't carry a meaningful verdict, or every
    non-executable found file would show an identical, non-actionable
    refusal."""
    called = []
    monkeypatch.setattr(rep, "verify_signature", lambda path: called.append(path))
    assert rep.check_own_signature("C:\\notes.txt") is None
    assert rep.check_own_signature("C:\\report.docx") is None
    assert rep.check_own_signature("C:\\noext") is None
    assert called == []


def test_check_own_signature_calls_through_for_signable_extensions(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rep, "verify_signature", lambda path: sentinel)
    for ext in (".exe", ".dll", ".sys", ".msi", ".msp", ".cab", ".scr",
                ".cpl", ".ocx", ".com"):
        assert rep.check_own_signature(f"C:\\dropped{ext}") is sentinel
    # case-insensitive, same as os.path.splitext/Windows itself
    assert rep.check_own_signature("C:\\DROPPED.EXE") is sentinel


def test_check_own_signature_is_a_real_verdict_on_this_machine():
    """Real-machine assertion, no mocking: a real signed system DLL comes
    back VALID, and an ordinary text file (wrong extension) is never even
    asked -- both measured directly against this machine's own files."""
    system32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")
    dll = os.path.join(system32, "kernel32.dll")
    assert os.path.isfile(dll), "kernel32.dll must exist on a real Windows machine"
    facts = rep.check_own_signature(dll)
    assert facts is not None
    assert facts.status in ("valid", "could_not_verify")  # never collapsed to unsigned
    assert rep.check_own_signature(__file__) is None  # this .py file: not signable
