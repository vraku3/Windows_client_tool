"""Effective Windows Update deferral/restart policy (Qt-free).

Windows shows the policy result, not the policy: this reads the local
GPO/Intune values and reports them WITH provenance, so "0 days" is never
mistaken for "unmanaged" and a refusal is never read as "no policy". The
local MDM path (PolicyManager) is included because on a work machine that is
usually where the value really comes from.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCOPES = {
    "wu_policy": r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
    "wu_au": r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU",
    "mdm_update": r"SOFTWARE\Microsoft\PolicyManager\current\device\Update",
    "ux_settings": r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings",
}

SOURCE_LABELS = {
    "wu_policy": "Local policy (GPO)",
    "wu_au": "Local policy (AU)",
    "mdm_update": "MDM / Intune",
    "ux_settings": "Windows settings",
}


@dataclass(frozen=True)
class PolicyRow:
    label: str
    value: str
    source: str = ""
    note: str = ""


@dataclass
class UpdatePolicy:
    rows: List[PolicyRow] = field(default_factory=list)
    quality_deferral_days: Optional[int] = None
    feature_deferral_days: Optional[int] = None
    pause_until: Optional[str] = None
    active_hours: str = "Windows-managed"
    target_release: str = ""
    unreadable: List[str] = field(default_factory=list)


def read_registry_scopes():
    """Scopes -> {value name: value}; missing keys are unmanaged, not refused."""
    import winreg
    scopes: Dict[str, dict] = {}
    unreadable: List[str] = []
    for scope, path in SCOPES.items():
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                                 winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            scopes[scope] = {}
            continue
        except OSError:
            logger.warning("Windows Update policy key %s could not be read", scope, exc_info=True)
            scopes[scope] = {}
            unreadable.append(scope)
            continue
        try:
            values = {}
            with key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _kind = winreg.EnumValue(key, i)
                    values[str(name)] = value
            scopes[scope] = values
        except OSError:
            logger.warning("Windows Update policy values in %s could not be read", scope, exc_info=True)
            scopes[scope] = {}
            unreadable.append(scope)
    return scopes, unreadable


def _int(scopes, *candidates) -> Tuple[Optional[int], str]:
    for scope, name in candidates:
        value = scopes.get(scope, {}).get(name)
        if value is None:
            continue
        try:
            return int(value), SOURCE_LABELS[scope]
        except (TypeError, ValueError):
            logger.warning("Policy %s\\%s is not an integer: %r", scope, name, value)
    return None, ""


def _text(scopes, *candidates) -> Tuple[str, str]:
    for scope, name in candidates:
        value = scopes.get(scope, {}).get(name)
        if value not in (None, ""):
            return str(value), SOURCE_LABELS[scope]
    return "", ""


def read_policy(reader=None) -> UpdatePolicy:
    """Read and summarise the effective deferral/restart policy."""
    scopes, unreadable = (reader or read_registry_scopes)()
    out = UpdatePolicy(unreadable=list(unreadable or []))

    quality, qsrc = _int(scopes, ("wu_policy", "DeferQualityUpdatesPeriodInDays"),
                         ("mdm_update", "DeferQualityUpdatesPeriodInDays"))
    feature, fsrc = _int(scopes, ("wu_policy", "DeferFeatureUpdatesPeriodInDays"),
                         ("mdm_update", "DeferFeatureUpdatesPeriodInDays"))
    out.quality_deferral_days = quality or 0
    out.feature_deferral_days = feature or 0
    out.rows.append(PolicyRow("Quality update deferral",
                              f"{quality} days" if quality is not None else "0 days (not configured)",
                              qsrc or "Default",
                              "" if quality is not None else "Windows applies updates immediately unless another layer defers them."))
    out.rows.append(PolicyRow("Feature update deferral",
                              f"{feature} days" if feature is not None else "0 days (not configured)",
                              fsrc or "Default"))

    pauses = [(_text(scopes, ("wu_policy", "PauseQualityUpdatesEndTime"))[0], "quality"),
              (_text(scopes, ("wu_policy", "PauseFeatureUpdatesEndTime"))[0], "feature"),
              (_text(scopes, ("ux_settings", "PauseQualityUpdatesEndTime"))[0], "quality (settings)"),
              (_text(scopes, ("ux_settings", "PauseFeatureUpdatesEndTime"))[0], "feature (settings)"),
              (_text(scopes, ("ux_settings", "PauseUpdatesExpiryTime"))[0], "all (settings)")]
    end_times = [(when, what) for when, what in pauses if when]
    if end_times:
        out.pause_until = max(when for when, _what in end_times)
        out.rows.append(PolicyRow("Updates paused until", out.pause_until,
                                  SOURCE_LABELS["wu_policy"] if scopes.get("wu_policy", {}).get("PauseQualityUpdatesEndTime")
                                  else SOURCE_LABELS["ux_settings"],
                                  ", ".join(sorted({what for _when, what in end_times}))))
    else:
        out.rows.append(PolicyRow("Updates paused until", "Not paused", "Default"))

    hours_on, hsrc = _int(scopes, ("wu_policy", "SetActiveHours"))
    start, _ = _int(scopes, ("wu_policy", "ActiveHoursStart"))
    end, _ = _int(scopes, ("wu_policy", "ActiveHoursEnd"))
    if hours_on == 1 and start is not None and end is not None:
        out.active_hours = f"{int(start):02d}:00 – {int(end):02d}:00"
        out.rows.append(PolicyRow("Active hours", out.active_hours, hsrc,
                                  "Windows will not restart inside these hours."))
    else:
        out.rows.append(PolicyRow("Active hours", "Windows-managed (not configured)", "Default"))

    deadline_q, dsrc = _int(scopes, ("wu_policy", "ConfigureDeadlineForQualityUpdates"),
                            ("mdm_update", "ConfigureDeadlineForQualityUpdates"))
    deadline_f, _ = _int(scopes, ("wu_policy", "ConfigureDeadlineForFeatureUpdates"),
                         ("mdm_update", "ConfigureDeadlineForFeatureUpdates"))
    grace, _ = _int(scopes, ("wu_policy", "ConfigureDeadlineGracePeriod"))
    no_reboot, _ = _int(scopes, ("wu_policy", "ConfigureDeadlineNoAutoReboot"))
    if deadline_q or deadline_f:
        detail = []
        if deadline_q:
            detail.append(f"quality {deadline_q} day(s)")
        if deadline_f:
            detail.append(f"feature {deadline_f} day(s)")
        if grace:
            detail.append(f"{grace} day grace")
        if no_reboot is not None:
            detail.append("auto-reboot " + ("disabled" if no_reboot else "allowed"))
        out.rows.append(PolicyRow("Install deadlines", ", ".join(detail), dsrc))
    else:
        out.rows.append(PolicyRow("Install deadlines", "Not configured", "Default"))

    release, rsrc = _text(scopes, ("wu_policy", "TargetReleaseVersionInfo"),
                          ("mdm_update", "TargetReleaseVersionInfo"))
    targeted, _ = _int(scopes, ("wu_policy", "TargetReleaseVersion"))
    if targeted == 1 and release:
        out.target_release = release
        out.rows.append(PolicyRow("Target release", release, rsrc))
    else:
        out.rows.append(PolicyRow("Target release", "Not targeted (whatever Windows offers)", "Default"))

    preview, psrc = _int(scopes, ("wu_policy", "ManagePreviewBuilds"))
    if preview is not None:
        level, _ = _int(scopes, ("wu_policy", "ManagePreviewBuildsPolicyValue"))
        meaning = {0: "disabled", 1: "allow optional", 2: "always"}.get(level, f"value {level}")
        out.rows.append(PolicyRow("Preview builds", meaning, psrc))

    drivers, drsrc = _int(scopes, ("wu_policy", "ExcludeWUDriversInQualityUpdate"))
    if drivers is not None:
        out.rows.append(PolicyRow("Drivers in quality updates",
                                  "excluded" if drivers else "included", drsrc))

    access, asrc = _int(scopes, ("wu_policy", "DisableWindowsUpdateAccess"))
    ux_access, _ = _int(scopes, ("wu_policy", "SetDisableUXWUAccess"))
    if access or ux_access:
        out.rows.append(PolicyRow("Windows Update access", "blocked by policy", asrc))

    auto, ausrc = _int(scopes, ("wu_au", "NoAutoUpdate"))
    if auto is not None:
        out.rows.append(PolicyRow("Automatic updates",
                                  "disabled" if auto else "enabled", ausrc))

    if out.unreadable:
        out.rows.append(PolicyRow(
            "Policy source unreadable", ", ".join(SOURCE_LABELS.get(s, s) for s in out.unreadable),
            "", "A refusal is not the same as 'not configured'."))
    return out
