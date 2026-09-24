"""The "Disable all monitor sound" button.

One click hides every live monitor audio output (HDMI / DisplayPort), after
one confirmation. It works on the display outputs as a CLASS, not per monitor:
on this machine all of them are named "High Definition Audio Device (Digital
Audio (HDMI))", so no endpoint can be attributed to a monitor and a button
built on the per-monitor match would never appear.

No real audio: the endpoint list, `set_endpoint_enabled` and the confirmation
box are stubbed.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from modules.monitor_control import display_audio as da
from modules.monitor_control import monitor_module as mm

HIDDEN = 0x10000000


def _endpoint(guid, *, state=da.EndpointState.ACTIVE, raw=1,
              display=True, name="High Definition Audio Device (Digital Audio (HDMI))"):
    return da.AudioEndpoint(endpoint_id=guid, friendly_name=name,
                            device_description=name, state=state,
                            raw_state=raw, is_display_audio=display)


class _InlinePool:
    def start(self, worker):
        worker.run()


def _module(endpoints, monkeypatch, answer=QMessageBox.StandardButton.Yes,
            default_guid=None):
    monkeypatch.setattr(da, "list_render_endpoints", lambda: list(endpoints))
    detail = type("D", (), {"endpoint_id": default_guid or ""})()
    monkeypatch.setattr(da, "default_render_endpoint_detail", lambda: detail)
    module = mm.MonitorControlModule()
    module.app = type("App", (), {"thread_pool": _InlinePool()})()
    module.create_widget()
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: answer))
    monkeypatch.setattr(module, "refresh_data", lambda: None)
    return module


def test_only_live_visible_display_outputs_are_offered(monkeypatch):
    module = _module([
        _endpoint("{a}"),                                        # live HDMI
        _endpoint("{b}", state=da.EndpointState.NOTPRESENT, raw=4),   # ghost
        _endpoint("{c}", raw=1 | HIDDEN),                        # already hidden
        _endpoint("{d}", display=False, name="Arctis Nova Pro"), # a headset
    ], monkeypatch)
    assert [e.endpoint_id for e in module._audible_outputs()] == ["{a}"]


def test_the_button_is_beside_the_refresh_button(monkeypatch):
    module = _module([_endpoint("{a}")], monkeypatch)
    assert module._mute_btn.text() == "Disable all monitor sound"
    assert module._mute_btn.parent() is module._fix_btn.parent()


def test_every_live_output_is_hidden_with_the_full_endpoint_id(monkeypatch):
    module = _module([_endpoint("{a}"), _endpoint("{b}")], monkeypatch)
    calls = []
    monkeypatch.setattr(da, "set_endpoint_enabled",
                        lambda eid, on, **k: calls.append((eid, on, k)))
    module._do_mute_all_audio()
    assert [(e, on) for e, on, _k in calls] == [
        ("{0.0.0.00000000}.{a}", False), ("{0.0.0.00000000}.{b}", False)]
    assert all(k.get("confirm_supervised") is True for _e, _o, k in calls)
    assert "2 outputs" in module._status.text()


def test_saying_no_changes_nothing(monkeypatch):
    module = _module([_endpoint("{a}")], monkeypatch,
                     answer=QMessageBox.StandardButton.No)
    calls = []
    monkeypatch.setattr(da, "set_endpoint_enabled",
                        lambda *a, **k: calls.append(a))
    module._do_mute_all_audio()
    assert calls == []


def test_one_failure_does_not_stop_the_rest_and_is_counted(monkeypatch):
    module = _module([_endpoint("{a}"), _endpoint("{b}")], monkeypatch)
    calls = []

    def flaky(eid, on, **_k):
        calls.append(eid)
        if eid.endswith("{a}"):
            raise OSError("access denied")
    monkeypatch.setattr(da, "set_endpoint_enabled", flaky)
    module._do_mute_all_audio()
    assert len(calls) == 2                                  # the second still ran
    text = module._status.text()
    assert "1 of 2" in text and "access denied" in text


def test_nothing_to_hide_says_so_without_asking(monkeypatch):
    module = _module([_endpoint("{a}", raw=1 | HIDDEN)], monkeypatch)
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(1)))
    module._do_mute_all_audio()
    assert asked == [] and "No monitor audio is on" in module._status.text()


def test_the_default_output_is_called_out_in_the_confirmation(monkeypatch):
    module = _module([_endpoint("{a}")], monkeypatch,
                     default_guid="{0.0.0.00000000}.{a}")
    seen = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda parent, title, text, *a, **k:
                     (seen.append(text), QMessageBox.StandardButton.No)[1]))
    module._do_mute_all_audio()
    assert "DEFAULT" in seen[0]
    assert "headset" in seen[0]          # says what is NOT touched
