"""An app update is UPDATED only when winget's installed version says so.

`winget upgrade` prints "Successfully installed" while nothing changed and
"No applicable upgrade found" for a package it still lists, and the old
verification counted a package as updated when it was missing from a re-scan
-- which a failed re-scan makes true for every package at once.
"""
from core import winget_list as wl
from modules.updates import stage_runners, winget_updater as wu

HEADER = "Name   Id         Version  Available Source\n------------------------------------------\n"


def _listed(version, available=""):
    return HEADER + f"App    Acme.App   {version:<9}{available:<10}winget\n"


def _fake_winget(monkeypatch, upgrade_out, list_out, calls=None):
    def run(args, output_cb=None, is_cancelled=lambda: False, **_kw):
        if calls is not None:
            calls.append(args)
        return upgrade_out if args[0] == "upgrade" else list_out
    monkeypatch.setattr(wu, "_run_winget", run)


def test_success_text_without_a_version_change_is_not_an_update(monkeypatch):
    _fake_winget(monkeypatch, "Successfully installed", _listed("1.0", "2.0"))
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0")
    assert outcome.state == wl.FAILED and not outcome


def test_the_target_version_read_back_is_an_update(monkeypatch):
    calls = []
    _fake_winget(monkeypatch, "Successfully installed", _listed("2.0"), calls)
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="v2.0.0")
    assert outcome.state == wl.UPDATED and outcome and outcome.detail == "Now 2.0"
    assert "--exact" in calls[0] and calls[1][:4] == ["list", "--id", "Acme.App", "--exact"]


def test_no_applicable_upgrade_is_not_success(monkeypatch):
    _fake_winget(monkeypatch, "No applicable upgrade found.", _listed("1.0", "2.0"))
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0")
    assert outcome.state == wl.NOT_APPLICABLE and not outcome


def test_per_user_refusal_says_what_to_do(monkeypatch):
    # The retry is exercised separately; never start a real task in a test.
    monkeypatch.setattr(wu, "_run_user_upgrade", lambda package, *a: (None, "task result unreadable", ""))
    _fake_winget(monkeypatch, "The package installed for user scope cannot be uninstalled "
                              "when running with administrator privileges.", "")
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0")
    assert outcome.state == wl.UNCONFIRMED and "unreadable" in outcome.detail


def test_an_unreadable_list_is_unconfirmed_never_updated(monkeypatch):
    _fake_winget(monkeypatch, "Successfully installed", "winget not found. Please install App Installer")
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0")
    assert outcome.state == wl.UNCONFIRMED and not outcome


def test_unknown_installed_version_is_unconfirmed(monkeypatch):
    _fake_winget(monkeypatch, "Successfully installed", _listed("Unknown", "2.0"))
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0")
    assert outcome.state == wl.UNCONFIRMED


def test_run_all_stage_reports_each_package_from_its_own_read_back(monkeypatch):
    class Config:
        def get(self, key, default=None):
            return default

    class App:
        config = Config()

    before = [wu.AppUpdate("App", "Acme.App", "1.0", "2.0", "winget"),
              wu.AppUpdate("Other", "Acme.Other", "1.0", "3.0", "winget")]
    monkeypatch.setattr(wu, "fetch_updates", lambda patterns=None: before)
    outcomes = {"Acme.App": wu.UpdateOutcome("Acme.App", wl.UPDATED, "Now 2.0"),
                "Acme.Other": wu.UpdateOutcome("Acme.Other", wl.UNCONFIRMED, "Could not read")}
    targets = {}

    def fake_install(winget_id, output_cb, is_cancelled=lambda: False, target_version="",
                     before_version="", verify=True):
        targets[winget_id] = target_version
        return outcomes[winget_id]
    monkeypatch.setattr(wu, "install_update", fake_install)
    data = stage_runners.run_winget_stage(App(), lambda line: None, lambda: False)
    by_id = {r["id"]: r for r in data["results"]}
    assert targets == {"Acme.App": "2.0", "Acme.Other": "3.0"}
    assert by_id["Acme.App"]["confirmed"] is True and by_id["Acme.App"]["after"] == "Now 2.0"
    assert by_id["Acme.Other"]["confirmed"] is False and by_id["Acme.Other"]["state"] == wl.UNCONFIRMED


