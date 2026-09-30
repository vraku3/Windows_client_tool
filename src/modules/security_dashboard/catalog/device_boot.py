"""Device, firmware and boot-path controls.

Most of this category is read-only, and for a reason that is worth stating
once: **firmware is not a Windows setting.** Secure Boot, the TPM and the
UEFI/legacy mode are read by Windows and changed in the firmware setup screen.
A switch here would be a button that cannot work.

What IS writable: BitLocker, Memory Integrity, test signing, AutoRun, fast
startup, hibernation, SmartScreen and the two event-log sizes.

BitLocker is the highest-risk entry in the whole catalog, and its
`why_it_matters` says so in plain words rather than leaving it to the risk
enum.
"""
from typing import Any, Dict, Tuple

from ..security_reader import (
    check_autorun, check_bios_mode, check_bitlocker, check_bitlocker_encryption,
    check_credential_guard_vbs, check_dump_encryption, check_fast_startup,
    check_hibernation, check_hvci, check_memory_integrity_registry,
    check_ntp_sync, check_pagefile_clear, check_secure_boot_tpm,
    check_security_log_size, check_smartscreen, check_system_log_size,
    check_test_signing, check_tpm_details, check_usb_storage,
    check_wu_auto_update,
)
from .model import Category, Risk, SecurityControl

_MEMORY_MANAGEMENT = (r"HKLM\SYSTEM\CurrentControlSet\Control"
                      r"\Session Manager\Memory Management")
_HVCI_POLICY = (r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard"
                r"\Scenarios\HypervisorEnforcedCodeIntegrity")
_DEVICE_GUARD = r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard"
_EXPLORER_POLICY = (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
                    r"\Policies\Explorer")
_SMARTSCREEN_POLICY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System"
_POWER = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
_USBSTOR = r"HKLM\SYSTEM\CurrentControlSet\Services\USBSTOR"


def _dword(key: str, value: str, data: int) -> Dict[str, Any]:
    return {"type": "registry", "key": key, "value": value, "data": data,
            "kind": "DWORD"}


