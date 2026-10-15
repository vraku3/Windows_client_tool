"""Assemble the catalog from its category modules."""
from typing import Dict

from .model import SecurityControl

#: check_* functions in security_reader.py that are deliberately NOT controls,
#: each with the reason. The binding test (test_security_catalog_binding.py)
#: fails on any reader that is neither bound nor listed here, so a check
#: cannot quietly fail to reach the pane.
NOT_A_CONTROL: Dict[str, str] = {
    # -- Defender (Task 6) --------------------------------------------------
    "check_defender_signatures":
        "A freshness reading, not a setting. The Defender tab's "
        "'Update definitions' action is the thing that changes it.",
    "check_defender_threats":
        "A count of detections. Read-only by nature.",
    "check_defender_quarantine":
        "Lists quarantined items; acting on them is a separate operation.",
    "check_defender_last_scan":
        "A timestamp. 'Run quick scan' is the action that changes it.",
    "check_defender_scanning_history":
        "History, not configuration.",
    "check_defender_av_mode":
        "Reports whether Defender is primary, passive or disabled. That is "
        "decided by which AV products are installed, not by a setting.",
    "check_exploit_protection_system":
        "A census over every system-wide process mitigation: how many are "
        "enforced, how many explicitly disabled, how many left at Windows' "
        "default. Each mitigation is its own setting; the census is not "
        "settable, and CFG and ASLR -- the two that are -- are controls "
        "in their own right.",

    # -- Firewall & Network (Task 7) ---------------------------------------
    "check_listening_ports":
        "An inventory of what is listening. The Firewall Rules module is "
        "where a port is closed.",
    "check_dns_servers":
        "Which DNS servers this machine was handed, usually by DHCP. An "
        "inventory, and changing it belongs to the network adapter, not to a "
        "security switch.",

    # -- Device, Services, Features (Task 8) -------------------------------
    # Three cases where two readers ask the machine the SAME question. Binding
    # both would give one setting two cards that write one value, and let a
    # batch stage two conflicting changes to it.
    "check_core_isolation_summary":
        "Reads the same registry value as check_memory_integrity_registry -- "
        "DeviceGuard\\Scenarios\\HypervisorEnforcedCodeIntegrity\\Enabled -- "
        "and differs only in its label. The `memory_integrity` control is "
        "bound to the other one.",
    "check_service_diag_track":
        "A second reader for the DiagTrack service, through a different "
        "helper. The `service_diagtrack` control is bound to "
        "check_service_diagtrack.",
    "check_service_maps_broker":
        "A second reader for the MapsBroker service, through a different "
        "helper. The `service_mapsbroker` control is bound to "
        "check_service_mapsbroker.",

    # Superseded by the Audit & Logging category, which reads every log's
    # size through `wevtutil gl` (works unelevated, even for Security) and
    # adds coverage, mode and the channels that ship off. These two readers
    # read the same value through `Get-WinEvent -ListLog`, which exits 0 and
    # prints "0" when refused -- the exact confusion the new engine removes.
    "check_security_log_size":
        "Superseded by the `security_log_size` control in the Audit & "
        "Logging category, read through log_readiness.read_log.",
    "check_system_log_size":
        "Superseded by the `system_log_size` control in the Audit & Logging "
        "category, read through log_readiness.read_log.",

    "check_disk_cleanup_state":
        "Reports whether cleanmgr.exe is present and Storage Sense has a "
        "policy. A capability reading, and disk cleanup is the Cleanup "
        "module's job, not a security setting.",
    "check_system_restore_disks":
        "How much disk the shadow-copy store is using. An inventory; the "
        "amount is configured per volume with vssadmin, and System Restore "
        "has its own module.",
    "check_windows_version":
        "The build number. It is the input to several checks here -- SMBGhost "
        "gates on it -- but it is not itself a setting.",

    # -- Exploit & CVE (Task 6) --------------------------------------------
    "check_windows_defender_cve_mitigations":
        "An aggregate over 24 individual CVE readers, each of which is its "
        "own control here or in another category. It exists to give the "
        "Overview one card; there is nothing to write.",
}

# Carried instruction (Task 5 addendum, Ruling 6) for whichever task binds
# check_service_wsearch and check_service_sysmain: DO NOT put them in
# NOT_A_CONTROL -- they are real, wireable controls. But their readers
# currently score a RUNNING service as red/bad (_svc_check(..., running_bad=
# True)), which is a false positive in a *security* dashboard: Windows
# Search and SysMain running is a performance/telemetry question, not attack
# surface. That polarity is retained-as-was on purpose -- it was not the
# reader's mistake to fix here, and the verdict belongs in the catalog, not
# in security_reader.py. When these two become catalog entries, give both
# `desired=None` ("no opinion") so a running service is never counted as a
# problem, while the card still shows the reader's honest status. Consequence
# for the pane's "Only problems" filter: it must key off `desired`, NOT off
# the reader's `color` field -- a reader may legitimately colour something
# amber/red that the catalog has no opinion about.


def load_catalog() -> Dict[str, SecurityControl]:
    """id -> control, across every category module."""
    from . import (accounts, audit_logging, defender, device_boot, exploit_cve,
                   features, firewall_network, services)

    catalog: Dict[str, SecurityControl] = {}
    for module in (defender, firewall_network, accounts, device_boot,
                   services, features, exploit_cve, audit_logging):
        for control in module.CONTROLS:
            if control.id in catalog:
                raise ValueError(f"duplicate control id: {control.id}")
            catalog[control.id] = control
    return catalog
