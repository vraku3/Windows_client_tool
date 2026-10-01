from datetime import datetime

from modules.file_forensics.engine import analysis


def test_orchestrates_every_engine_piece(monkeypatch):
    class _Meta:
        created = datetime(2026, 1, 1)

    monkeypatch.setattr(analysis, "read_metadata", lambda p: _Meta())
    monkeypatch.setattr(analysis, "find_locking_processes",
                        lambda p: (["proc1"], "1 matches"))

    class _Candidate:
        path = "C:\\creator.exe"

    monkeypatch.setattr(analysis, "find_creator_candidates",
                        lambda created, tolerance_seconds: [_Candidate()])

    class _SigFacts:
        signed = False

    sig_facts = _SigFacts()
    monkeypatch.setattr(analysis, "check_signature", lambda path: sig_facts)
    monkeypatch.setattr(analysis, "check_reputation", lambda path, api_key: None)

    result = analysis.analyze("C:\\found.txt", vt_api_key="")

    assert result.metadata is not None
    assert result.locking_processes == ["proc1"]
    assert result.locking_summary == "1 matches"
    assert len(result.creator_candidates) == 1
    assert result.top_creator_signature is sig_facts
    assert result.top_creator_signature.signed is False
    assert result.reputation is None
    # "found.txt" is not a signable extension -- own_signature is None,
    # the same "not this kind of file" answer check_own_signature gives.
    assert result.own_signature is None


def test_own_signature_is_checked_independently_of_the_creator(monkeypatch):
    """The found file's own signature is a different question from the
    creator's -- it must be checked even when there is no creator
    candidate at all, and must use the FOUND file's path, not the
    creator's."""
    class _Meta:
        created = datetime(2026, 1, 1)

    monkeypatch.setattr(analysis, "read_metadata", lambda p: _Meta())
    monkeypatch.setattr(analysis, "find_locking_processes", lambda p: ([], "ok"))
    monkeypatch.setattr(analysis, "find_creator_candidates",
                        lambda created, tolerance_seconds: [])
    monkeypatch.setattr(analysis, "check_reputation", lambda path, api_key: None)

    own_sig = object()
    seen_paths = []

    def _fake_own(path):
        seen_paths.append(path)
        return own_sig

    monkeypatch.setattr(analysis, "check_own_signature", _fake_own)

    result = analysis.analyze("C:\\dropped.exe")

    assert result.own_signature is own_sig
    assert seen_paths == ["C:\\dropped.exe"]
    assert result.creator_candidates == []


def test_no_creator_candidates_means_no_signature_check(monkeypatch):
    class _Meta:
        created = datetime(2026, 1, 1)

    monkeypatch.setattr(analysis, "read_metadata", lambda p: _Meta())
    monkeypatch.setattr(analysis, "find_locking_processes", lambda p: ([], "ok"))
    monkeypatch.setattr(analysis, "find_creator_candidates",
                        lambda created, tolerance_seconds: [])

    called = []
    monkeypatch.setattr(analysis, "check_signature", lambda path: called.append(path))
    monkeypatch.setattr(analysis, "check_reputation", lambda path, api_key: None)

    result = analysis.analyze("C:\\found.txt")

    assert result.top_creator_signature is None
    assert called == []
