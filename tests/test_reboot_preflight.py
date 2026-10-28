"""Evidence shown before a maintenance run starts."""
from modules.updates import reboot_preflight as rp
from modules.updates import run_checkpoint as rc


def _reader(reasons, unreadable=False):
    return lambda: (reasons, unreadable)


def test_a_pending_restart_asks_with_its_reasons(tmp_path):
    pre = rp.assess(tmp_path, _reader(["Windows Update", "Pending file rename"]))
    assert pre.should_ask
    text = "\n".join(pre.evidence_lines())
    assert "Windows Update" in text and "Pending file rename" in text


def test_no_evidence_does_not_ask(tmp_path):
    pre = rp.assess(tmp_path, _reader([]))
    assert not pre.should_ask and pre.evidence_lines() == []


def test_an_unreadable_reboot_state_is_reported_but_not_a_blocker(tmp_path):
    pre = rp.assess(tmp_path, _reader(None, unreadable=True))
    assert not pre.should_ask
    assert "Could not read the pending-restart state" in pre.evidence_lines()[0]


def test_an_unfinished_run_is_shown_with_its_last_transitions(tmp_path, monkeypatch):
    rc.begin(tmp_path, ["winget"], "run_all")
    rc.item(tmp_path, "winget:Acme.App", "started", "1.0 -> 2.0")
    monkeypatch.setattr(rp, "unfinished", lambda folder: rc.load(folder))
    pre = rp.assess(tmp_path, _reader([]))
    assert pre.should_ask
    text = "\n".join(pre.evidence_lines())
    assert "previous maintenance run did not finish" in text
    assert "winget:Acme.App: started" in text


def test_the_real_reader_never_raises(tmp_path):
    pre = rp.assess(tmp_path)
    assert pre.reasons is None or isinstance(pre.reasons, list)
