"""The certificate reader's failure path must say why -- and must never
pass a failed read off as an empty store.

`except Exception as e: logger.warning(..., store, e)` once referenced a
name that does not exist in fetch_certs, so every failure raised NameError
from inside the handler meant to report it (found by ruff F821). Later the
handler returned [] -- an unreadable Root store then read as "no
certificates". A failure now RAISES with the reason; the pane runs this on
a Worker and shows it in its error banner.
"""
import subprocess

import pytest

from modules.certificate_viewer import cert_reader


def test_a_failed_enumeration_raises_and_says_why(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise subprocess.SubprocessError("powershell is not on PATH")

    monkeypatch.setattr(cert_reader.subprocess, "run", boom)

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="powershell is not on PATH"):
        cert_reader.fetch_certs("ROOT", "machine")
    assert "ROOT" in caplog.text, "the log must name the store that failed"


def test_malformed_json_is_reported_not_returned_as_empty(monkeypatch, caplog):
    class _Proc:
        stdout = "{not json at all"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(cert_reader.subprocess, "run", lambda *a, **k: _Proc())

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="MY"):
        cert_reader.fetch_certs("MY", "user")
    assert "MY" in caplog.text


def test_a_refused_store_is_an_error_but_an_empty_one_is_not(monkeypatch):
    class _Refused:
        stdout, stderr, returncode = "", "Access is denied", 1

    class _Empty:
        stdout, stderr, returncode = "[]", "", 0

    monkeypatch.setattr(cert_reader.subprocess, "run", lambda *a, **k: _Refused())
    with pytest.raises(RuntimeError, match="Access is denied"):
        cert_reader.fetch_certs("Disallowed", "machine")
    monkeypatch.setattr(cert_reader.subprocess, "run", lambda *a, **k: _Empty())
    assert cert_reader.fetch_certs("Disallowed", "machine") == []
