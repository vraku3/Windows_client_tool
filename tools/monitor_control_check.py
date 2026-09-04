r"""Everything Monitor Control knows about this machine's displays.

Read-only. Nothing here changes a display, an audio endpoint or a monitor
setting — it exists to run the engine against real hardware, which is the
habit that found every serious defect in this project.

    .venv\Scripts\python.exe tools\monitor_control_check.py
    .venv\Scripts\python.exe tools\monitor_control_check.py --json out.json

Two things it is specifically watching for, both measured here:

* **`\\.\DISPLAYn` names are not stable.** They were reassigned between two
  runs during the session that wrote this module — DISPLAY1 and DISPLAY2
  swapped, while the CCD targets did not move at all. Everything below is
  keyed on the target and the EDID; the GDI name is shown only as the
  transient detail it is.
* **A mode list is not a promise.** The Gigabyte offered 280 Hz at 1440p
  earlier in that same session and 120 Hz an hour later, which is what a
  link renegotiating to less bandwidth looks like.

Exit code is 1 when something is reported as unreadable, so this can be run
after a change and its silence means something.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from modules.monitor_control import display_audio as da       # noqa: E402
from modules.monitor_control import display_config as dc      # noqa: E402
from modules.monitor_control import display_modes as dm       # noqa: E402
from modules.monitor_control import monitor_identity as mi    # noqa: E402
from modules.monitor_control import view_model as vm          # noqa: E402


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="also write the findings here")
    args = parser.parse_args()

    elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    problems = []
    report = {"elevated": elevated, "monitors": []}

    print(f"elevated: {elevated}")

    # -- topology -------------------------------------------------------
    _rule("DISPLAY TOPOLOGY (QueryDisplayConfig)")
    topology = dc.query()
    print(f"{len(topology.paths)} paths, {len(topology.active_paths())} active")
    print(f"active targets           : {sorted(topology.active_target_ids())}")
    print(f"available targets        : {sorted(topology.available_target_ids())}")
    print(f"connected but not in use : "
          f"{sorted(topology.inactive_available_target_ids())}")

    # -- what the tab would say -----------------------------------------
    _rule("WHAT THE TAB WOULD SAY")
    views = vm.build_views(topology)
    banner = vm.headline(views)
    print(f"headline: {banner or '(nothing to report)'}")
    plan = vm.raise_refresh_plan(views)
    if plan:
        print("\none-click 'use highest refresh rate' would set:")
        for fix in plan:
            print(f"   {fix.name}: {fix.resolution[0]}x{fix.resolution[1]}  "
                  f"{fix.from_rate:g} Hz -> {fix.to_rate:g} Hz")
    else:
        print("nothing to raise — every active display is at its best rate")

    # -- per monitor ----------------------------------------------------
    _rule("PER MONITOR")
    for view in views:
        print(f"\n{view.label}   [target {view.target_id}]")
        print(f"   state        : {'ACTIVE' if view.active else 'connected, not in use'}")
        print(f"   mode         : {vm.describe(view)}")
        native = view.native_resolution
        print(f"   native (EDID): "
              f"{f'{native[0]}x{native[1]}' if native else 'UNREADABLE'}")
        if native is None:
            problems.append(f"{view.name}: EDID native resolution unreadable")
        print(f"   gdi device   : {view.device_name or '(none — not attached)'}")
        if view.rates_at_resolution:
            print(f"   rates here   : "
                  f"{', '.join(f'{r:g}' for r in view.rates_at_resolution)}")
        if view.device_name:
            offered = dm.resolutions(view.device_name)
            print(f"   resolutions  : {len(offered)} offered, "
                  f"largest {offered[0] if offered else '-'}")
            if native and offered and offered[0] != native:
                print(f"                  (larger than native — the GPU is "
                      f"offering downsampled modes)")
        report["monitors"].append({
            "target_id": view.target_id, "name": view.name,
            "connector": view.connector, "active": view.active,
            "resolution": view.resolution, "refresh_hz": view.refresh_hz,
            "native": view.native_resolution,
            "device_name": view.device_name,
            "rates_at_resolution": list(view.rates_at_resolution),
        })

    # -- audio ----------------------------------------------------------
    _rule("DISPLAY AUDIO ENDPOINTS")
    endpoints = da.list_render_endpoints()
    display_endpoints = [e for e in endpoints if e.is_display_audio]
    default_id = da.default_render_endpoint()
    print(f"{len(endpoints)} render endpoints, "
          f"{len(display_endpoints)} belong to displays")
    if default_id is None:
        print("default output: COULD NOT BE DETERMINED")
        problems.append("the default audio endpoint could not be read")
    for endpoint in display_endpoints:
        if endpoint.state.name == "NOTPRESENT":
            continue
        marker = "  <- DEFAULT" if default_id and endpoint.endpoint_id in default_id else ""
        print(f"   [{endpoint.state.name}] {endpoint.friendly_name}{marker}")

    for view in views:
        matches = da.matches_for_monitor(endpoints, view.name)
        live = [m for m in matches if m.state.name != "NOTPRESENT"]
        if len(live) > 1:
            print(f"   ambiguous: {view.name} matches {len(live)} live "
                  f"endpoints — the UI must not guess")
            problems.append(f"{view.name}: ambiguous audio endpoint")

    # -- DDC/CI ---------------------------------------------------------
    _rule("DDC/CI (monitor hardware control)")
    try:
        from modules.monitor_control import ddc
        with ddc.open_monitors() as monitors:
            if not monitors:
                print("no physical monitors answered — nothing to control")
            for monitor in monitors:
                capability = ddc.probe(monitor)
                print(f"\n{monitor.description}")
                print(f"   responds     : {capability.responded}  "
                      f"({capability.reason})")
                if capability.brightness:
                    print(f"   brightness   : {capability.brightness.current}"
                          f" / {capability.brightness.maximum}")
                if capability.input_sources_known:
                    print(f"   inputs       : "
                          + ", ".join(f"0x{v:02X} {n}" for v, n
                                      in capability.input_source_choices()))
                    print(f"   current input: {capability.current_input_name}")
                elif capability.responded:
                    print("   inputs       : not claimed — switching refused")
    except Exception as exc:                             # noqa: BLE001
        print(f"DDC/CI probe failed: {type(exc).__name__}: {exc}")
        problems.append(f"DDC/CI probe failed: {exc}")

    # -- verdict --------------------------------------------------------
    _rule("UNREADABLE / AMBIGUOUS")
    if problems:
        for problem in problems:
            print(f"   {problem}")
    else:
        print("   (none — everything above was read, not assumed)")

    if args.json:
        report["problems"] = problems
        report["headline"] = banner
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"\nwrote {args.json}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