def test_a_version_that_moved_off_the_old_one_is_an_update_even_below_the_target(monkeypatch):
    # winget installs the newest installer that APPLIES, which can be older than the newest offered.
    _fake_winget(monkeypatch, "Successfully installed", _listed("2.0", "3.0"))
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="3.0", before_version="1.0")
    assert outcome.state == wl.UPDATED and "was 1.0" in outcome.detail


def test_a_restart_request_is_unconfirmed_not_failed(monkeypatch):
    _fake_winget(monkeypatch, "Successfully installed. Restart your PC to finish installation.",
                 _listed("1.0", "2.0"))
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0", before_version="1.0")
    assert outcome.state == wl.UNCONFIRMED and "restart" in outcome.detail


def test_verification_off_skips_the_read_back(monkeypatch):
    calls = []
    _fake_winget(monkeypatch, "Successfully installed", _listed("2.0"), calls)
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="2.0", verify=False)
    assert outcome.state == wl.UNCONFIRMED and len(calls) == 1


def test_a_cancel_never_kills_a_running_install(monkeypatch):
    """Cancel stops the batch before the next package; the install that is
    running finishes and is still read back (winget's tree used to be
    taskkill'ed mid-install)."""
    seen = []
    def run(args, output_cb=None, is_cancelled=lambda: False, **_kw):
        seen.append(is_cancelled())
        return "Successfully installed" if args[0] == "upgrade" else _listed("2.0")
    monkeypatch.setattr(wu, "_run_winget", run)
    outcome = wu.install_update("Acme.App", lambda line: None, is_cancelled=lambda: True,
                                target_version="2.0", before_version="1.0")
    assert seen == [False, False]
    assert outcome.state == wl.UPDATED


def test_a_failed_listing_is_not_up_to_date(monkeypatch):
    import pytest

    def run(args, output_cb=None, is_cancelled=lambda: False, returncode_out=None):
        if returncode_out is not None:
            returncode_out.append(None)
        return "winget not found."
    monkeypatch.setattr(wu, "_run_winget", run)
    with pytest.raises(RuntimeError):
        wu.fetch_updates()


def test_a_version_that_went_down_is_not_an_update(monkeypatch):
    _fake_winget(monkeypatch, "Successfully installed", _listed("2.0", "4.0"))
    outcome = wu.install_update("Acme.App", lambda line: None, target_version="4.0", before_version="3.0")
    assert outcome.state == wl.FAILED and not outcome


# -- interactivity -----------------------------------------------------------

def test_a_package_needing_an_install_location_needs_input(monkeypatch):
    """Battle.net here: winget asks for an install root no headless run can give."""
    _fake_winget(monkeypatch, "This package requires an install location\n"
                              "Specify the install root: "
                              "0x8a150042 : Error reading input in prompt", "")
    outcome = wu.install_update("Blizzard.BattleNet", lambda line: None,
                                target_version="1.19.3")
    assert outcome.state == wl.NEEDS_INPUT and not outcome
    assert "by hand" in outcome.detail


def test_every_winget_call_is_forced_non_interactive(monkeypatch):
    captured = {}

    class FakeProc:
        pid = 1
        stdout = iter([])

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr(wu.subprocess, "Popen", fake_popen)
    wu._run_winget(["upgrade", "--id", "X", "--exact", "--silent"])
    assert "--disable-interactivity" in captured["args"]


def test_the_flag_is_not_added_twice(monkeypatch):
    captured = {}

    class FakeProc:
        pid = 1
        stdout = iter([])

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr(wu.subprocess, "Popen", fake_popen)
    wu._run_winget(["list", "--disable-interactivity"])
    assert captured["args"].count("--disable-interactivity") == 1


def test_a_working_listing_is_parsed(monkeypatch):
    """The success path through the REAL _run_winget: the exit code must
    reach fetch_updates (it once never did, and every scan raised)."""
    import io

    class _Proc:
        returncode = 0
        pid = 1

        def __init__(self, *a, **k):
            self.stdout = io.StringIO(
                "Name   Id          Version Available Source\n"
                "-----------------------------------------------\n"
                "OCCT   OCBase.OCCT 17.1.3  17.1.9    winget\n"
                "1 upgrades available.\n")

        def wait(self):
            return 0
    monkeypatch.setattr(wu.subprocess, "Popen", _Proc)
    updates = wu.fetch_updates()
    assert [u.winget_id for u in updates] == ["OCBase.OCCT"]
