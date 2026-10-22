"""The context menu's signature and VirusTotal actions.

Two new entries on the process menu: Verify signature and Check
VirusTotal. Both need the file's path, and both are tested here by their
handlers rather than by opening a real menu (`exec` blocks).
"""
import os

import pytest

from modules.dashboard.process_menu import ProcessMenu

KERNEL32 = os.path.join(os.environ["SystemRoot"], "System32", "kernel32.dll")


class _FakeInfo:
    pid = 12345
    raw = type("R", (), {"create_time": 116444737000000000})()
    name = "kernel32.dll"
    details = type("D", (), {"path": KERNEL32})()


class _FakeConfig:
    def __init__(self, api_key=""):
        self._api_key = api_key

    def get(self, key, default=None):
        if key == "virustotal.api_key":
            return self._api_key
        return default


class _FakeApp:
    def __init__(self, api_key=""):
        self.config = _FakeConfig(api_key)
        self.thread_pool = None


@pytest.fixture
def menu(qapp):
    from PyQt6.QtWidgets import QWidget

    host = QWidget()
    m = ProcessMenu(host)
    m._widget = host
    return m


# ---- Verify signature ----------------------------------------------------

def test_the_signature_handler_shows_a_verdict(menu, qapp, monkeypatch):
    from core.procengine.signatures import SignatureFacts, VALID

    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    menu._show_signature(SignatureFacts(KERNEL32, VALID, signer="Microsoft"))
    assert any("Microsoft" in text and "Valid" in text for text in shown)


def test_the_signature_handler_runs_the_real_verifier(menu, qapp, monkeypatch):
    """End to end: kernel32.dll is Microsoft-signed, and the menu path
    reaches the engine that says so."""
    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    menu._signature(_FakeInfo())

    assert shown, "no verdict was shown"
    assert any("signed by" in text.lower() for text in shown)


def test_a_process_without_a_path_gets_no_signature(menu, qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    menu._signature(None)
    assert not shown, "a process with no path cannot be verified"


# ---- Check VirusTotal ----------------------------------------------------

def test_virustotal_without_a_key_says_so(menu, qapp, monkeypatch):
    """No API key is a message, not a silent disable -- the user cannot fix
    a check that quietly does nothing."""
    menu._app = _FakeApp(api_key="")
    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    menu._virustotal(_FakeInfo())

    assert any("API key" in text for text in shown)


def test_virustotal_computes_the_hash_and_queries(menu, qapp, monkeypatch):
    """With a key configured, the flow hashes the file and calls the
    client. The network is not touched here -- the client itself is faked
    and its real behaviour is covered by its own tests."""
    menu._app = _FakeApp(api_key="test-key")

    from modules.dashboard.process_menu import ProcessMenu as PM
    from core import virustotal_client as vtc

    hashed = []
    queried = []
    monkeypatch.setattr(vtc, "compute_sha256",
                        lambda path: hashed.append(path) or "a" * 64)
    monkeypatch.setattr(vtc, "VTClient",
                        lambda api_key: _FakeVT(queried))
    monkeypatch.setattr(PM, "_on_vt_result", lambda self, result: None)

    menu._virustotal(_FakeInfo())

    assert hashed == [KERNEL32]
    assert queried == ["a" * 64]


class _FakeVT:
    def __init__(self, queried):
        self._queried = queried

    def check(self, sha256):
        self._queried.append(sha256)
        return None


def test_virustotal_reports_an_unknown_file_honestly(menu, qapp, monkeypatch):
    menu._app = _FakeApp(api_key="test-key")
    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    from core.virustotal_client import VTResult

    menu._on_vt_result(VTResult(found=False, sha256="a" * 64))
    assert any("unknown to VirusTotal" in text for text in shown)


def test_virustotal_reports_a_detection_with_its_score(menu, qapp,
                                                       monkeypatch):
    menu._app = _FakeApp(api_key="test-key")
    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    from core.virustotal_client import VTResult

    menu._on_vt_result(VTResult(found=True, sha256="a" * 64, malicious=3,
                                total=72, score="3/72"))
    assert any("3/72" in text for text in shown)


def test_a_process_without_a_path_gets_no_virustotal(menu, qapp, monkeypatch):
    menu._app = _FakeApp(api_key="test-key")
    shown = []
    monkeypatch.setattr(
        "modules.dashboard.process_menu.QMessageBox.information",
        lambda widget, title, text: shown.append(text))

    menu._virustotal(None)
    assert not shown


# ---- the menu lists them -------------------------------------------------

def test_the_menu_lists_the_new_actions(menu, qapp, monkeypatch):
    from PyQt6.QtWidgets import QMenu

    captured = []
    monkeypatch.setattr(QMenu, "exec",
                        lambda self, *a, **k: captured.append(
                            [a.text() for a in self.actions()]))

    menu._app = _FakeApp()
    menu.show([12345], _FakeInfo(), None)

    assert captured
    labels = " ".join(captured[0])
    assert "Verify signature" in labels
    assert "Check VirusTotal" in labels
