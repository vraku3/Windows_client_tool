"""Who manages Windows Update on this machine, and from where (Qt-free).

Windows itself shows only the result. This reports the local evidence with
provenance: a WSUS server, Windows Update for Business policy, an MDM
enrollment, or nothing found. "Nothing found" is a definite local answer; a
refused key is listed as unreadable and never becomes "unmanaged".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

MANAGED_KEY = "SOFTWARE\\Microsoft\\Enrollments"
CLASSES = (
    ("FeatureUpdates", "Feature updates"),
    ("QualityUpdates", "Quality updates"),
    ("DriverUpdates", "Driver updates"),
    ("OtherUpdates", "Other updates"),
)
SOURCE_LABELS = {
    "wu_policy": "Local policy (GPO)",
    "mdm_update": "MDM / Intune",
}


@dataclass(frozen=True)
class OwnershipRow:
    label: str
    value: str
    source: str = ""
    note: str = ""


@dataclass
class UpdateOwnership:
    managed_by: str = "No local WSUS/MDM policy found"
    wsus_url: Optional[str] = None
    rows: List[OwnershipRow] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)


def _read_enrollments():
    import winreg
    found = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MANAGED_KEY, 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return found, False
    except OSError:
        logger.warning("MDM enrollment key could not be read", exc_info=True)
        return found, True
    try:
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                guid = winreg.EnumKey(key, i)
                try:
                    with winreg.OpenKey(key, guid, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
                        provider = ""
                        state = None
                        for name in ("ProviderID", "EnrollmentState"):
                            try:
                                value = winreg.QueryValueEx(sub, name)[0]
                            except FileNotFoundError:
                                continue
                            if name == "ProviderID":
                                provider = str(value)
                            else:
                                state = int(value)
                        if provider:
                            found.append({"id": guid, "provider": provider, "state": state})
                except OSError:
                    logger.debug("Enrollment %s unreadable", guid, exc_info=True)
    except OSError:
        logger.warning("MDM enrollments could not be enumerated", exc_info=True)
        return found, True
    return found, False


def _read():
    from modules.updates.update_policy import read_registry_scopes
    scopes, unreadable = read_registry_scopes()
    try:
        enrollments, enroll_error = _read_enrollments()
    except Exception:
        logger.warning("MDM enrollments could not be read", exc_info=True)
        enrollments, enroll_error = [], True
    if enroll_error:
        unreadable = list(unreadable) + ["enrollments"]
    return {"scopes": scopes, "enrollments": enrollments, "unreadable": unreadable}


def _text(values: dict, *names) -> str:
    for name in names:
        value = values.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _int(values: dict, name) -> Optional[int]:
    value = values.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Ownership value %s is not an integer: %r", name, value)
        return None


def read_ownership(reader=None) -> UpdateOwnership:
    data = (reader or _read)()
    scopes = data.get("scopes") or {}
    enrollments = data.get("enrollments") or []
    unreadable = list(data.get("unreadable") or [])
    out = UpdateOwnership(unreadable=unreadable)
    policy = scopes.get("wu_policy") or {}
    mdm = scopes.get("mdm_update") or {}

    wsus = _text(policy, "WUServer")
    alt = _text(policy, "WUStatusServer")
    target = _text(policy, "TargetGroup")
    target_on = _int(policy, "TargetGroupEnabled")
    no_internet = _int(policy, "DoNotConnectToWindowsUpdateInternetLocations")

    drivers = {
        key: _int(policy, f"SetPolicyDrivenUpdateSourceFor{key}")
        for key, _label in CLASSES
    }
    wufb_policy = any(value is not None for value in drivers.values()) or bool(
        _text(policy, "BranchReadinessLevel") or _text(policy, "TargetReleaseVersionInfo")
        or _int(policy, "DeferQualityUpdates") or _int(policy, "DeferFeatureUpdates"))

    provider_rows = [row for row in enrollments if row.get("provider")]
    if wsus:
        out.managed_by = "WSUS"
    elif wufb_policy:
        out.managed_by = "Windows Update for Business"
    elif provider_rows:
        out.managed_by = "MDM: " + ", ".join(sorted({row["provider"] for row in provider_rows}))
    else:
        out.managed_by = "No local WSUS/MDM policy found"
    out.wsus_url = wsus or None
    out.rows.append(OwnershipRow("Managed by", out.managed_by,
                                 SOURCE_LABELS["wu_policy"] if (wsus or wufb_policy) else
                                 (SOURCE_LABELS["mdm_update"] if provider_rows else "Local evidence")))
    out.rows.append(OwnershipRow("WSUS server", wsus or "Not configured",
                                 SOURCE_LABELS["wu_policy"] if wsus else "Default",
                                 "An update server other than Windows Update." if wsus else
                                 "No WSUS endpoint is set in local policy."))
    out.rows.append(OwnershipRow("WSUS status server", alt or "Not configured",
                                 SOURCE_LABELS["wu_policy"] if alt else "Default"))
    if target and target_on != 0:
        out.rows.append(OwnershipRow("Target group", target, SOURCE_LABELS["wu_policy"]))
    if no_internet is not None:
        out.rows.append(OwnershipRow("Internet update locations",
                                     "blocked" if no_internet else "allowed",
                                     SOURCE_LABELS["wu_policy"]))
    for key, label in CLASSES:
        value = drivers.get(key)
        if value is None:
            source = "WSUS (implied by WUServer)" if wsus else "Windows Update (no source policy)"
        else:
            source = "Windows Update (WUfB)" if value == 1 else "WSUS / policy server"
        out.rows.append(OwnershipRow(f"{label} source", source,
                                     SOURCE_LABELS["wu_policy"] if value is not None else "Default"))
    if provider_rows:
        names = ", ".join(sorted({row["provider"] for row in provider_rows}))
        out.rows.append(OwnershipRow("MDM enrollment", f"{names} ({len(provider_rows)} enrollment(s))",
                                     SOURCE_LABELS["mdm_update"]))
    else:
        out.rows.append(OwnershipRow("MDM enrollment", "No enrollment found", "Default"))
    if mdm:
        source_policy = _text(mdm, "AllowAutoUpdate")
        if source_policy:
            out.rows.append(OwnershipRow("MDM AllowAutoUpdate", source_policy, SOURCE_LABELS["mdm_update"]))
    if unreadable:
        out.rows.append(OwnershipRow("Source unreadable", ", ".join(unreadable), "",
                                     "A refusal is not the same as 'unmanaged'."))
    return out
