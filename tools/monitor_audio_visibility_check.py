r"""Repeatable version of the manual round trip that first confirmed
`IPolicyConfig::SetEndpointVisibility` actually works (2026-09-04): hide a
display's audio endpoint, verify it went hidden, show it again, verify it
came back — on a NON-default endpoint, so a bug here costs at most a
temporarily-missing extra output, never the machine's sound.

This is a WRITE tool, unlike monitor_control_check.py's read-only sibling.
It refuses to run without --yes, and refuses outright (never guesses) if:
  * no non-default, currently-live display-audio endpoint exists to test on
  * any state read comes back unreadable (None) at any point in the sequence
  * the state after a write does not match what was asked for

    .venv\Scripts\python.exe tools\monitor_audio_visibility_check.py
    .venv\Scripts\python.exe tools\monitor_audio_visibility_check.py --yes

Exit code is 1 on any refusal or unexpected state, 0 only when the full
hide -> verify -> show -> verify cycle completed and the endpoint ended
exactly where it started.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from modules.monitor_control import display_audio as da  # noqa: E402

_RENDER_ID_PREFIX = "{0.0.0.00000000}."


def _full_endpoint_id(endpoint: da.AudioEndpoint) -> str:
    """Same construction as monitor_module._full_endpoint_id -- an endpoint
    read from the registry may only carry its bare guid."""
    raw = (endpoint.endpoint_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("{0."):
        return raw
    return _RENDER_ID_PREFIX + raw


def _pick_test_endpoint(endpoints, default_id):
    """A live, non-default display-audio endpoint to test against, or None.

    Never the default output: hiding it (even correctly, even briefly)
    changes what the user hears mid-test, and this tool's whole point is
    to prove the write path, not to interrupt whatever is currently
    playing.
    """
    candidates = [
        e for e in endpoints
        if e.is_display_audio
        and e.state.name != "NOTPRESENT"
        and not (default_id and e.endpoint_id in default_id)
    ]
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true",
                        help="actually run the round trip (default: dry-run, picks and "
                             "reports the endpoint but writes nothing)")
    args = parser.parse_args()

    endpoints = da.list_render_endpoints()
    default_id = da.default_render_endpoint()
    if default_id is None:
        print("REFUSED: could not determine the default audio endpoint at all -- "
              "a refused read is never treated as \"probably fine\".")
        return 1

    endpoint = _pick_test_endpoint(endpoints, default_id)
    if endpoint is None:
        print("REFUSED: no non-default, currently-live display-audio endpoint "
              "exists to test on. Connect a second display with audio, or "
              "run this while more than one is active.")
        return 1

    full_id = _full_endpoint_id(endpoint)
    if not full_id:
        print(f"REFUSED: {endpoint.label} has no usable endpoint id.")
        return 1

    print(f"test endpoint: {endpoint.label}")
    print(f"  endpoint_id (full): {full_id}")
    print(f"  is default output : False (never tested against the default)")

    baseline_hidden = da.is_hidden(endpoint.raw_state)
    if baseline_hidden is None:
        print("REFUSED: could not read this endpoint's current hidden state "
              "-- refusing to write on top of an unknown baseline.")
        return 1
    print(f"  baseline hidden   : {baseline_hidden}")

    if not args.yes:
        print("\nDry run only (pass --yes to actually hide/show this endpoint "
              "twice). Nothing was written.")
        return 0

    if baseline_hidden:
        print("\nREFUSED: this endpoint is already hidden. Re-show it manually "
              "(Sound settings) before running this tool, so the round trip "
              "starts from a known, visible baseline.")
        return 1

    def _reread_hidden(step: str):
        fresh = [e for e in da.list_render_endpoints() if e.endpoint_id == endpoint.endpoint_id]
        if not fresh:
            print(f"REFUSED at {step}: the endpoint no longer enumerates at all.")
            return None
        hidden = da.is_hidden(fresh[0].raw_state)
        if hidden is None:
            print(f"REFUSED at {step}: re-read came back unreadable.")
        return hidden

    print("\nhiding...")
    da.set_endpoint_enabled(full_id, False, confirm_supervised=True)
    hidden_now = _reread_hidden("post-hide re-read")
    if hidden_now is not True:
        print(f"UNEXPECTED: expected hidden=True after hiding, got {hidden_now}.")
        return 1
    print("  confirmed hidden")

    print("showing...")
    da.set_endpoint_enabled(full_id, True, confirm_supervised=True)
    visible_now = _reread_hidden("post-show re-read")
    if visible_now is not False:
        print(f"UNEXPECTED: expected hidden=False after showing, got {visible_now}. "
              f"The endpoint may still be hidden -- check Sound settings.")
        return 1
    print("  confirmed visible again")

    print("\nRound trip complete: hide -> verified hidden -> show -> verified "
          "visible. Endpoint is back where it started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
