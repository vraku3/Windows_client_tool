import pytest

from core import audio_switch as asw
from modules.monitor_control import display_audio as da


def _ep(guid, name, state=1):
    return da.AudioEndpoint(endpoint_id="{" + guid + "}", friendly_name=name, device_description="",
                            state=da.decode_state(state), raw_state=state, is_display_audio=False)


EPS = [_ep("bbb", "Speakers"), _ep("aaa", "Headphones"), _ep("ccc", "Monitor audio"),
       _ep("ddd", "Old card", state=4), _ep("eee", "Disabled card", state=2)]


def test_only_active_devices_are_numbered_and_alphabetical_by_default():
    outputs = asw.build_outputs(EPS, None)
    assert [o.label for o in outputs] == ["Headphones", "Monitor audio", "Speakers"]
    assert [o.number for o in outputs] == [1, 2, 3]          # numbers close up over missing devices


def test_the_saved_order_wins_and_unplaced_devices_follow():
    outputs = asw.build_outputs(EPS, None, saved_order=["{ccc}", "{bbb}"])
    assert [o.label for o in outputs] == ["Monitor audio", "Speakers", "Headphones"]
    # a saved GUID that is no longer present is skipped, not a hole
    assert [o.label for o in asw.build_outputs(EPS, None, ["{zzz}", "{aaa}"])][0] == "Headphones"


def test_the_current_default_is_marked_in_either_id_form():
    full = "{0.0.0.00000000}.{bbb}"
    assert [o.is_default for o in asw.build_outputs(EPS, full)] == [False, False, True]


def test_at_most_ten_and_ctrl_zero_is_the_tenth():
    many = [_ep(f"{i:03d}", f"Device {i:02d}") for i in range(14)]
    outputs = asw.build_outputs(many, None)
    assert len(outputs) == 10 and outputs[9].key_name == "Ctrl+0" and outputs[0].key_name == "Ctrl+1"
    assert asw.number_for_key(0) == 10 and asw.number_for_key(7) == 7


def test_hidden_endpoints_are_not_offered():
    hidden = _ep("hhh", "Hidden", state=1 | da.DEVICE_STATE_HIDDEN)
    assert "Hidden" not in [o.label for o in asw.build_outputs(EPS + [hidden], None)]


def test_switching_needs_the_explicit_user_flag():
    with pytest.raises(da.SupervisionRequired):
        da.set_default_endpoint("{aaa}")


def test_full_endpoint_id_accepts_both_forms():
    assert da.full_endpoint_id("{abc}") == "{0.0.0.00000000}.{abc}"
    assert da.full_endpoint_id("{0.0.0.00000000}.{abc}") == "{0.0.0.00000000}.{abc}"


def test_switch_reports_unknown_numbers_and_failures(monkeypatch):
    monkeypatch.setattr(asw, "list_outputs", lambda order=None: asw.build_outputs(EPS, "{aaa}"))
    assert "no sound output number 7" in asw.switch_to_number(7).message
    assert asw.switch_to_number(1).ok and "already" in asw.switch_to_number(1).message

    def boom(*a, **k):
        raise da.AudioPolicyError("E_ACCESSDENIED")
    monkeypatch.setattr(da, "set_default_endpoint", boom)
    result = asw.switch_to_number(2)
    assert not result.ok and "E_ACCESSDENIED" in result.message


def test_a_switch_windows_accepts_but_did_not_take_is_not_reported_as_done(monkeypatch):
    monkeypatch.setattr(asw, "list_outputs", lambda order=None: asw.build_outputs(EPS, "{aaa}"))
    monkeypatch.setattr(da, "set_default_endpoint", lambda *a, **k: True)
    monkeypatch.setattr(da, "default_render_endpoint_detail",
                        lambda: da.DefaultEndpointResult("{0.0.0.00000000}.{aaa}", True, "x"))
    result = asw.switch_to_number(2)
    assert not result.ok and "read back" in result.message


def test_real_machine_outputs_are_listable():
    outputs = asw.list_outputs()
    assert outputs is not None
    assert all(1 <= o.number <= 10 for o in outputs)
    assert sum(o.is_default for o in outputs) <= 1


def test_main_window_binds_ctrl_1_to_ctrl_0_and_reports_the_result(qapp, monkeypatch):
    from PyQt6.QtGui import QShortcut
    from ui.main_window import MainWindow
    from core import audio_switch
    calls = []
    monkeypatch.setattr(audio_switch, "switch_to_number",
                        lambda n, order=None: calls.append(n) or audio_switch.SwitchResult(True, f"ok {n}"))

    class App:
        config = type("C", (), {"get": staticmethod(lambda k, d=None: d)})()
    win = MainWindow.__new__(MainWindow)
    win._app = App()
    shown = []
    win._status_bar = type("S", (), {"showMessage": lambda self, m, t=0: shown.append(m)})()
    for digit in (1, 7, 0):
        MainWindow._switch_audio_output(win, digit)
    assert calls == [1, 7, 10] and shown == ["ok 1", "ok 7", "ok 10"]


def test_ten_shortcuts_are_registered_and_none_collides_with_firewall(qapp):
    import inspect
    from ui import main_window
    src = inspect.getsource(main_window.MainWindow._setup_shortcuts)
    assert 'f"Ctrl+{digit}"' in src and "range(10)" in src
    from modules.firewall_rules import firewall_manager_module as fw
    assert '"Ctrl+0",' not in inspect.getsource(fw.FirewallManagerModule._install_zoom_shortcuts).replace("Ctrl+Alt+0", "")


def test_the_dialog_lists_moves_and_saves_order(qapp, tmp_path):
    from ui.audio_outputs_dialog import AudioOutputsDialog, CONFIG_ORDER
    store = {}
    config = type("C", (), {"get": lambda self, k, d=None: store.get(k, d),
                            "set": lambda self, k, v: store.__setitem__(k, v),
                            "save": lambda self: None})()
    dlg = AudioOutputsDialog(type("A", (), {"config": config})())
    if dlg.list.count() < 2:
        pytest.skip("needs two active outputs")
    first = dlg.list.item(0).data(0x0100)
    dlg.list.setCurrentRow(0)
    dlg._move(1)
    assert store[CONFIG_ORDER][1] == first
    assert dlg.list.item(1).data(0x0100) == first