CONTROLS: Tuple[SecurityControl, ...] = (

    # -- disk encryption ----------------------------------------------------
    SecurityControl(
        id="bitlocker_system_drive",
        title="BitLocker on the system drive",
        category=Category.DEVICE_BOOT,
        description="Full-volume encryption of C:, tied to the TPM.",
        why_it_matters="Without it, anyone who can take the disk out reads "
                       "every file on it, and every password hash. "
                       "**Save the recovery key before turning this on.** If "
                       "the TPM state changes -- a firmware update, a "
                       "motherboard swap, a Secure Boot change -- Windows asks "
                       "for that key at boot, and without it the data is gone. "
                       "This is the one control on this page that can lock you "
                       "out of your own machine.",
        reader=check_bitlocker,
        read_value=lambda d: (None if "Unknown" in str(d.get("status", ""))
                              else "Protected" in str(d.get("status", ""))),
        on_steps=({"type": "command",
                   "cmd": "manage-bde -on C: -RecoveryPassword "
                          "-SkipHardwareTest"},),
        off_steps=({"type": "command", "cmd": "manage-bde -off C:"},),
        desired=True,
        risk=Risk.HIGH,
        requires_reboot=True,
    ),

    SecurityControl(
        id="bitlocker_encryption_detail",
        title="BitLocker encryption method and progress",
        category=Category.DEVICE_BOOT,
        description="Which cipher each volume uses, and whether encryption "
                    "has finished.",
        why_it_matters="A volume that is 'Encryption In Progress' is not yet "
                       "protected, and an older cipher (AES-128 rather than "
                       "XTS-AES-256) is weaker than what this machine can do.",
        reader=check_bitlocker_encryption,
        read_only_reason="The method is chosen when a volume is encrypted; "
                         "changing it means decrypting and re-encrypting the "
                         "whole disk, which is hours of work and not a "
                         "toggle. The BitLocker control above is where it is "
                         "turned on.",
    ),

    # -- VBS ----------------------------------------------------------------
    SecurityControl(
        id="memory_integrity",
        title="Memory Integrity (HVCI)",
        category=Category.DEVICE_BOOT,
        description="Hypervisor-enforced code integrity: drivers are verified "
                    "inside VBS before the kernel will run them.",
        why_it_matters="It is what stops a signed-but-vulnerable driver from "
                       "being loaded and abused, which is how most kernel "
                       "compromises now start. It needs virtualization "
                       "support, and it can block old drivers from loading at "
                       "all -- which is exactly the point, and also how it "
                       "breaks hardware.",
        reader=check_memory_integrity_registry,
        on_steps=(_dword(_DEVICE_GUARD, "EnableVirtualizationBasedSecurity", 1),
                  _dword(_HVCI_POLICY, "Enabled", 1)),
        off_steps=(_dword(_HVCI_POLICY, "Enabled", 0),),
        desired=True,
        risk=Risk.HIGH,
        requires_reboot=True,
    ),

    SecurityControl(
        id="hvci_running",
        title="Memory Integrity, as it is running",
        category=Category.DEVICE_BOOT,
        description="What Windows reports VBS and HVCI are actually doing "
                    "right now, as opposed to what policy asks for.",
        why_it_matters="Policy and reality can differ for a whole boot: "
                       "Memory Integrity switched on takes effect at the next "
                       "restart, and can silently fail to start if a driver "
                       "is incompatible.",
        reader=check_hvci,
        read_only_reason="This is the live state, not a setting. Write the "
                         "policy through the Memory Integrity control above; "
                         "this card is how you find out whether it took.",
    ),

    SecurityControl(
        id="vbs_policy",
        title="Virtualization-Based Security policy",
        category=Category.DEVICE_BOOT,
        description="The two registry values that ask for VBS and require "
                    "platform security features.",
        why_it_matters="VBS is the container Credential Guard and Memory "
                       "Integrity both run inside. Without it neither can "
                       "start.",
        reader=check_credential_guard_vbs,
        read_only_reason="These are the same two values the Credential Guard "
                         "control (Accounts) and the Memory Integrity control "
                         "above already write. A third card writing them "
                         "would let the catalog stage two conflicting changes "
                         "to one value; this one shows the state instead.",
    ),

    # -- boot path ----------------------------------------------------------
    SecurityControl(
        id="test_signing",
        title="Test signing mode",
        category=Category.DEVICE_BOOT,
        description="Whether Windows will load drivers signed with a test "
                    "certificate.",
        why_it_matters="With test signing on, any self-signed driver loads "
                       "into the kernel. It is meant for driver development "
                       "and is a standard step in installing a rootkit.",
        reader=check_test_signing,
        on_steps=({"type": "command", "cmd": "bcdedit /set testsigning on"},),
        off_steps=({"type": "command", "cmd": "bcdedit /set testsigning off"},),
        desired=False,
        risk=Risk.MEDIUM,
        requires_reboot=True,
    ),

    SecurityControl(
        id="fast_startup",
        title="Fast Startup",
        category=Category.DEVICE_BOOT,
        description="Shutdown hibernates the kernel session instead of ending "
                    "it, so the next boot is quicker.",
        why_it_matters="A machine with Fast Startup on never really shuts "
                       "down: the kernel session, and anything resident in "
                       "it, comes back. It also leaves the hibernation file "
                       "holding memory contents on an unencrypted disk.",
        reader=check_fast_startup,
        on_steps=(_dword(_POWER, "HiberbootEnabled", 1),),
        off_steps=(_dword(_POWER, "HiberbootEnabled", 0),),
        desired=False,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="hibernation",
        title="Hibernation",
        category=Category.DEVICE_BOOT,
        description="Whether hiberfil.sys exists and hibernation is available.",
        why_it_matters="hiberfil.sys is a copy of physical memory sitting on "
                       "disk, including keys and open documents. On an "
                       "encrypted volume that is fine; on an unencrypted one "
                       "it is the whole of RAM, readable offline. It is also "
                       "how laptops resume, so the catalog has no opinion.",
        reader=check_hibernation,
        on_steps=({"type": "command", "cmd": "powercfg /hibernate on"},),
        off_steps=({"type": "command", "cmd": "powercfg /hibernate off"},),
        desired=None,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="autorun",
        title="AutoRun / AutoPlay",
        category=Category.DEVICE_BOOT,
        description="Whether Windows runs or offers to run content from a "
                    "drive as soon as it is attached.",
        why_it_matters="AutoRun from removable media is how Stuxnet and "
                       "Conficker spread, and it still turns 'plugging in a "
                       "USB stick' into 'running whatever is on it'.",
        reader=check_autorun,
        on_steps=(_dword(_EXPLORER_POLICY, "NoDriveTypeAutoRun", 0),),
        off_steps=(_dword(_EXPLORER_POLICY, "NoDriveTypeAutoRun", 255),),
        desired=False,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="usb_storage",
        title="USB Mass Storage",
        category=Category.DEVICE_BOOT,
        description="Whether Windows will mount a USB drive at all, via the "
                    "USBSTOR driver's Start value.",
        why_it_matters="This is the standard control against walking data "
                       "out on a USB stick or plugging in an unknown one "
                       "(BadUSB) -- distinct from AutoRun above, which only "
                       "stops a drive Windows already mounted from running "
                       "something automatically. Blocking it also stops "
                       "every legitimate USB drive, camera SD reader and "
                       "phone-as-storage from mounting.",
        reader=check_usb_storage,
        on_steps=(_dword(_USBSTOR, "Start", 3),),
        off_steps=(_dword(_USBSTOR, "Start", 4),),
        desired=False,
        risk=Risk.MEDIUM,
        requires_reboot=True,
    ),

    SecurityControl(
        id="pagefile_clear",
        title="Clear the page file at shutdown",
        category=Category.DEVICE_BOOT,
        description="Overwrites pagefile.sys during shutdown.",
        why_it_matters="The page file holds whatever was paged out of memory, "
                       "keys included, and it survives a shutdown. Clearing "
                       "it removes that; it also adds minutes to every "
                       "shutdown on a large page file, which is why the "
                       "catalog has no opinion.",
        reader=check_pagefile_clear,
        on_steps=(_dword(_MEMORY_MANAGEMENT, "ClearPageFileAtShutdown", 1),),
        off_steps=(_dword(_MEMORY_MANAGEMENT, "ClearPageFileAtShutdown", 0),),
        desired=None,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="smartscreen",
        title="SmartScreen enforced by policy",
        category=Category.DEVICE_BOOT,
        description="Whether SmartScreen -- Microsoft's reputation check on "
                    "downloaded programs and visited sites -- is pinned on by "
                    "policy. \"Not configured\" means the policy is absent, "
                    "not that SmartScreen is off: Windows has its own default, "
                    "which this control neither reads nor writes.",
        why_it_matters="It is the warning that appears before somebody runs "
                       "the installer they were emailed. Set by policy, it "
                       "cannot be turned off in Settings.",
        reader=check_smartscreen,
        # "Not Configured" is the policy value being absent, which for a
        # control whose steps write exactly that value is False. Without this
        # the reader returns no `enabled` on that path, read() is None,
        # staging records no prior value, and the verify pass compares None
        # against True -- reporting APPLIED_UNVERIFIED however well the write
        # landed. A writable control that could never be verified.
        read_value=lambda d: (d["enabled"] if "enabled" in d
                              else d.get("status") == "Enabled"),
        on_steps=(_dword(_SMARTSCREEN_POLICY, "EnableSmartScreen", 1),),
        off_steps=(_dword(_SMARTSCREEN_POLICY, "EnableSmartScreen", 0),),
        desired=True,
        risk=Risk.LOW,
    ),

    # -- the record, if something happens -----------------------------------
    SecurityControl(
        id="security_log_size",
        title="Security event log size",
        category=Category.DEVICE_BOOT,
        description="How much the Security log holds before it starts "
                    "overwriting.",
        why_it_matters="The default is small enough that a busy machine "
                       "overwrites yesterday. Whatever an intrusion leaves "
                       "behind is in here, and only for as long as it fits. "
                       "How big is a retention question, so the catalog "
                       "suggests rather than insists.",
        reader=check_security_log_size,
        read_value=lambda d: d.get("megabytes"),
        on_steps=({"type": "command",
                   "cmd": "wevtutil sl Security /ms:201326592"},),
        off_steps=({"type": "command",
                    "cmd": "wevtutil sl Security /ms:20971520"},),
        desired=None,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="system_log_size",
        title="System event log size",
        category=Category.DEVICE_BOOT,
        description="How much the System log holds before it starts "
                    "overwriting.",
        why_it_matters="Driver loads, service failures and boot problems land "
                       "here, and they are what a compromise of the boot path "
                       "looks like from the outside.",
        reader=check_system_log_size,
        read_value=lambda d: d.get("megabytes"),
        on_steps=({"type": "command",
                   "cmd": "wevtutil sl System /ms:67108864"},),
        off_steps=({"type": "command",
                    "cmd": "wevtutil sl System /ms:20971520"},),
        desired=None,
        risk=Risk.LOW,
    ),

    # -- firmware: read, never written --------------------------------------
    SecurityControl(
        id="tpm_present",
        title="TPM",
        category=Category.DEVICE_BOOT,
        description="Trusted Platform Module presence, version and readiness.",
        why_it_matters="BitLocker, Credential Guard and Windows Hello all rest "
                       "on it. Without one they fall back to weaker "
                       "protection.",
        reader=check_tpm_details,
        read_only_reason="A TPM is hardware, enabled in firmware. Nothing "
                         "Windows can write turns one on.",
    ),

    SecurityControl(
        id="secure_boot",
        title="Secure Boot and TPM readiness",
        category=Category.DEVICE_BOOT,
        description="Whether the firmware refuses to load a bootloader it "
                    "cannot verify, and whether the TPM is enabled.",
        why_it_matters="Without Secure Boot a bootkit loads before Windows, "
                       "and before any protection Windows could offer.",
        reader=check_secure_boot_tpm,
        read_only_reason="Secure Boot is a UEFI firmware setting. Windows can "
                         "read it and cannot change it; use the firmware "
                         "setup screen.",
    ),

    SecurityControl(
        id="bios_mode",
        title="Firmware mode (UEFI or legacy BIOS)",
        category=Category.DEVICE_BOOT,
        description="Which firmware interface this installation boots through.",
        why_it_matters="Secure Boot, and therefore the whole verified boot "
                       "chain, exists only under UEFI. A legacy-BIOS "
                       "installation cannot have it at all.",
        reader=check_bios_mode,
        read_only_reason="Converting a legacy installation to UEFI means "
                         "converting the disk from MBR to GPT (mbr2gpt) and "
                         "changing firmware settings. It is a migration, not "
                         "a switch.",
    ),

    SecurityControl(
        id="crash_dump_encryption",
        read_value=lambda d: d.get("status"),
        title="Crash dump encryption",
        category=Category.DEVICE_BOOT,
        description="Whether kernel crash dumps are written encrypted.",
        why_it_matters="A kernel dump is a copy of memory, and it lands in "
                       "C:\\Windows\\MEMORY.DMP where anything that can read "
                       "the disk can read it.",
        reader=check_dump_encryption,
        read_only_reason="This is derived from BitLocker's state rather than "
                         "being a switch of its own -- Windows encrypts dumps "
                         "when the volume is encrypted. Turn on BitLocker "
                         "instead.",
    ),

    SecurityControl(
        id="ntp_sync",
        read_value=lambda d: d.get("status"),
        title="Time synchronisation",
        category=Category.DEVICE_BOOT,
        description="Whether the clock is synchronised, and against what.",
        why_it_matters="Kerberos refuses tickets more than five minutes out, "
                       "certificate validity is a time window, and every log "
                       "entry is only as useful as the clock that stamped it.",
        reader=check_ntp_sync,
        read_only_reason="`w32tm /resync` is an action rather than a state, "
                         "and which time source to trust is a configuration "
                         "question with no safe default this tool can pick.",
    ),

    SecurityControl(
        id="windows_update_policy",
        read_value=lambda d: d.get("status"),
        title="Windows Update automatic install policy",
        category=Category.DEVICE_BOOT,
        description="The AUOptions policy value, when one is set.",
        why_it_matters="Almost every mitigation on these tabs exists because "
                       "a patch was not applied. An unset policy is the "
                       "SECURE state here: Windows installs updates "
                       "automatically on its own.",
        reader=check_wu_auto_update,
        read_only_reason="This policy exists to RESTRICT updating -- to defer, "
                         "to notify-only, to hand control to a management "
                         "server. There is no value that makes Windows update "
                         "more eagerly than its own default, so a switch here "
                         "could only make things worse. Windows Update's own "
                         "settings page is the right place.",
    ),
)
