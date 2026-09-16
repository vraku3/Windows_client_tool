r"""Delivery Optimization is turned off by policy, not by killing the service.

services.json's disable_delivery_optimization shipped a step Windows refuses
outright:

    services.json  disable_delivery_optimization   {"type": "service",
                                                    "name": "DoSvc",
                                                    "start_type": "disabled"}

Measured with tools/service_config_probe.py, which writes nothing
(ChangeServiceConfig with SERVICE_NO_CHANGE for every field):

    elevated    DoSvc REFUSED; RemoteRegistry, DiagTrack, SysMain, WSearch,
                MapsBroker, RetailDemo, WMPNetworkSvc, lfsvc all ALLOWED
    unelevated  every one refused at OpenService, a step earlier

`sc sdshow DoSvc` grants Builtin Administrators DC (SERVICE_CHANGE_CONFIG), so
the DACL is not the obstacle: Windows protects that service beyond its own
permissions. The real 2026-08-29 run proves the consequence -- an elevated
session logged the refusal and DoSvc\Start is still 2, so the row read
"suboptimal" and applying it errored, every time, for everyone.

Killing the service was also the wrong instrument. DoSvc downloads updates
generally; peer-to-peer SHARING -- which is what both descriptions promise to
stop -- is the DODownloadMode policy, and this repo already turns it off that
way in three other tweaks (disable_peer_updates,
wu_disable_delivery_optimization_p2p, wu_delivery_optimization_lan_only). The
ids stay put: six built-in presets reference them.
"""
import json
import os

_DEFS = os.path.join(os.path.dirname(__file__), "..", "src", "modules",
                     "tweaks", "definitions")

_DO_POLICY_KEY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization"


def _definitions(filename):
    with open(os.path.join(_DEFS, filename), encoding="utf-8") as f:
        return json.load(f)


def _tweak(filename, tweak_id):
    for entry in _definitions(filename):
        if entry["id"] == tweak_id:
            return entry
    raise AssertionError(f"{tweak_id} is gone from {filename} -- six "
                         "built-in presets still name it by id")


def test_the_tweak_stops_sharing_by_policy_not_by_the_service():
    tweak = _tweak("services.json", "disable_delivery_optimization")
    kinds = {step["type"] for step in tweak["steps"]}
    assert "service" not in kinds, (
        "Windows refuses ChangeServiceConfig on DoSvc even to an elevated "
        "administrator, so this step can never succeed")
    targets = {(step.get("key"), step.get("value")) for step in tweak["steps"]}
    assert (_DO_POLICY_KEY, "DODownloadMode") in targets


def test_the_policy_value_actually_means_no_peering():
    tweak = _tweak("services.json", "disable_delivery_optimization")
    mode = next(step for step in tweak["steps"]
                if step.get("value") == "DODownloadMode")
    # 0 = HTTP only, no peering. 1 is LAN peering and 3 is internet peering,
    # either of which would leave the sharing this tweak promises to stop.
    assert mode["data"] == 0
    assert mode["kind"] == "DWORD"


def test_no_definition_still_tries_to_disable_dosvc():
    """The whole class, not just the one that was reported."""
    offenders = []
    for filename in os.listdir(_DEFS):
        if not filename.endswith(".json"):
            continue
        try:
            entries = _definitions(filename)
        except (ValueError, IsADirectoryError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for step in entry.get("steps", []):
                if (step.get("type") == "service"
                        and str(step.get("name", "")).lower() == "dosvc"):
                    offenders.append(f"{filename}:{entry.get('id')}")
    assert offenders == [], (
        f"these can never succeed -- Windows refuses DoSvc: {offenders}")
