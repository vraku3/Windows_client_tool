"""What an event ID or a bugcheck code MEANS -- the sentence a log never prints.

Qt-free, so the tables are testable without a display. Two lookups:

* `lookup_event(provider, event_id)` -> `EventInfo` or None
* `bugcheck_info(code)` -> `BugcheckInfo` or None

None is the common, honest answer. A wrong explanation of an event sends
someone hunting for a problem they do not have, so an ID that is not in the
table says nothing rather than guessing. Entries are matched on the provider
name AND the ID, because IDs are only unique per provider (7 is a disk error
from `disk` and something else entirely from anyone else).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class EventInfo:
    title: str
    meaning: str
    cause: str
    next_step: str = ""


@dataclass(frozen=True)
class BugcheckInfo:
    code: int
    name: str
    cause: str
    params: Tuple[str, str, str, str] = ("", "", "", "")


def _e(title: str, meaning: str, cause: str, next_step: str = "") -> EventInfo:
    return EventInfo(title, meaning, cause, next_step)


#: (provider name lowercased, event id) -> EventInfo. The provider is matched
#: on a "startswith" so `Microsoft-Windows-Kernel-Power` and its older short
#: spelling `Kernel-Power` both hit.
_EVENTS: Dict[Tuple[str, int], EventInfo] = {
    ("kernel-power", 41): _e(
        "Unexpected reboot (Kernel-Power 41)",
        "Windows restarted without shutting down cleanly first.",
        "Power loss, a hard reset or power-button hold, a hang, or a bugcheck. "
        "BugcheckCode 0 means no bugcheck was recorded: the machine lost power or was "
        "reset while running (PSU, motherboard, overclock, or a hang followed by a reset).",
        "Look for event 1001 (BugCheck) and 6008 around the same time; if there is "
        "none, suspect power delivery or hardware rather than a driver."),
    ("eventlog", 6008): _e(
        "Previous shutdown was unexpected",
        "The last shutdown was not clean.",
        "Same family as Kernel-Power 41: crash, hang-then-reset, or power loss.",
        "Compare with Kernel-Power 41 and BugCheck 1001 for the same boot."),
    ("eventlog", 6005): _e("Event log service started", "The machine booted (or the log service restarted).",
                           "Normal at every boot."),
    ("eventlog", 6006): _e("Event log service stopped", "A clean shutdown reached the log service.",
                           "Normal at every clean shutdown; its ABSENCE before a 6005 is the sign of a crash."),
    ("eventlog", 1102): _e("Audit log was cleared", "Someone cleared the Security log.",
                           "An administrator, a script, or an attacker covering tracks.",
                           "Establish who and why; this is a security-relevant event."),
    ("microsoft-windows-wer-systemerrorreporting", 1001): _e(
        "Bugcheck (blue screen) recorded",
        "The machine bugchecked and rebooted; this event carries the bugcheck code.",
        "See the bugcheck code in the message; the Crash Dumps tab explains it.",
        "Open the Crash Dumps tab, or analyse the dump named in the message."),
    ("bugcheck", 1001): _e(
        "Bugcheck (blue screen) recorded",
        "The machine bugchecked and rebooted; this event carries the bugcheck code.",
        "See the bugcheck code in the message; the Crash Dumps tab explains it.",
        "Open the Crash Dumps tab, or analyse the dump named in the message."),
    ("service control manager", 7031): _e(
        "Service crashed, recovery action taken",
        "A service terminated unexpectedly and its recovery action ran.",
        "The service process crashed or was killed; usually a bug in that service or a "
        "dependency, sometimes memory pressure or a security product killing it.",
        "Check Application log 1000 (Application Error) at the same time for the faulting module."),
    ("service control manager", 7034): _e(
        "Service crashed, no recovery",
        "A service terminated unexpectedly and nothing restarted it.",
        "Same causes as 7031 with no recovery action configured.",
        "Restart the service, then look for a 1000 event naming the faulting module."),
    ("service control manager", 7000): _e(
        "Service failed to start", "A service did not start because of a logon or start error.",
        "Missing or broken binary, bad logon account, or a driver-level failure.",
        "Read the error text in the message; check the service's binary path and account."),
    ("service control manager", 7001): _e(
        "Service failed: dependency failed", "A service was not started because a service it depends on failed.",
        "The dependency (named in the message) failed first.", "Fix the dependency named in the message."),
    ("service control manager", 7009): _e(
        "Service start timeout", "A service did not respond to start within the timeout (30 s by default).",
        "Slow disk at boot, a hung dependency, or an antivirus scan of the binary.",
        "Look for 7000/7011 for the same service; check boot-time disk load."),
    ("service control manager", 7011): _e(
        "Service transaction timeout", "A service did not answer a control request in time.",
        "The service is hung or overloaded.", "Check the service's CPU/handle use."),
    ("service control manager", 7023): _e(
        "Service terminated with an error", "A service stopped and reported an error code.",
        "The service hit a fatal error; the code is in the message.", "Decode the error code."),
    ("service control manager", 7026): _e(
        "Boot-start driver failed to load", "A boot-start driver could not be loaded.",
        "Missing, corrupt or blocked driver binary (Secure Boot, HVCI, or a bad update).",
        "Identify the driver named in the message; reinstall or disable it."),
    ("disk", 7): _e(
        "Bad block on disk", "The device reported a bad block.",
        "A failing disk or a bad cable/controller. This is one of the strongest early "
        "failure signals.", "Check SMART, back up now, swap the cable, then the disk."),
    ("disk", 11): _e(
        "Disk controller error", "The driver detected a controller error on a device.",
        "Bad SATA/NVMe cable or port, failing disk, or a controller/driver fault.",
        "Reseat or replace the cable, update the storage driver, check SMART."),
    ("disk", 15): _e(
        "Device not ready", "The device is not ready for access yet.",
        "A disk that dropped off the bus or is spinning up; can precede a failure.",
        "Check power and cabling; look for 153 and 51 nearby."),
    ("disk", 51): _e(
        "Paging error on disk", "An error was detected on a device during a paging operation.",
        "A failing disk, a bad cable, or a dying USB/external enclosure.",
        "Check SMART; if it is an external drive, test another cable/port."),
    ("disk", 52): _e(
        "SMART predicts failure", "The drive reported that failure is imminent.",
        "SMART thresholds exceeded.", "Back up immediately and replace the drive."),
    ("disk", 153): _e(
        "I/O operation retried", "A disk I/O failed and Windows retried it.",
        "Transient controller or cable trouble, a drive that stalls, or a marginal device.",
        "Frequent 153s point at the cable, port, controller or the drive itself."),
    ("ntfs", 55): _e(
        "File system structure corrupt", "NTFS found corruption on a volume.",
        "Unclean shutdown, failing disk, or a driver that wrote bad data.",
        "Run chkdsk on the volume; check the disk for 7/11/51/153 too."),
    ("ntfs", 98): _e(
        "Volume health check", "NTFS performed an online health check.",
        "Informational unless it reports problems.", ""),
    ("ntfs", 137): _e(
        "NTFS could not write metadata", "The NTFS transaction log could not be written.",
        "Disk or controller trouble.", "Check the disk and its cabling."),
    ("microsoft-windows-whea-logger", 17): _e(
        "Corrected hardware error (PCIe)", "A PCI Express device reported a corrected error.",
        "Marginal PCIe link: riser, slot, GPU/NVMe seating, or an overclock/undervolt.",
        "Reseat the device; check BIOS PCIe settings; note which device is named."),
    ("microsoft-windows-whea-logger", 18): _e(
        "Fatal machine check", "The CPU reported a fatal machine check exception.",
        "Unstable CPU (overclock, undervolt, thermal) or failing hardware.",
        "Return to stock clocks and voltages, check cooling, run a memory/CPU stress test."),
    ("microsoft-windows-whea-logger", 19): _e(
        "Corrected hardware error (machine check)", "The CPU corrected a hardware error.",
        "Marginal CPU/cache/memory; often early-warning for instability.",
        "Check for a pattern by APIC ID; reduce overclock or raise voltage margins."),
    ("microsoft-windows-whea-logger", 20): _e(
        "Corrected hardware error (PCIe)", "A PCI Express device reported an error.",
        "Marginal PCIe link or device.", "Reseat, update firmware, check BIOS PCIe settings."),
    ("microsoft-windows-whea-logger", 47): _e(
        "Corrected hardware error", "A corrected error was reported (often memory or cache).",
        "ECC-corrected memory error, marginal RAM or memory overclock/EXPO/XMP settings.",
        "Test memory at rated speed; try lower memory speed."),
    ("application error", 1000): _e(
        "Application crash", "A program crashed; the message names the faulting module and exception code.",
        "A bug in the application or one of its DLLs; 0xc0000005 is an access violation, "
        "0xc0000409 a stack-buffer/fail-fast check, 0xc000041d a fatal unhandled error.",
        "If the faulting module is a third-party DLL (overlay, hook, shell extension), update or remove it."),
    ("application hang", 1002): _e(
        "Application hang", "A program stopped responding and was terminated.",
        "A UI thread blocked on I/O, a lock, or a slow network/disk call.",
        "Check what the app was waiting on; look for disk/network events at that time."),
    (".net runtime", 1026): _e(
        ".NET unhandled exception", "A .NET application crashed with an unhandled exception.",
        "The exception text and stack are in the message.", "Read the exception type; fix or update the app."),
    ("windows error reporting", 1001): _e(
        "Windows Error Reporting record", "WER recorded a crash, hang or install failure with a bucket ID.",
        "The message names the event type and the faulting module.", ""),
    ("microsoft-windows-kernel-pnp", 219): _e(
        "Driver failed to load for a device", "PnP could not load a driver for a device.",
        "Missing/incompatible/blocked driver.", "Note the device in the message; reinstall its driver."),
    ("microsoft-windows-kernel-pnp", 411): _e(
        "Device problem reported", "PnP started a device with a problem code.",
        "See the problem status in the message.", ""),
    ("display", 4101): _e(
        "Display driver stopped responding and recovered (TDR)", "The GPU driver hung and Windows reset it.",
        "GPU hang: unstable GPU clock/undervolt, overheating, a driver bug, or a failing card/PCIe link.",
        "Look for AMD/NVIDIA watchdog events and LiveKernelReports (Crash Dumps tab) at the same time."),
    ("microsoft-windows-time-service", 36): _e(
        "Time not synchronised", "The time service has not synchronised for a while.",
        "Time source unreachable (firewall, no network, bad peer list).", "Check `w32tm /query /status`."),
    ("microsoft-windows-time-service", 134): _e(
        "Time service: manual peer DNS failure", "The time service could not resolve its configured NTP peer.",
        "DNS failure or a mistyped peer name.", "Check `w32tm /query /peers`."),
    ("microsoft-windows-time-service", 129): _e(
        "Time service: domain peer discovery error", "The time service could not find a domain peer.",
        "Domain controller unreachable or DNS trouble.", ""),
    ("microsoft-windows-time-service", 47): _e(
        "Time service: no valid response from peer", "No valid response was received from the configured NTP peer.",
        "Firewall blocking UDP 123, or the peer is down.", ""),
    ("microsoft-windows-security-auditing", 4625): _e(
        "Failed logon", "An account failed to log on.",
        "Wrong password, expired/locked account, a service with stale credentials, or a "
        "brute-force attempt. The logon type and source address are in the message.",
        "Group by account and source address; many from one address is an attack pattern."),
    ("microsoft-windows-security-auditing", 4740): _e(
        "Account locked out", "An account was locked out after too many failed logons.",
        "Stale saved credentials on some device or an attack.", "Find the caller computer named in the event."),
    ("microsoft-windows-distributedcom", 10016): _e(
        "DCOM permission error", "A COM server was denied permission for an activation.",
        "Very common and almost always harmless: default permissions do not grant a "
        "component activation to a user.", "Ignore unless a specific feature is broken."),
    ("dcom", 10016): _e(
        "DCOM permission error", "A COM server was denied permission for an activation.",
        "Very common and almost always harmless.", "Ignore unless a specific feature is broken."),
    ("schannel", 36887): _e(
        "TLS fatal alert received", "The remote side sent a TLS fatal alert.",
        "Cipher/protocol mismatch or a certificate problem.", ""),
    ("microsoft-windows-groupolicy", 1129): _e(
        "Group Policy could not be processed", "Group Policy processing failed because the network was not available.",
        "The machine had no network path to the domain at policy time.", "Check DC reachability and DNS."),
    ("microsoft-windows-windowsupdateclient", 20): _e(
        "Windows Update install failure", "An update failed to install.",
        "The error code is in the message; decode it in the Windows Update tab.", ""),
    ("microsoft-windows-kernel-general", 12): _e("OS started", "Windows started.", "Normal at every boot."),
    ("microsoft-windows-kernel-general", 13): _e("OS shutting down", "Windows is shutting down.", "Normal."),
    ("volmgr", 46): _e(
        "Crash dump could not be created", "The dump stack could not be initialised.",
        "No page file on the boot volume, or too small.", "Configure a page file on the system drive."),
    ("microsoft-windows-resource-exhaustion-detector", 2004): _e(
        "Low virtual memory", "Windows diagnosed low virtual memory and named the biggest consumers.",
        "A leaking process or too small a page file.", "The message lists the top processes."),
    ("srv", 2017): _e(
        "Server could not allocate from pool", "The server service could not allocate resources.",
        "Memory pressure.", ""),
}


def lookup_event(provider: str, event_id: int) -> Optional[EventInfo]:
    """The table entry for `(provider, event_id)`, or None when nothing is known."""
    prov = (provider or "").strip().lower()
    if not prov:
        return None
    direct = _EVENTS.get((prov, int(event_id)))
    if direct is not None:
        return direct
    for (key, eid), info in _EVENTS.items():
        if eid == int(event_id) and (prov.endswith(key) or prov.startswith(key)):
            return info
    return None


def _b(code, name, cause, params=("", "", "", "")) -> BugcheckInfo:
    return BugcheckInfo(code, name, cause, params)


_BUGCHECKS: Dict[int, BugcheckInfo] = {b.code: b for b in (
    _b(0x0A, "IRQL_NOT_LESS_OR_EQUAL", "A driver touched pageable memory at too high an IRQL. Usually a driver bug or bad RAM.",
       ("Address referenced", "IRQL at the time", "0 read / 1 write", "Address of the instruction")),
    _b(0x1A, "MEMORY_MANAGEMENT", "A severe memory-management error. Bad RAM is the classic cause; also a failing disk under paging.",
       ("Sub-code", "", "", "")),
    _b(0x1E, "KMODE_EXCEPTION_NOT_HANDLED", "A kernel-mode program raised an exception nothing handled. Driver bug or hardware.",
       ("Exception code", "Address of the exception", "", "")),
    _b(0x24, "NTFS_FILE_SYSTEM", "NTFS hit an unrecoverable problem. Disk corruption or a failing disk.", ()),
    _b(0x3B, "SYSTEM_SERVICE_EXCEPTION", "An exception while executing a system service routine. Often a graphics or antivirus driver.",
       ("Exception code", "Address of the failing instruction", "", "")),
    _b(0x3F, "NO_MORE_SYSTEM_PTES", "The system ran out of page-table entries. A driver leak.", ()),
    _b(0x44, "MULTIPLE_IRP_COMPLETE_REQUESTS", "A driver completed the same I/O request twice.", ()),
    _b(0x4E, "PFN_LIST_CORRUPT", "The page-frame list is corrupt. Bad RAM or a misbehaving driver.", ()),
    _b(0x50, "PAGE_FAULT_IN_NONPAGED_AREA", "Invalid memory was referenced. Bad RAM, a faulty driver or a corrupt file system.",
       ("Address referenced", "0 read / 1 write", "Address of the instruction", "")),
    _b(0x51, "REGISTRY_ERROR", "A severe registry I/O problem. Disk trouble or a corrupt hive.", ()),
    _b(0x77, "KERNEL_STACK_INPAGE_ERROR", "A kernel stack page could not be read from disk. Disk or cable failure, or bad RAM.", ()),
    _b(0x7A, "KERNEL_DATA_INPAGE_ERROR", "Requested kernel data could not be read from the page file. Disk/cable/controller failure; also bad RAM.",
       ("Lock type", "I/O status (error code)", "", "")),
    _b(0x7B, "INACCESSIBLE_BOOT_DEVICE", "Windows lost access to the boot volume. Storage controller mode change (AHCI/RAID/VMD), missing driver or damaged boot volume.", ()),
    _b(0x7E, "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "A system thread raised an unhandled exception. Driver bug.",
       ("Exception code", "Address of the exception", "", "")),
    _b(0x7F, "UNEXPECTED_KERNEL_MODE_TRAP", "The CPU raised a trap the kernel does not allow. Hardware (RAM/CPU/overclock) most often.", ()),
    _b(0x9C, "MACHINE_CHECK_EXCEPTION", "The CPU reported a fatal hardware error. Unstable CPU, overclock, thermal or failing hardware.", ()),
    _b(0x9F, "DRIVER_POWER_STATE_FAILURE", "A driver did not complete a power transition in time. Frequent around sleep/resume; often USB, storage or GPU drivers.",
       ("1 = device object stuck in a power IRP", "", "", "")),
    _b(0xA0, "INTERNAL_POWER_ERROR", "The power policy manager hit a fatal error.", ()),
    _b(0xBE, "ATTEMPTED_WRITE_TO_READONLY_MEMORY", "A driver wrote to read-only memory. Driver bug.", ()),
    _b(0xC1, "SPECIAL_POOL_DETECTED_MEMORY_CORRUPTION", "A driver corrupted pool memory (caught by special pool).", ()),
    _b(0xC2, "BAD_POOL_CALLER", "A thread made a bad pool request. Driver bug.", ()),
    _b(0xC4, "DRIVER_VERIFIER_DETECTED_VIOLATION", "Driver Verifier caught a driver violating a rule. Verifier is turned on.", ()),
    _b(0xC5, "DRIVER_CORRUPTED_EXPOOL", "A driver touched the pool at too high an IRQL or corrupted it.", ()),
    _b(0xCE, "DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS", "A driver unloaded with work still pending.", ()),
    _b(0xD1, "DRIVER_IRQL_NOT_LESS_OR_EQUAL", "A driver accessed pageable memory at too high an IRQL. The most common driver bugcheck; often a network or storage driver.",
       ("Address referenced", "IRQL at the time", "0 read / 1 write / 8 execute", "Address of the instruction")),
    _b(0xD5, "DRIVER_PAGE_FAULT_IN_FREED_SPECIAL_POOL", "A driver used memory after freeing it.", ()),
    _b(0xDA, "SYSTEM_PTE_MISUSE", "A page-table-entry routine was misused. Driver bug.", ()),
    _b(0xE2, "MANUALLY_INITIATED_CRASH", "The crash was requested by keyboard (Ctrl+Scroll Lock) or a tool: deliberate.", ()),
    _b(0xEA, "THREAD_STUCK_IN_DEVICE_DRIVER", "A driver spent too long in a loop, usually waiting on hardware. Classic for GPU driver hangs.", ()),
    _b(0xEF, "CRITICAL_PROCESS_DIED", "A critical system process (csrss, wininit, smss) ended. Corruption, a failing disk or a bad driver.",
       ("Process object", "0 = process, 1 = thread", "", "")),
    _b(0xF4, "CRITICAL_OBJECT_TERMINATION", "A process or thread the system needs was terminated. Disk failure or corruption.", ()),
    _b(0xFC, "ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", "Code was executed from a non-executable page. Driver bug or corruption.", ()),
    _b(0xFE, "BUGCODE_USB_DRIVER", "A USB driver or device misbehaved.", ()),
    _b(0x101, "CLOCK_WATCHDOG_TIMEOUT", "A processor did not respond to interrupts. Unstable CPU, undervolt/overclock or firmware.",
       ("Interrupt interval (ticks)", "", "PRCB address", "")),
    _b(0x109, "CRITICAL_STRUCTURE_CORRUPTION", "Kernel code or a critical structure was modified. Bad RAM, a driver, or (rarely) tampering.", ()),
    _b(0x116, "VIDEO_TDR_FAILURE", "The display driver did not recover from a timeout (TDR). GPU hang: clocks, heat, driver or hardware.", ()),
    _b(0x117, "VIDEO_TDR_TIMEOUT_DETECTED", "The display driver timed out and could not reset the GPU.", ()),
    _b(0x119, "VIDEO_SCHEDULER_INTERNAL_ERROR", "The video scheduler found a fatal violation. GPU driver or hardware.", ()),
    _b(0x124, "WHEA_UNCORRECTABLE_ERROR", "A fatal hardware error. CPU, memory, PCIe device or power delivery; overclock/undervolt are common causes.",
       ("Error source: 0 MCE, 1 CMC, 2 CPE, 3 NMI, 4 PCI Express, 5 generic", "Address of the WHEA error record", "", "")),
    _b(0x12B, "FAULTY_HARDWARE_CORRUPTED_PAGE", "A page was corrupted by faulty hardware, almost always RAM.", ()),
    _b(0x133, "DPC_WATCHDOG_VIOLATION", "A deferred procedure call ran too long. Storage (NVMe/SATA) or network driver/firmware.",
       ("0 = single DPC over its limit, 1 = cumulative DPCs over the limit", "Time taken (ticks)", "Limit (ticks)", "")),
    _b(0x139, "KERNEL_SECURITY_CHECK_FAILURE", "A corrupted structure was detected (stack cookie, list corruption). Driver bug or bad RAM.",
       ("Kind of corruption", "", "", "")),
    _b(0x13A, "KERNEL_MODE_HEAP_CORRUPTION", "The kernel heap was corrupted. Driver bug.", ()),
    _b(0x141, "VIDEO_ENGINE_TIMEOUT_DETECTED", "A GPU engine timed out and the driver could not recover it.", ()),
    _b(0x18B, "SECURE_KERNEL_ERROR", "The secure kernel (VBS) hit an error.", ()),
    _b(0x1CA, "SYNTHETIC_WATCHDOG_TIMEOUT", "A watchdog fired while the machine looked hung, often in a VM or with a stuck driver.", ()),
    _b(0xC000021A, "STATUS_SYSTEM_PROCESS_TERMINATED", "A critical user-mode process (Winlogon or CSRSS) ended. Corrupted system files or a bad update.", ()),
)}

#: Windows uses 0xA0000000 | n and 0x1000xxxx style codes for live kernel events
#: and for some wrapped bugchecks; the directory a live dump sits in names it better.
LIVE_KERNEL_PREFIX = 0xA0000000


def bugcheck_info(code: int) -> Optional[BugcheckInfo]:
    """The table entry for a bugcheck code, or None. Never guesses."""
    try:
        return _BUGCHECKS.get(int(code) & 0xFFFFFFFF)
    except (TypeError, ValueError):
        return None


def is_live_kernel_event(code: int) -> bool:
    """True for a live-kernel-event code (0xA1000001 ...): a dump taken WITHOUT crashing."""
    return (int(code) & 0xFF000000) in (0xA0000000, 0xA1000000, 0xA2000000, 0xA3000000)


def bugcheck_label(code: int) -> str:
    """`0x00000133 DPC_WATCHDOG_VIOLATION`, or just the hex when unknown."""
    info = bugcheck_info(code)
    hexcode = f"0x{int(code) & 0xFFFFFFFF:08X}"
    return f"{hexcode} {info.name}" if info else hexcode
