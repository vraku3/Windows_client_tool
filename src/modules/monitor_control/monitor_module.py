r"""Monitor Control — one place for what your displays are doing.

Shows what the displays are doing, and changes them.

**Every change goes through `_apply_guard`** — snapshot, apply, then a
15-second countdown that puts it back unless someone confirms. Nothing here
calls a write function directly, because the failure being designed around
is a mode the monitor cannot show: the screen goes dark and the control
that would undo it is on that screen. Doing nothing has to be the safe
answer, so doing nothing reverts.

Wired: raise every display to its best refresh rate, the four Win+P
arrangements, connect/disconnect per monitor, and the monitor's own DDC/CI
controls — brightness, contrast and input source.

**Brightness and contrast deliberately skip the countdown.** The control
that undoes them is the same slider, on the same screen, still reachable, so
there is nothing to strand. Input source keeps it, and its confirm is placed
on a screen OTHER than the one being switched: Qt still lists that screen —
the GPU is still driving it — while the panel is showing another machine.

Still not wired: the audio endpoint enable/disable writes, which drive an
undocumented `IPolicyConfig` and need their own supervised first run. The
audio a monitor carries is shown read-only.

`requires_admin` with `read_only_unelevated`: display and DDC work needs no
elevation at all, and only the audio endpoint writes do. Gating the whole
module on admin would disable a tab that is mostly usable without it — the
same reasoning as `debloat_module.py` and `store_apps_module.py`.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.monitor_control import _apply_guard as guard
from modules.monitor_control import ddc
from modules.monitor_control import display_config as dc
from modules.monitor_control import display_writes as dw
from modules.monitor_control import view_model as vm
from modules.monitor_control._arrangement_canvas import ArrangementCanvas
from modules.monitor_control._screen_overlay import IdentifyOverlays
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)


class _MonitorCard(QFrame):
    """One monitor: what it is, what it is doing, what it could do.

    The DDC controls appear only for what the monitor ACTUALLY answered.
    `DdcCapability` already draws that line — `supports_brightness` is set
    from a read that worked, not from what the capabilities string claims —
    so a control on this card is a control the panel has replied about.
    """

    #: (target_id, activate)
    toggle_requested = pyqtSignal(int, bool)
    #: (target_id, value) — a VCP value the user settled on.
    brightness_requested = pyqtSignal(int, int)
    contrast_requested = pyqtSignal(int, int)
    input_source_requested = pyqtSignal(int, int)
    #: (target_id, hz) — a rate offered AT the resolution already in use.
    refresh_rate_requested = pyqtSignal(int, float)
    #: (target_id, turn_on) — show or hide this monitor's audio endpoint.
    audio_enabled_requested = pyqtSignal(int, bool)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.setObjectName("monitorCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(3)

        title = QLabel(f"<b>{view.name}</b>")
        state = QLabel("● Active" if view.active else "○ Not in use")
        state.setObjectName("monitorActive" if view.active
                            else "monitorInactive")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(state)
        layout.addLayout(header)

        mode = QLabel(vm.describe(view))
        mode.setObjectName("muted" if not view.active else "")
        layout.addWidget(mode)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        rows = [("Connector", view.connector)]
        if view.native_resolution:
            native = view.native_resolution
            rows.append(("Native", f"{native[0]}x{native[1]}"))
        if view.rates_at_resolution:
            rows.append(("Rates here",
                         ", ".join(f"{r:g}" for r in view.rates_at_resolution)))
        if view.device_name:
            rows.append(("Device", view.device_name))
        audio = _audio_text(view)
        if audio:
            rows.append(("Audio", audio))
        for row, (name, value) in enumerate(rows):
            key = QLabel(name)
            key.setObjectName("muted")
            grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignRight)
            grid.addWidget(QLabel(str(value)), row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        if vm.is_below_best(view):
            best = vm.best_available_rate(view)
            note = QLabel(f"Could run at {best:g} Hz here")
            note.setObjectName("statusWarning")
            layout.addWidget(note)

        self._add_refresh_buttons(layout, view)
        self._add_audio_toggle(layout, view)
        self._add_ddc_controls(layout, view)

        actions = QHBoxLayout()
        actions.addStretch()
        toggle = QPushButton("Disconnect" if view.active else "Connect")
        toggle.setToolTip(
            "Remove this display from the desktop"
            if view.active else "Add this display to the desktop")
        toggle.clicked.connect(
            lambda _checked=False, t=view.target_id, a=not view.active:
                self.toggle_requested.emit(t, a))
        actions.addWidget(toggle)
        layout.addLayout(actions)

    # ── refresh rate, one button per rate ──

    def _add_refresh_buttons(self, layout, view) -> None:
        """A button per rate offered AT the resolution already in use.

        Deliberately not every rate the device can do: the fastest mode on a
        panel is often at a resolution nobody asked for, and offering it here
        would change resolution underneath someone who clicked "240".

        The rate in use is shown checked and disabled — it is a statement of
        where you are, not a button that does nothing.
        """
        if not view.active or not view.rates_at_resolution:
            return
        if len(view.rates_at_resolution) < 2:
            # One rate is not a choice, and a lone dead button reads as a
            # control that is broken rather than as a panel with no options.
            return

        row = QHBoxLayout()
        name = QLabel("Refresh")
        name.setObjectName("muted")
        name.setMinimumWidth(70)
        row.addWidget(name)

        for rate in sorted(view.rates_at_resolution):
            current = abs(rate - view.refresh_hz) < 0.01
            button = QPushButton(f"{rate:g} Hz")
            button.setCheckable(True)
            button.setChecked(current)
            button.setEnabled(not current)
            button.setToolTip(
                f"Already running at {rate:g} Hz" if current else
                f"Switch to {rate:g} Hz at "
                f"{view.resolution[0]}x{view.resolution[1]}. Reverts itself "
                f"in 15 seconds unless you confirm.")
            button.clicked.connect(
                lambda _checked=False, t=view.target_id, r=rate:
                    self.refresh_rate_requested.emit(t, float(r)))
            row.addWidget(button)

        row.addStretch(1)
        layout.addLayout(row)

    # ── the monitor's audio, on or off ──

    def _add_audio_toggle(self, layout, view) -> None:
        """One button: hide or show this monitor's audio endpoint.

        Only when we know which endpoint is this monitor's AND what state it
        is in. `audio_hidden` is None for "no endpoint" and for "could not
        read it", and neither is something to offer a switch for — an on/off
        button whose position is a guess is worse than no button.
        """
        if view.audio_endpoint is None or view.audio_hidden is None:
            return

        hidden = view.audio_hidden
        row = QHBoxLayout()
        name = QLabel("Audio")
        name.setObjectName("muted")
        name.setMinimumWidth(70)
        row.addWidget(name)

        button = QPushButton("Off — click to turn on" if hidden
                             else "On — click to turn off")
        button.setToolTip(
            "This monitor's audio output is hidden from Windows' sound "
            "device list. Click to show it again."
            if hidden else
            "Hide this monitor's audio output from Windows' sound device "
            "list, the same as 'Don't allow' in Sound settings. Takes "
            "effect immediately.")
        button.clicked.connect(
            lambda _checked=False, t=view.target_id, on=hidden:
                self.audio_enabled_requested.emit(t, on))
        row.addWidget(button)

        if view.audio_is_default:
            warning = QLabel("this is your default output")
            warning.setObjectName("statusWarning")
            row.addWidget(warning)

        row.addStretch(1)
        layout.addLayout(row)

    # ── the DDC/CI half of the card ──

    def _add_ddc_controls(self, layout, view) -> None:
        """Brightness, contrast and input — for whatever the panel answered.

        A monitor that is not on the desktop has no `ddc` at all and gets
        nothing here; one that is there and will not talk gets its reason,
        because "no controls" and "the monitor refused" look identical
        otherwise and only one of them is worth investigating.
        """
        capability = view.ddc
        if capability is None:
            return

        if not capability.responded:
            excuse = QLabel(f"Monitor controls unavailable — "
                            f"{capability.reason}")
            excuse.setObjectName("muted")
            excuse.setWordWrap(True)
            layout.addWidget(excuse)
            return

        if capability.supports_brightness:
            layout.addLayout(self._slider_row(
                "Brightness", capability.brightness, view.target_id,
                self.brightness_requested))
        if capability.supports_contrast:
            layout.addLayout(self._slider_row(
                "Contrast", capability.contrast, view.target_id,
                self.contrast_requested))
        if capability.supports_input_source:
            layout.addLayout(self._input_row(capability, view.target_id))

    def _slider_row(self, label: str, value, target_id: int, signal):
        """One VCP slider, over the MONITOR's own range.

        Never 0..100: the range is whatever `maximum` the panel reported.
        A maximum of 0 never reaches here — `ddc._read` refuses that reply
        rather than passing on a slider with no range.

        The signal fires on `sliderReleased`, not `valueChanged`: a write
        measures 0.28s against this hardware, so emitting per drag step
        would queue dozens of them behind one gesture.
        """
        row = QHBoxLayout()
        name = QLabel(label)
        name.setObjectName("muted")
        name.setMinimumWidth(70)
        row.addWidget(name)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(value.maximum)
        slider.setValue(value.current)
        row.addWidget(slider, 1)

        readout = QLabel(f"{value.current} / {value.maximum}")
        readout.setObjectName("muted")
        readout.setMinimumWidth(64)
        readout.setAlignment(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(readout)

        slider.valueChanged.connect(
            lambda v, m=value.maximum: readout.setText(f"{v} / {m}"))
        slider.sliderReleased.connect(
            lambda s=slider, t=target_id: signal.emit(t, s.value()))
        return row

    def _input_row(self, capability, target_id: int):
        """The input picker — only over values the monitor CLAIMS.

        With no capabilities string there is no list, and a guess here
        switches the panel to a machine that cannot switch it back. So the
        current input is shown as text instead.
        """
        row = QHBoxLayout()
        name = QLabel("Input")
        name.setObjectName("muted")
        name.setMinimumWidth(70)
        row.addWidget(name)

        if not capability.input_sources_known:
            current = capability.current_input_name or "unknown"
            text = QLabel(f"{current} — the monitor does not say which "
                          f"inputs it accepts, so it cannot be switched here")
            text.setObjectName("muted")
            text.setWordWrap(True)
            row.addWidget(text, 1)
            return row

        combo = QComboBox()
        for value, choice in capability.input_source_choices():
            combo.addItem(choice, value)
        current = capability.current_input
        if current is not None:
            index = combo.findData(current)
            if index >= 0:
                # Selecting the current value must not look like a request
                # to switch to it. Populating a combo fires `activated` only
                # on user action, but `setCurrentIndex` is wired after the
                # fact anyway so the order here is what keeps it honest.
                combo.setCurrentIndex(index)
        combo.activated.connect(
            lambda _i, c=combo, t=target_id:
                self.input_source_requested.emit(t, c.currentData()))
        combo.setMinimumWidth(200)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        row.addWidget(combo)
        row.addStretch(1)
        return row


#: What `IMMDevice::GetId` prefixes a render endpoint's guid with. The
#: registry enumerates endpoints by the trailing guid alone, and
#: `SetEndpointVisibility` will not accept that bare form, so the prefix has
#: to go back on. Render endpoints all live under this one flow id.
_RENDER_ID_PREFIX = "{0.0.0.00000000}."


def _full_endpoint_id(endpoint) -> str:
    """The MMDevice-form id for an endpoint that may only know its guid.

    Returns "" rather than a half-built id when there is nothing to work
    from — passing a malformed id to an undocumented COM interface is not
    an experiment worth running.
    """
    raw = (getattr(endpoint, "endpoint_id", "") or "").strip()
    if not raw:
        return ""
    if raw.startswith("{0."):
        return raw
    return _RENDER_ID_PREFIX + raw


def _describe_write(name: str, what: str, result) -> str:
    """What to say about a VCP write, with `verified` reported honestly.

    The three outcomes are genuinely different and the user is told which:
    a monitor that took the call and ignored it is common, and showing that
    as success leaves someone watching a slider snap back with no
    explanation. `verified` None is "we could not read it back" — not a
    failure, and not a success either.
    """
    if not result.ok:
        return f"{name} {what} unchanged: {result.reason}"
    if result.verified is True:
        return f"{name} {what} set to {result.applied}"
    if result.verified is False:
        return (f"{name} accepted the {what} change and did not make it "
                f"(still not {result.applied})")
    return (f"{name} {what} set to {result.applied}, but it could not be "
            f"read back to confirm")


def _audio_text(view) -> str:
    """What the card's Audio row says, or "" for nothing worth saying.

    `audio_is_default` False is never rendered as "not the default": the
    default may simply not have been readable, and a badge that appears on
    True and is silent otherwise makes no claim either way.
    """
    endpoint = view.audio_endpoint
    if endpoint is not None:
        label = getattr(endpoint, "label", "") or "(unnamed endpoint)"
        if view.audio_hidden:
            # Worth saying on the row as well as on the button: an endpoint
            # is ACTIVE while hidden, so every other reading of it looks
            # perfectly healthy while Windows will not offer it to anyone.
            return f"{label} · hidden from the sound list"
        return f"{label} · default output" if view.audio_is_default else label
    return view.audio_note or ""


class MonitorControlModule(BaseModule):
    """The Monitor Control tab."""

    name = "Monitor Control"
    icon = "🖥️"
    description = "Displays, resolutions, refresh rates and monitor audio"
    requires_admin = True
    #: Reading and every display change needs no elevation; only the audio
    #: endpoint writes do, and those gate themselves through require_admin().
    read_only_unelevated = True
    group = ModuleGroup.SYSTEM

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._views: List = []
        self._workers: list = []
        self._identify = IdentifyOverlays()
        #: The most recent capture taken while the desktop was NOT mid-change.
        #: This is the "before" a restore replays, so it must never be
        #: overwritten by a capture taken after monitors moved — by then
        #: Windows has already piled everything onto the survivor.
        self._auto_layout = None
        #: A layout the user asked to keep, kept for the session.
        self._saved_layout = None
        #: Set by the screen signals so the refresh they trigger knows not to
        #: treat what it finds as a good layout.
        self._topology_changed = False

    # ── UI ──

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setObjectName("noticeBanner")
        self._banner.hide()
        layout.addWidget(self._banner)

        self._fix_btn = QPushButton("Use highest refresh rate")
        self._fix_btn.setToolTip(
            "Raise every display to the fastest rate it offers AT the "
            "resolution it is already using. Reverts itself in 15 seconds "
            "unless you confirm.")
        self._fix_btn.clicked.connect(self._do_raise_refresh)
        self._fix_btn.hide()

        self._mute_btn = QPushButton("Disable all monitor sound")
        self._mute_btn.setToolTip(
            "Hide the audio output of every monitor that currently has one "
            "from Windows' sound device list, in one step. Each monitor's own "
            "Audio button turns it back on.")
        self._mute_btn.clicked.connect(self._do_mute_all_audio)
        self._mute_btn.hide()

        action_row = QHBoxLayout()
        action_row.addWidget(self._fix_btn)
        action_row.addWidget(self._mute_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        bar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._identify_btn = QPushButton("Identify")
        self._identify_btn.setToolTip(
            "Show a large number on each screen for a few seconds")
        self._status = QLabel("")
        self._status.setObjectName("muted")
        bar.addWidget(self._refresh_btn)
        bar.addWidget(self._identify_btn)

        # Win+P, as four buttons. What people actually want most of the time.
        bar.addSpacing(16)
        self._arrangement_buttons = {}
        for key, label in dw.ARRANGEMENT_LABELS:
            button = QPushButton(label)
            button.setToolTip(f"Switch the desktop to: {label}")
            button.clicked.connect(
                lambda _checked=False, k=key, t=label: self._do_arrangement(k, t))
            bar.addWidget(button)
            self._arrangement_buttons[key] = button

        bar.addStretch()
        bar.addWidget(self._status)
        layout.addLayout(bar)

        layout.addLayout(self._build_profile_bar())

        self._canvas = ArrangementCanvas()
        self._canvas.moved.connect(self._do_move_monitor)
        layout.addWidget(self._canvas)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_host)
        layout.addWidget(scroll, 1)

        self._refresh_btn.clicked.connect(self.refresh_data)
        self._identify_btn.clicked.connect(self._do_identify)

        # Qt already tracks this; a raw WM_DISPLAYCHANGE filter would be
        # reimplementing it. Stale state that looks authoritative is the
        # failure being avoided — this machine's configuration changed three
        # times while the module was being written.
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda _s: self._on_screens_changed())
            app.screenRemoved.connect(lambda _s: self._on_screens_changed())
            app.primaryScreenChanged.connect(
                lambda _s: self._on_screens_changed())

        self._widget = outer
        return outer

    # ── display profiles ──
    #
    # "This is what my desk looks like", saved and applied later. Identity is
    # the EDID, never `\\.\DISPLAYn` — that is a position in a list and it
    # renumbers when cables move. `profiles.py` carries the whole argument.

    def _build_profile_bar(self):
        row = QHBoxLayout()
        label = QLabel("Profiles")
        label.setObjectName("muted")
        row.addWidget(label)

        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(220)
        self._profile_combo.setToolTip(
            "Saved desktop layouts — every monitor's resolution, refresh "
            "rate, position and which one is primary")
        row.addWidget(self._profile_combo)

        self._profile_apply = QPushButton("Apply")
        self._profile_apply.setToolTip(
            "Reconfigure the desktop to match this profile. Reverts itself "
            "in 15 seconds unless you confirm.")
        self._profile_apply.clicked.connect(self._do_apply_profile)
        row.addWidget(self._profile_apply)

        save = QPushButton("Save current…")
        save.setToolTip("Save the current layout of all monitors as a profile")
        save.clicked.connect(self._do_save_profile)
        row.addWidget(save)

        self._profile_delete = QPushButton("Delete")
        self._profile_delete.clicked.connect(self._do_delete_profile)
        row.addWidget(self._profile_delete)

        row.addSpacing(16)
        windows_label = QLabel("Windows")
        windows_label.setObjectName("muted")
        row.addWidget(windows_label)

        keep = QPushButton("Remember positions")
        keep.setToolTip(
            "Remember where every window is right now, for this session. "
            "Restore puts them back.")
        keep.clicked.connect(self._do_save_windows)
        row.addWidget(keep)

        self._windows_restore = QPushButton("Put windows back")
        self._windows_restore.setToolTip(
            "Move every window back to the monitor and position it was "
            "remembered on. A window whose monitor is not connected is left "
            "alone and named.")
        self._windows_restore.clicked.connect(self._do_restore_windows)
        self._windows_restore.setEnabled(False)
        row.addWidget(self._windows_restore)

        row.addStretch(1)
        self._profile_note = QLabel("")
        self._profile_note.setObjectName("muted")
        row.addWidget(self._profile_note)
        return row

    def _reload_profiles(self) -> None:
        """Refill the list from disk. Cheap: reads sidecar summaries only."""
        from modules.monitor_control import profiles as pf

        self._profile_combo.clear()
        try:
            summaries = pf.list_profiles()
        except Exception as exc:                         # noqa: BLE001
            logger.warning("Could not list display profiles: %s", exc)
            self._profile_note.setText(f"profiles unreadable: {exc}")
            summaries = []
        for summary in summaries:
            self._profile_combo.addItem(
                f"{summary.name}  ({summary.active_count} of "
                f"{summary.monitor_count} on)", summary.name)
        has_any = bool(summaries)
        self._profile_apply.setEnabled(has_any)
        self._profile_delete.setEnabled(has_any)
        if not has_any:
            self._profile_note.setText("no profiles saved yet")
        elif not self._profile_note.text().startswith("profiles unreadable"):
            self._profile_note.setText("")

    def _do_save_profile(self) -> None:
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        from modules.monitor_control import profiles as pf

        name, ok = QInputDialog.getText(self._widget, "Save display profile",
                                        "Name for this layout:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            profile = pf.capture_profile(name)
            path = pf.save_profile(profile)
        except Exception as exc:                         # noqa: BLE001
            logger.warning("Could not save display profile %r: %s", name, exc)
            self._error_banner.set_error(f"The profile was not saved: {exc}")
            return

        # A monitor whose EDID could not be read is saved -- the layout is
        # still worth keeping -- but it is the thing that will make the
        # profile refuse to apply later, so it is said now rather than at
        # the moment someone needs it to work.
        unidentified = [m.identity.label for m in profile.monitors
                        if not m.identity.identified]
        if unidentified:
            QMessageBox.information(
                self._widget, "Saved, with a caveat",
                "Saved to:\n%s\n\nThese monitors could not be identified by "
                "EDID, so this profile will refuse to apply while they are "
                "attached:\n\n%s" % (path, "\n".join(unidentified)))
        self._reload_profiles()
        index = self._profile_combo.findData(name)
        if index >= 0:
            self._profile_combo.setCurrentIndex(index)
        self._status.setText(f"Saved profile “{name}”")

    def _do_delete_profile(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        from modules.monitor_control import profiles as pf

        name = self._profile_combo.currentData()
        if not name:
            return
        answer = QMessageBox.question(
            self._widget, "Delete profile",
            f"Delete the saved profile “{name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            removed = pf.delete_profile(name)
        except Exception as exc:                         # noqa: BLE001
            logger.warning("Could not delete profile %r: %s", name, exc)
            self._status.setText(f"Could not delete “{name}”: {exc}")
            return
        self._status.setText(f"Deleted “{name}”" if removed
                             else f"“{name}” was already gone")
        self._reload_profiles()

    def _do_apply_profile(self) -> None:
        """Apply a saved layout, behind the countdown.

        The largest change this module can make, so it is checked twice
        before anything moves: `can_apply` decides whether every monitor the
        profile turns on is actually present and unambiguous, and its
        refusal is shown as-is — it names the monitor, which is the whole
        point of it.
        """
        from modules.monitor_control import profiles as pf

        name = self._profile_combo.currentData()
        if not name:
            return
        try:
            profile = pf.load_profile(name)
            present = pf.live_identities()
        except Exception as exc:                         # noqa: BLE001
            logger.warning("Could not load profile %r: %s", name, exc)
            self._error_banner.set_error(f"“{name}” could not be read: {exc}")
            return

        ok, reason = pf.can_apply(profile, present)
        if not ok:
            self._error_banner.set_error(
                f"This profile cannot be applied here: {reason}")
            self._status.setText(f"“{name}” refused: {reason}")
            return

        def _apply():
            pf.apply_profile(profile, present, confirm=True)

        self._guarded(_apply, f"Display profile: {name}")

    # ── window layout ──
    #
    # The point of this is the twenty seconds after a monitor comes back:
    # Windows has already piled every window onto whatever display survived,
    # and dragging thirty of them back is the real cost of unplugging one.
    #
    # A capture costs ~4ms, so one is taken after every ordinary refresh. The
    # thing that makes it work is knowing which refreshes are ORDINARY: a
    # refresh triggered by the topology changing must not capture, because by
    # then the windows have already moved and that capture would overwrite
    # the only record of where they belong.

    def _on_screens_changed(self) -> None:
        self._topology_changed = True
        self.refresh_data()

    def _capture_windows(self):
        from modules.monitor_control import window_layout as wl

        try:
            return wl.capture()
        except Exception:                                # noqa: BLE001
            logger.debug("Could not capture the window layout", exc_info=True)
            return None

    def _remember_layout(self) -> None:
        """Keep this as the good layout, after an ordinary refresh."""
        layout = self._capture_windows()
        if layout is not None and layout.windows:
            self._auto_layout = layout
            if self._saved_layout is None:
                self._windows_restore.setEnabled(True)

    def _do_save_windows(self) -> None:
        layout = self._capture_windows()
        if layout is None or not layout.windows:
            self._status.setText("No windows to remember")
            return
        self._saved_layout = layout
        self._windows_restore.setEnabled(True)
        counts = layout.counts()
        where = ", ".join(
            f"{n} on {self._monitor_name_for_key(key)}"
            for key, n in counts.items())
        self._status.setText(
            f"Remembered {len(layout.windows)} window(s) — {where}")

    def _monitor_name_for_key(self, key: str) -> str:
        """A monitor's friendly name for a message, falling back to the key.

        The EDID key is correct but unreadable; "MO27Q28G" is what someone
        recognises. The key is what everything MATCHES on — this is only for
        the sentence.
        """
        for view in self._views:
            identity = getattr(view, "edid_key", None)
            if identity == key:
                return view.name
        from modules.monitor_control import profiles as pf

        try:
            for identity in pf.live_identities():
                if identity.key == key:
                    return identity.friendly_name or key
        except Exception:                                # noqa: BLE001
            logger.debug("Could not name monitor %s", key, exc_info=True)
        return key

    def _do_restore_windows(self) -> None:
        layout = self._saved_layout or self._auto_layout
        if layout is None:
            return
        self._apply_window_layout(layout)

    def _apply_window_layout(self, layout) -> None:
        """Put the windows back and say exactly what happened.

        Not behind the revert countdown: moving a window strands nobody —
        the screen stays readable and the user can drag it back — and the
        guard's revert replays display topology, which is not what changed.
        """
        from modules.monitor_control import window_layout as wl

        try:
            plan = wl.restore(layout, apply=True)
        except Exception as exc:                         # noqa: BLE001
            logger.warning("Restoring the window layout failed: %s", exc)
            self._status.setText(f"Could not put the windows back: {exc}")
            return

        moved = len(plan.actions) - len(plan.failures)
        parts = [f"Put {moved} window(s) back"]
        if plan.refusals:
            # Named, never a count on its own: "3 windows were left alone"
            # sends someone hunting for which three.
            names = ", ".join(sorted({r.title or "(untitled)"
                                      for r in plan.refusals})[:3])
            parts.append(f"{len(plan.refusals)} left alone ({names}"
                         f"{'…' if len(plan.refusals) > 3 else ''}) — their "
                         f"monitor is not connected")
        if plan.failures:
            parts.append(f"{len(plan.failures)} would not move")
            for failure in plan.failures:
                logger.info("Window %r would not move: %s",
                            failure.title, failure.reason)
        self._status.setText(" · ".join(parts))

    def _offer_window_restore(self) -> None:
        """After a topology change: did windows actually move, and shall we?

        Only offers when every monitor in the remembered layout is back AND
        at least one window is somewhere it was not. Offering on a monitor
        being REMOVED would be noise — there is nowhere for those windows to
        go — and offering when nothing moved would be worse than noise.
        """
        from PyQt6.QtWidgets import QMessageBox
        from modules.monitor_control import window_layout as wl

        layout = self._auto_layout
        if layout is None or not layout.windows:
            return
        try:
            present = wl.current_monitors()
        except Exception:                                # noqa: BLE001
            logger.debug("Could not read the current monitors", exc_info=True)
            return
        if not all(key in present for key in layout.monitors):
            return

        now = self._capture_windows()
        if now is None:
            return
        where_now = {w.hwnd: w.monitor_key for w in now.windows}
        moved = [w for w in layout.windows
                 if w.hwnd in where_now
                 and where_now[w.hwnd] != w.monitor_key]
        if not moved:
            # Nothing shifted, so this IS the good layout now.
            self._auto_layout = now
            return

        answer = QMessageBox.question(
            self._widget, "Put your windows back?",
            f"{len(moved)} window(s) moved when the displays changed.\n\n"
            f"Put them back where they were?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer is QMessageBox.StandardButton.Yes:
            self._apply_window_layout(layout)
        else:
            # They chose to keep the new arrangement, so it becomes the one
            # worth remembering — otherwise the same prompt returns on every
            # subsequent screen change.
            self._auto_layout = now

    # ── data ──

    def on_activate(self) -> None:
        self._reload_profiles()
        self.refresh_data()

    def refresh_data(self) -> None:
        """Re-read everything on a worker; the engines walk the driver."""
        if self._widget is None:
            return
        self._status.setText("Reading displays…")

        def _run(_worker):
            return vm.build_views()

        def _done(views):
            if not widget_is_valid(self._widget):
                return
            self._views = views
            self._render()
            # A refresh the topology triggered must NOT capture: by the time
            # it runs, Windows has already moved the windows, and capturing
            # would overwrite the only record of where they belong.
            if self._topology_changed:
                self._topology_changed = False
                self._offer_window_restore()
            else:
                self._remember_layout()

        def _error(message: str):
            if not widget_is_valid(self._widget):
                return
            self._status.setText(f"Could not read the display configuration: "
                                 f"{message}")
            logger.warning("Monitor Control refresh failed: %s", message)

        worker = Worker(_run)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_error)
        self._workers.append(worker)
        self._thread_pool().start(worker)

    def _thread_pool(self):
        from PyQt6.QtCore import QThreadPool
        app = getattr(self, "app", None)
        return getattr(app, "thread_pool", None) or QThreadPool.globalInstance()

    def _render(self) -> None:
        headline = vm.headline(self._views)
        self._banner.setText(headline)
        self._banner.setVisible(bool(headline))

        active = sum(1 for v in self._views if v.active)
        self._status.setText(f"{active} of {len(self._views)} monitors active")

        self._canvas.set_views(self._views)
        self._fix_btn.setVisible(bool(vm.raise_refresh_plan(self._views)))
        self._mute_btn.setVisible(bool(self._audible_outputs()))

        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, view in enumerate(self._views):
            card = _MonitorCard(view)
            card.toggle_requested.connect(self._do_toggle_monitor)
            card.brightness_requested.connect(self._do_brightness)
            card.contrast_requested.connect(self._do_contrast)
            card.input_source_requested.connect(self._do_input_source)
            card.refresh_rate_requested.connect(self._do_set_refresh_rate)
            card.audio_enabled_requested.connect(self._do_set_audio_enabled)
            self._cards_layout.insertWidget(index, card)

    # ── changing things ──
    #
    # Everything below goes through `_apply_guard`: snapshot, apply, then a
    # countdown that puts it back unless someone says otherwise. Nothing
    # here calls a write function directly.

    def _guarded(self, apply_fn, summary: str, snapshot=None, restore=None,
                 confirm_screen=None) -> None:
        """Apply a display change with the revert countdown around it.

        `snapshot`/`restore` default to the whole raw topology, which is the
        right undo for a mode or arrangement change. A control that owns a
        smaller piece of state passes its own pair — an input source is one
        VCP value on one panel, and replaying the topology arrays to undo it
        would put back a configuration that never changed while leaving the
        monitor on the other input.
        """
        def _snapshot_topology():
            return dc.raw_topology_arrays()

        def _restore_topology(arrays):
            paths, modes, npath, nmode = arrays
            ok, reason = dc.apply_raw_topology(paths, modes, npath, nmode)
            if not ok:
                raise OSError(reason)

        take = snapshot or _snapshot_topology
        put_back = restore or _restore_topology

        def _start(before):
            def _resolved(kept):
                outcome = guard.resolve(kept, before, put_back)
                self._status.setText(
                    "Change kept" if outcome is guard.Outcome.KEPT
                    else "Reverted" if outcome is guard.Outcome.REVERTED
                    else "COULD NOT REVERT — see the log")
                if outcome is guard.Outcome.REVERT_FAILED:
                    logger.error("Reverting a display change failed")
                self.refresh_data()

            self._countdown = guard.RevertCountdown(
                seconds=guard.COUNTDOWN_SECONDS, on_resolve=_resolved,
                summary=summary)
            self._countdown.start(affected_screen=confirm_screen)

        result = guard.run_apply(snapshot=take, apply=apply_fn,
                                 start_countdown=_start)
        if not result.applied:
            self._status.setText(result.error or "the change was refused")
            logger.info("Display change refused: %s", result.error)

    def _do_raise_refresh(self) -> None:
        plan = vm.raise_refresh_plan(self._views)
        if not plan:
            return
        changes = [(fix.device_name, fix.resolution[0], fix.resolution[1],
                    fix.to_rate) for fix in plan if fix.device_name]
        summary = ", ".join(f"{f.name} to {f.to_rate:g} Hz" for f in plan)

        def _apply():
            ok, reason = dw.apply_modes(changes)
            if not ok:
                raise OSError(reason)

        self._guarded(_apply, summary)

    def _do_arrangement(self, key: str, label: str) -> None:
        def _apply():
            ok, reason = dw.set_arrangement(key)
            if not ok:
                raise OSError(reason)

        self._guarded(_apply, f"Display arrangement: {label}")

    def _do_toggle_monitor(self, target_id: int, activate: bool) -> None:
        """Connect or disconnect one monitor — immediately, no countdown.

        Deliberately NOT behind `_guarded`. The countdown exists for a
        change that can leave someone looking at a screen with no way back,
        and neither direction of this can do that:

        * Connecting only adds a display. Nothing already visible changes.
        * Disconnecting is refused by `can_set_target_active` (inside
          `set_target_active`) the one time it would be unsafe — turning
          off the LAST active display — so a disconnect that reaches this
          call always leaves at least one other monitor active. If that one
          was primary, Windows reassigns primary to the survivor on its own
          (measured, 2026-09-04): the screen the guard exists to protect
          never goes dark.

        Same reasoning already applied to brightness and contrast: the
        failure the guard is FOR cannot happen here, so asking someone to
        confirm within 15 seconds is friction with nothing behind it.
        """
        view = self._view_for(target_id)
        name = view.name if view else f"target {target_id}"
        verb = "connected" if activate else "disconnected"

        ok, reason = dw.set_target_active(target_id, activate)
        if not ok:
            # Nothing changed, so nothing needs re-reading — and calling
            # `refresh_data()` here would overwrite this message with
            # "Reading displays…" before anyone read it. Matches the
            # outright-refusal branch of `_guarded`.
            self._status.setText(f"{name}: could not be {verb} — {reason}")
            logger.info("Toggle refused for %s (target %s): %s",
                       name, target_id, reason)
            return
        self._status.setText(f"{name}: {verb}")
        self.refresh_data()

    def _do_move_monitor(self, target_id: int, x: int, y: int) -> None:
        """Persist a drag in the arrangement map to the real desktop layout.

        `ArrangementCanvas` only draws and tracks the drag itself -- it
        emits `moved` on release but was never connected to anything, so
        dragging a monitor snapped it into place on screen and then did
        nothing to Windows; the next `refresh_data()` would have silently
        put it back, since `set_views()` rebuilds from the real position.

        The WHOLE layout is written, not just the dragged monitor: the
        primary is at (0, 0) by definition, so dragging it (or dropping
        anything left of/above it) changes every other display's
        coordinates, and a lone write leaves Windows a layout it refuses --
        which is why a vertical nudge of a secondary worked while moving
        the primary sideways silently did nothing.

        No `_guarded` countdown here, same reasoning already applied to
        connect/disconnect and brightness/contrast: only positions
        change, so this can never leave a monitor showing no signal --
        there's nothing for the revert countdown to protect against.
        """
        from modules.monitor_control import _arrangement_geometry as geo

        view = self._view_for(target_id)
        if view is None or not view.device_name:
            return

        active = [v for v in self._views
                  if v.active and v.position and v.resolution and v.device_name]
        current = {v.target_id: (v.position[0], v.position[1],
                                 v.resolution[0], v.resolution[1])
                   for v in active}
        if target_id not in current:
            return
        primary = next((v.target_id for v in active if v.position == (0, 0)),
                       None)

        proposed = dict(current)
        proposed[target_id] = (x, y, current[target_id][2], current[target_id][3])
        others = [r for t, r in proposed.items() if t != target_id]
        if any(geo.overlap(proposed[target_id], o) for o in others):
            self._status.setText(
                f"{view.name}: position not changed -- it would overlap "
                "another monitor")
            self._canvas.set_views(self._views)
            return
        proposed = geo.normalise_to_primary(proposed, primary)

        changes = {v.target_id: proposed[v.target_id][:2]
                   for v in active
                   if proposed[v.target_id][:2] != current[v.target_id][:2]}
        if not changes:
            self._canvas.set_views(self._views)
            return

        ok, reason = dw.set_layout(changes)
        if not ok:
            self._status.setText(f"{view.name}: position not changed -- {reason}")
            logger.info("Move refused for %s (target %s) to (%d, %d): %s",
                       view.name, target_id, x, y, reason)
            self._canvas.set_views(self._views)  # snap the drawing back to reality
            return
        self._status.setText(f"{view.name}: moved")
        self.refresh_data()

    def _do_set_refresh_rate(self, target_id: int, hz: float) -> None:
        """Move ONE monitor to one rate, at the resolution it already has.

        Same guard as everything else that changes a mode: a rate the panel
        cannot show leaves a black screen, and the button that would undo it
        is on it. `apply_modes` takes the resolution explicitly so raising a
        rate can never change resolution as a side effect.
        """
        view = self._view_for(target_id)
        if view is None or not view.device_name or not view.resolution:
            return
        width, height = view.resolution

        def _apply():
            ok, reason = dw.apply_modes([(view.device_name, width, height, hz)])
            if not ok:
                raise OSError(reason)

        self._guarded(_apply, f"{view.name} to {hz:g} Hz at {width}x{height}")

    def _audible_outputs(self) -> list:
        """Display audio outputs that are live and not already hidden.

        Deliberately NOT derived from the per-monitor views. On this machine
        every HDMI output is named "High Definition Audio Device (Digital
        Audio (HDMI))", so no endpoint can be attributed to any one monitor
        and every card reads as "no audio" -- a button built on that would
        never appear. "All monitor sound" is a class, not a per-monitor
        question: every ACTIVE endpoint that belongs to a display output.
        Ghosts (NOTPRESENT) are left alone, and so is an endpoint whose state
        could not be read.
        """
        from modules.monitor_control import display_audio as da

        try:
            endpoints = da.list_render_endpoints()
        except Exception:                                # noqa: BLE001
            logger.debug("Could not read the audio endpoints", exc_info=True)
            return []
        return [e for e in da.display_audio_endpoints(endpoints)
                if e.is_active
                and da.is_hidden(e.raw_state) is False
                and _full_endpoint_id(e)]

    def _do_mute_all_audio(self) -> None:
        """Hide every live monitor audio output, after one confirmation.

        The same `SetEndpointVisibility` write as the per-monitor button, run
        once per output on a single worker. A failure on one does not stop the
        others, and the result says how many worked and which did not -- a
        bulk action that reports only "done" hides which half failed.
        """
        from PyQt6.QtWidgets import QMessageBox
        from modules.monitor_control import display_audio as da

        targets = self._audible_outputs()
        if not targets:
            self._status.setText("No monitor audio is on")
            self._mute_btn.hide()
            return

        default_guid = None
        try:
            detail = da.default_render_endpoint_detail()
            if detail.endpoint_id:
                default_guid = da.endpoint_guid(detail.endpoint_id)
        except Exception:                                # noqa: BLE001
            logger.debug("Could not read the default output", exc_info=True)
        default_hit = bool(default_guid) and any(
            da.endpoint_guid(_full_endpoint_id(e)) == default_guid
            for e in targets)

        question = (f"Hide {len(targets)} monitor audio output"
                    f"{'s' if len(targets) != 1 else ''} (HDMI / DisplayPort) "
                    "from Windows' sound device list?\n\n"
                    "Your headset and speakers are not touched. Each monitor's "
                    "Audio button (or Windows' sound settings) turns them back on.")
        if default_hit:
            question += ("\n\nOne of them is currently your DEFAULT output -- "
                         "Windows will fall back to another device.")
        answer = QMessageBox.question(
            self._widget, "Disable all monitor sound", question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer is not QMessageBox.StandardButton.Yes:
            return

        jobs = [(e.friendly_name or "monitor audio", _full_endpoint_id(e))
                for e in targets]
        self._status.setText(f"Hiding {len(jobs)} monitor audio output(s)\u2026")

        def _run(_worker):
            outcome = []
            for name, full_id in jobs:
                try:
                    da.set_endpoint_enabled(full_id, False,
                                            confirm_supervised=True)
                    outcome.append((name, ""))
                except Exception as exc:  # noqa: BLE001 - reported per output
                    logger.warning("Hiding audio %s failed: %s", full_id, exc)
                    outcome.append((name, str(exc) or type(exc).__name__))
            return outcome

        def _done(outcome):
            if not widget_is_valid(self._widget):
                return
            failed = [(n, why) for n, why in outcome if why]
            if failed:
                self._status.setText(
                    f"Hid {len(outcome) - len(failed)} of {len(outcome)} "
                    "monitor audio outputs; failed: "
                    + "; ".join(f"{n} ({why})" for n, why in failed))
            else:
                self._status.setText(
                    f"Monitor sound disabled ({len(outcome)} output"
                    f"{'s' if len(outcome) != 1 else ''} hidden)")
            self.refresh_data()

        def _error(message: str):
            if not widget_is_valid(self._widget):
                return
            self._status.setText(f"Could not change monitor audio: {message}")
            logger.warning("Bulk audio hide failed: %s", message)

        worker = Worker(_run)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_error)
        self._workers.append(worker)
        self._thread_pool().start(worker)

    def _do_set_audio_enabled(self, target_id: int, turn_on: bool) -> None:
        """Show or hide this monitor's audio endpoint, after asking.

        `SetEndpointVisibility` is confirmed working and immediate (measured
        2026-09-04, three round trips), but it is still a machine-wide
        change, so `confirm_supervised` is passed only once a person has
        actually said yes — that is what the interlock now means.

        Hiding the default output is called out separately: the machine
        keeps its sound, Windows falls back to another device, but it is not
        what someone clicking a monitor's button is likely to expect.
        """
        from PyQt6.QtWidgets import QMessageBox
        from modules.monitor_control import display_audio as da

        view = self._view_for(target_id)
        if view is None or view.audio_endpoint is None:
            return
        endpoint = view.audio_endpoint
        label = getattr(endpoint, "label", view.name)

        if not turn_on:
            question = (f"Hide the audio output of {view.name}?\n\n{label}\n\n"
                        "It disappears from Windows' sound device list until "
                        "you turn it back on here.")
            if view.audio_is_default:
                question += ("\n\nThis is currently your DEFAULT output — "
                             "Windows will fall back to another device.")
            answer = QMessageBox.question(
                self._widget, "Hide this monitor's audio", question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer is not QMessageBox.StandardButton.Yes:
                return

        # IPolicyConfig wants the full MMDevice form; the registry key is
        # only the trailing guid, which is what the endpoint carries.
        full_id = _full_endpoint_id(endpoint)
        if not full_id:
            self._status.setText(
                f"{view.name}: the audio endpoint has no id to act on")
            return

        self._status.setText(
            f"{'Showing' if turn_on else 'Hiding'} {view.name} audio…")

        def _run(_worker):
            da.set_endpoint_enabled(full_id, turn_on, confirm_supervised=True)
            return turn_on

        def _done(now_on: bool):
            if not widget_is_valid(self._widget):
                return
            self._status.setText(
                f"{view.name} audio is now "
                f"{'available' if now_on else 'hidden'}")
            self.refresh_data()

        def _error(message: str):
            if not widget_is_valid(self._widget):
                return
            self._status.setText(f"Could not change {view.name} audio: "
                                 f"{message}")
            logger.warning("Audio visibility write failed for %s: %s",
                           view.name, message)

        worker = Worker(_run)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_error)
        self._workers.append(worker)
        self._thread_pool().start(worker)

    # ── the monitor's own controls (DDC/CI) ──
    #
    # Brightness and contrast do NOT get the countdown, and that is not an
    # oversight: the control that undoes them is the same slider, on the
    # same screen, still reachable. Input source DOES, because switching the
    # panel to another input takes this app off the screen — which is
    # exactly the failure the guard exists for.

    def _view_for(self, target_id: int):
        return next((v for v in self._views if v.target_id == target_id), None)

    def _do_brightness(self, target_id: int, value: int) -> None:
        self._ddc_write(target_id, "brightness", value,
                        lambda device: ddc.set_brightness_for_device(device,
                                                                     value))

    def _do_contrast(self, target_id: int, value: int) -> None:
        self._ddc_write(target_id, "contrast", value,
                        lambda device: ddc.set_contrast_for_device(device,
                                                                   value))

    def _ddc_write(self, target_id: int, what: str, value: int, call) -> None:
        """Run one VCP write on a worker and report what really happened.

        On a worker because a write measures ~0.28s against this hardware,
        and the card is not re-probed afterwards: a re-probe measures ~1.5s,
        and the `WriteResult` already carries both the applied value and
        whether reading it back agreed.
        """
        view = self._view_for(target_id)
        if view is None or not view.device_name:
            return
        device = view.device_name
        name = view.name
        self._status.setText(f"Setting {name} {what} to {value}…")

        def _run(_worker):
            return call(device)

        def _done(result):
            if not widget_is_valid(self._widget):
                return
            self._status.setText(_describe_write(name, what, result))
            if not result.ok:
                logger.info("%s %s write refused: %s", name, what,
                            result.reason)

        def _error(message: str):
            if not widget_is_valid(self._widget):
                return
            self._status.setText(f"Could not set {name} {what}: {message}")
            logger.warning("%s %s write raised: %s", name, what, message)

        worker = Worker(_run)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_error)
        self._workers.append(worker)
        self._thread_pool().start(worker)

    def _do_input_source(self, target_id: int, value: int) -> None:
        """Switch a monitor's input, behind the countdown.

        The snapshot is the panel's OWN previous VCP value, not the topology
        arrays: nothing about the desktop changed, and replaying it would
        undo something that never happened while leaving the monitor on the
        new input. A current input that could not be read means there is no
        way back, so the switch is refused rather than attempted hopefully.
        """
        view = self._view_for(target_id)
        if view is None or not view.device_name:
            return
        capability = view.ddc
        if capability is None or not capability.input_sources_known:
            return

        device = view.device_name
        allowed = capability.input_sources
        previous = capability.current_input

        def _snapshot():
            if previous is None:
                raise OSError("the monitor's current input could not be read, "
                              "so there would be no way back")
            return previous

        def _restore(old):
            result = ddc.set_input_source_for_device(device, old,
                                                     allowed=allowed)
            if not result.ok:
                raise OSError(result.reason)

        def _apply():
            result = ddc.set_input_source_for_device(device, value,
                                                     allowed=allowed)
            if not result.ok:
                raise OSError(result.reason)

        self._guarded(
            _apply,
            f"{view.name}: input switched to {ddc.input_source_name(value)}",
            snapshot=_snapshot, restore=_restore,
            confirm_screen=self._screen_away_from(view))

    def _screen_away_from(self, view):
        """A screen that is NOT this monitor, for the confirm to land on.

        After an input switch Qt still lists the affected screen — the GPU
        is still driving it — while the panel shows another machine, so the
        usual "put it on the affected screen" rule points at the one display
        that cannot show it. Returning None if the screen cannot be
        identified is safe: the countdown lands wherever it would have, and
        doing nothing still reverts.
        """
        screens = QGuiApplication.screens()
        mine = next((s for s in screens if s.name() == view.name), None)
        if mine is None and view.position and view.resolution:
            width, height = view.resolution
            left, top = view.position
            mine = next(
                (s for s in screens
                 if s.geometry().x() == left and s.geometry().y() == top
                 and s.geometry().width() == width
                 and s.geometry().height() == height), None)
        return guard.choose_confirm_screen_avoiding(mine, screens)

    # ── actions ──

    def _do_identify(self) -> None:
        labels = {}
        for screen, view in zip(QGuiApplication.screens(),
                                [v for v in self._views if v.active]):
            labels[screen.name()] = view.name
        self._identify.show(labels)

    # ── lifecycle ──

    def on_start(self, app) -> None:
        self.app = app

    def on_deactivate(self) -> None:
        self._identify.hide()
        self._cancel_all()

    def on_stop(self) -> None:
        self._identify.hide()
        self._cancel_all()

    def _cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    def get_status_info(self) -> str:
        active = sum(1 for v in self._views if v.active)
        return f"Monitor Control — {active} active"
