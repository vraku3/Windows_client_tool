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

    # ── UI ──

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

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
        layout.addWidget(self._fix_btn, 0, Qt.AlignmentFlag.AlignLeft)

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

        self._canvas = ArrangementCanvas()
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
            app.screenAdded.connect(lambda _s: self.refresh_data())
            app.screenRemoved.connect(lambda _s: self.refresh_data())
            app.primaryScreenChanged.connect(lambda _s: self.refresh_data())

        self._widget = outer
        return outer

    # ── data ──

    def on_activate(self) -> None:
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
        view = next((v for v in self._views if v.target_id == target_id), None)
        name = view.name if view else f"target {target_id}"

        def _apply():
            ok, reason = dw.set_target_active(target_id, activate)
            if not ok:
                raise OSError(reason)

        self._guarded(
            _apply,
            f"{name}: {'connected' if activate else 'disconnected'}")

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
