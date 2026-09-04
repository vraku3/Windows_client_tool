# Monitor Control — plan and handoff

**Branch:** `feat/monitor-control` (11 commits ahead of `master`, unmerged)
**Written:** 2026-09-04, mid-flight, immediately before an OS reinstall.

This document exists because the first two stages were executed from a plan
that only ever lived in a session's context. Everything below was
reconstructed from the code and the commit messages on the branch, so it is
accurate about what is *there*; the "remaining" section is the design as the
code already implies it, not a wish list.

## What the module is

One tab that shows what the displays are doing and changes them. The engine
half is pure Python with no Qt and no display required, so all of it is
tested against captured fixtures from this machine (3 monitors: a Gigabyte
MO27Q28G, a Dell, and an LG ULTRAWIDE that is connected and switched off).

`src/modules/monitor_control/`

| File | Role | In the UI? |
|---|---|---|
| `display_config.py` | CCD topology reader (`QueryDisplayConfig`) — the only source of truth | yes |
| `display_modes.py` | what each display *could* do (mode enumeration) | via `view_model` |
| `monitor_identity.py` | friendly names, device paths, EDID native resolution | via `view_model` |
| `display_writes.py` | the write layer: modes, Win+P arrangements, connect/disconnect | yes |
| `_apply_guard.py` | snapshot -> apply -> 15s countdown -> revert unless confirmed | yes |
| `view_model.py` | joins the engines into one `MonitorView` per monitor + the headline | yes |
| `_arrangement_canvas.py`, `_arrangement_geometry.py`, `_screen_overlay.py` | the map, and Identify | yes |
| `ddc.py` | DDC/CI over Dxva2 — brightness, contrast, input source | **NO** |
| `display_audio.py` | which monitor carries which audio endpoint | **NO** |
| `profiles.py` | named display profiles keyed on EDID | **NO** |
| `window_layout.py`, `window_census.py` | capture and restore window positions | **NO** |

The four unwired files are complete and tested (`tests/test_ddc.py`,
`tests/test_display_audio.py`, `tests/test_monitor_profiles.py`,
`tests/test_window_layout.py`). They are engines with no caller yet.

## The one rule the module is built around

**Every display change goes through `_apply_guard`.** Snapshot, apply, then a
15-second countdown that puts it back unless someone confirms. Nothing in
`monitor_module.py` calls a write function directly.

The failure being designed around is a mode the monitor cannot show: the
screen goes dark and the control that would undo it is on that screen. Doing
nothing has to be the safe answer, so doing nothing reverts. Three
corollaries already implemented and worth not breaking:

* The confirm window is placed on a screen that still exists *after* the
  change (`choose_confirm_screen`).
* A failed apply starts no countdown, and a change whose snapshot could not
  be taken is refused rather than attempted hopefully.
* The revert replays the **raw** path and mode arrays captured before the
  change, not a reconstruction from a parsed copy.

## Done

**Stage 1.1 — read.** Commits `8a7fdb8` … `e11ad73`. Topology, identity,
modes, DDC, audio, profiles, window layout, the safety guard, the canvas,
the view model, the tab itself, and `tools/monitor_control_check.py` as a
read-only harness against the real hardware.

**Stage 1.2 — write.** Commits `6fca531`, `e28b8dd`, `db87414`. The write
layer, the revert countdown proven against a real display, and every button
wired through the guard:

* "Use highest refresh rate" — appears only when something is actually below
  its best, and raises each display *at the resolution it already has*.
  Batched through `apply_modes`, which refuses the whole set if any one mode
  is unavailable.
* The four Win+P arrangements, via `SDC_TOPOLOGY_*` rather than
  `DisplaySwitch.exe`: the flag form returns a result that can be reported,
  where the exe returns before Windows has finished and tells you nothing.
  Never OR-ed with `SDC_USE_SUPPLIED_DISPLAY_CONFIG` — they are two different
  ways of saying what to apply and combining them is
  `ERROR_INVALID_PARAMETER`.
* Connect / Disconnect per monitor, refusing to switch off the last one.

Suite at the tip of the branch: `PYTEST_EXIT=0`, no failures.

**Stage 1.3 — DDC controls and the audio a monitor carries.** Done as
written, on the machine rebuilt after the reinstall. `build_views()` fills
`audio_endpoint`, `audio_is_default` and `ddc`; the card grew a read-only
audio row, brightness and contrast sliders and an input picker; brightness
and contrast go without the countdown, input source with it.

Four things the plan did not say, all of them measured rather than reasoned:

* **The pairing was the missing piece.** A `MonitorView` knows
  `\\.\DISPLAY2`; a `PhysicalMonitor` sits behind an HMONITOR, and nothing
  joined them. `GetMonitorInfoW`'s `szDevice` does, exactly and one-to-one.
  Matching on the description cannot work: Windows calls the Gigabyte
  "Generic PnP Monitor" while its view is named "MO27Q28G", so
  `find_monitor(monitors, view.name)` finds nothing at all. Hence
  `PhysicalMonitor.device`, `find_monitor_for_device`, and the
  `*_for_device` wrappers, which are the only form the UI uses.
* **A capability must not carry its handle out of the block it was probed
  in.** `probe_device` returns one with `monitor=None` on purpose — the
  handle died in `open_monitors()`, and a tab that refreshes on a timer
  would otherwise be calling into memory `DestroyPhysicalMonitors` has
  already taken back. Every write re-finds its monitor.
* **A write costs ~0.28s; a re-probe costs ~1.5s.** So writes run on a
  worker, fire on `sliderReleased` rather than `valueChanged`, and the card
  is never re-probed afterwards — `WriteResult` already carries the applied
  value and whether the read-back agreed. `build_views()` itself is now
  ~3.4s for three monitors, which is why it stays on a worker.
* **The input-source confirm must AVOID its own screen.** After the switch
  Qt still lists that screen (the GPU is still driving it) while the panel
  shows another machine, so `choose_confirm_screen`'s "prefer the affected
  screen" rule points at the one display that cannot show the dialog.
  `choose_confirm_screen_avoiding` is the opposite rule, for exactly this.
  Note Qt names screens by MODEL (`S2719DGF`), not by device path, so the
  view→QScreen match goes by name and falls back to geometry.

Proven against the hardware: brightness 46 → 36, `verified=True`, the panel
actually moved, restored to 46.

**Added after 1.3, at the user's request** — per-monitor refresh-rate
buttons (one per rate offered *at the resolution in use*, the current one
checked and disabled) and a per-monitor audio on/off. The audio button
forced the supervised round trip the interlock was waiting for, and it
overturned a reading: `SetEndpointVisibility` never touches the documented
nibble, it flips `0x10000000`, so that bit is `DEVICE_STATE_HIDDEN` and an
endpoint is routinely ACTIVE *and* invisible. The old machine had those
endpoints hidden; its driver was not different.

**Stage 2 — display profiles.** Done. A Profiles row above the arrangement
canvas: a list, Apply, Save current…, Delete. Apply goes through the guard
like everything else. `can_apply`'s refusal is shown verbatim because it
names the monitor, which is the entire point of it, and saving warns at once
about any monitor whose EDID could not be read — that is what will make the
profile refuse later, so it is said while the person is still there.

One real defect came out of wiring it: **an EDID descriptor is not always
newline-terminated.** The Gigabyte fills all 13 bytes and ends with `\x00`
where the spec says 0x0A padded with 0x20, and `str.strip()` does not remove
NUL — so the serial carried one into the identity key, into profile JSON,
and into every comparison. The fixture had claimed to be "byte for byte what
the registry holds" and used `\n`, which is why no test caught it. Fixed in
`f44e0f8`; verified end to end against the hardware, including a refusal
that correctly names an absent monitor.

## Remaining

**Not to be wired without a supervised first run:** the *audio endpoint
enable/disable* writes in `display_audio.py`. They drive
`IPolicyConfig::SetEndpointVisibility`, an undocumented interface with no
published header, and **nothing in that file has ever been executed against a
real endpoint**. It is behind a `confirm_supervised=True` interlock for that
reason. Before it is trusted it needs: disable -> re-read the state ->
re-enable -> re-read, on an endpoint nobody is listening to, confirming the
state actually moved and actually came back. Disabling the wrong one takes
the sound off the machine.

### Stage 3 — window layout

`window_layout.py` + `window_census.py`, also complete and unwired. Capture
where every window is and put them back after the monitors change. The
natural trigger is the `screenAdded` / `screenRemoved` signals the module
already listens to.

## Where to pick it up

```
git checkout feat/monitor-control
python tools/monitor_control_check.py     # read-only, prints what the hardware says
python -m pytest tests/ -q                # whole suite; last run PYTEST_EXIT=0
python src/main.py                        # the tab is under System
```

`tools/monitor_revert_check.py` exercises the countdown against a real
display. Both harnesses are read-only until told otherwise.

Note that the module declares `requires_admin` with `read_only_unelevated`:
display and DDC work needs no elevation at all, and only the audio endpoint
writes do. Gating the whole module on admin would disable a tab that is
mostly usable without it — the same reasoning as `debloat_module.py` and
`store_apps_module.py`.
