"""
Security status data-gathering and action functions.

Check functions return a dict with: status (str), color ("green"|"amber"|"red"),
details (list of (key, value) tuples), and optionally enabled (bool).
Toggle functions return a dict with: success (bool), message (str), and
optionally before_value and after_value for revert tracking.
"""

import json
import logging
import os
import subprocess
import sys
import winreg
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from core.admin_utils import is_admin  # noqa: E402

CREATION_FLAGS = 0x08000000  # CREATE_NO_WINDOW


class _RefusalWeAlreadyKnew(Exception):
    """Raised instead of spending five seconds to be told what we know.

    A process cannot gain elevation while it runs, so an unelevated process
    asking a namespace that only answers administrators has a certain answer
    before it asks.
    """


#: Namespaces measured at a FIXED ~5s to refuse an ordinary user (2026-08-24,
#: three attempts each; 2026-08-29, again, per tab open). Only these two --
#: `root\Microsoft\Windows\Defender` answers unelevated and is not in here.
_ELEVATION_ONLY_NAMESPACES = frozenset({
    r"root\cimv2\Security\MicrosoftVolumeEncryption",
    r"root\cimv2\Security\MicrosoftTpm",
})


# ── Helpers ────────────────────────────────────────────────────────────────

def _ps(cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a PowerShell command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
        creationflags=CREATION_FLAGS,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# Imported here, after _ps is defined, so the circular import resolves either
# way round: snapshots.py does `from .security_reader import _ps`, and by the
# time either module needs the other's contents, both have finished loading.
from . import snapshots  # noqa: E402

NEEDS_ADMIN = "Requires administrator"

#: WBEM_E_ACCESS_DENIED, as a signed HRESULT — what every privileged WMI
#: namespace answers to an ordinary user.
_WBEM_E_ACCESS_DENIED = -2147217405


#: Namespaces that have already answered access-denied in this process, and
#: the exception they answered with. Elevation cannot change while a process
#: runs, so one refusal settles it for the session.
_denied_namespaces: Dict[str, Exception] = {}
_denied_lock = Lock()


def _wmi_namespace(namespace: str):
    """Connect to a WMI namespace, remembering a refusal rather than repaying it.

    Being told "no" by these security namespaces costs a FIXED ~5 seconds --
    measured six times unelevated on 2026-08-24, 5.00-5.02s every attempt, all
    of them failures. It is the denial that is expensive, not the connect. The
    Overview dialled two of them on every refresh, so ten of its 12.4 seconds
    went on being refused twice, and the 30-second auto-refresh did it again
    for as long as the tab stayed open.

    Only an access-denied answer is remembered. A transient failure -- an
    unavailable RPC server, a service still starting -- must be retried, so it
    is re-raised without being latched.
    """
    with _denied_lock:
        remembered = _denied_namespaces.get(namespace)
    if remembered is not None:
        raise remembered

    if namespace in _ELEVATION_ONLY_NAMESPACES and not is_admin():
        # The 5 seconds buys nothing: this namespace does not answer ordinary
        # users, and elevation cannot arrive mid-process. Opening Device &
        # Boot cost 16.79s against 1.9s elevated, almost all of it here.
        raise _RefusalWeAlreadyKnew(namespace)

    import wmi

    try:
        return wmi.WMI(namespace=namespace)
    except Exception as exc:
        denied = isinstance(exc, wmi.x_access_denied) or getattr(
            getattr(exc, "com_error", None), "hresult", None
        ) == _WBEM_E_ACCESS_DENIED
        if denied:
            with _denied_lock:
                _denied_namespaces[namespace] = exc
            logger.debug("%s refused; not asking again this session", namespace)
        raise


def _wmi_failure_reason(exc: Exception) -> str:
    """Say what a failed WMI query means, not what its COM tuple looks like.

    `str(x_wmi)` is `<x_wmi: Unexpected COM Error (-2147217405, 'OLE error
    0x80041003', None, None)>`, which tells a user nothing and reads as a
    crash. The wmi package has already classified it — access denied gets its
    own subclass — so the answer is there to be used rather than stringified.
    """
    if isinstance(exc, _RefusalWeAlreadyKnew):
        return NEEDS_ADMIN
    try:
        import wmi

        if isinstance(exc, wmi.x_access_denied):
            return NEEDS_ADMIN
    except ImportError:
        logger.debug("_wmi_failure_reason: giving up on this read", exc_info=True)
        pass
    com_error = getattr(exc, "com_error", None)
    if getattr(com_error, "hresult", None) == _WBEM_E_ACCESS_DENIED:
        return NEEDS_ADMIN
    return str(exc)


def _secure_boot_from_registry() -> Optional[bool]:
    """Read Secure Boot state without elevation.

    `Confirm-SecureBootUEFI` needs administrator rights, but the value it
    reports is mirrored under `SecureBoot\\State`, which any user can read.
    The key exists only on UEFI firmware, so its absence — and only its
    absence — is what legitimately means "BIOS/Legacy".

    Returns True/False for enabled/disabled, or None when there is no key.
    """
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SecureBoot\State",
        ) as key:
            return bool(winreg.QueryValueEx(key, "UEFISecureBootEnabled")[0])
    except OSError:
        return None


_HIVES = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER,
    "HKCR": winreg.HKEY_CLASSES_ROOT, "HKU": winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}


def _reg_read(key: str, value: str, kind: str = "REG_DWORD") -> Optional[Any]:
    """Read a registry value. Returns None if key or value is absent.

    Was `reg query` in a subprocess at 21.1 ms a call, measured; winreg reads
    the same value in 0.015 ms. With ~150 controls to read that is the
    difference between a responsive pane and the Overview defect again.
    """
    hive_name, _, sub = key.partition("\\")
    hive = _HIVES.get(hive_name.upper())
    if hive is None:
        return None
    try:
        with winreg.OpenKey(hive, sub) as handle:
            raw, _ = winreg.QueryValueEx(handle, value)
    except OSError:
        return None
    if kind == "REG_DWORD" and isinstance(raw, str):
        try:
            return int(raw, 16) if raw.startswith("0x") else int(raw)
        except ValueError:
            return raw
    return raw


def _reg_write(key: str, value: str, data: Any, kind: str = "REG_DWORD") -> bool:
    """Write a registry value via reg add. Returns True on success."""
    try:
        cmd = ["reg", "add", key, "/v", value, "/t", kind, "/d", str(data), "/f"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=10,
            creationflags=CREATION_FLAGS,
        )
        return result.returncode == 0
    except Exception:
        return False


def _resolve_mpcmdrun() -> str:
    """Resolve MpCmdRun.exe path using environment variables."""
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.path.join(prog, "Windows Defender", "MpCmdRun.exe")


def _cmd_run(cmd: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    """Run an arbitrary command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=CREATION_FLAGS,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ── Read-Only Status Checks ────────────────────────────────────────────────

def check_defender() -> Dict[str, Any]:
    try:
        c = _wmi_namespace(r"root\Microsoft\Windows\Defender")
        status_obj = c.MSFT_MpComputerStatus()[0]
        av_enabled = bool(status_obj.AntivirusEnabled)
        rt_enabled = bool(status_obj.RealTimeProtectionEnabled)
        version = str(status_obj.AMProductVersion or "")
        all_ok = av_enabled and rt_enabled
        return {
            "enabled": av_enabled, "real_time": rt_enabled,
            "version": version,
            "status": "Protected" if all_ok else ("Partial" if av_enabled else "Disabled"),
            "color": "green" if all_ok else ("amber" if av_enabled else "red"),
            "details": [
                ("AV Enabled", "Yes" if av_enabled else "No"),
                ("Real-Time Protection", "On" if rt_enabled else "Off"),
                ("Product Version", version),
            ]
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_firewall() -> Dict[str, Any]:
    try:
        import win32com.client
        fw = win32com.client.Dispatch("HNetCfg.FwPolicy2")
        profile_names = {1: "Domain", 2: "Private", 4: "Public"}
        enabled = {}
        for profile_type, name in profile_names.items():
            try:
                enabled[name] = bool(fw.FirewallEnabled(profile_type))
            except Exception:
                enabled[name] = False
        all_on = all(enabled.values())
        any_on = any(enabled.values())
        return {
            "profiles": enabled,
            "status": "All On" if all_on else ("Partial" if any_on else "All Off"),
            "color": "green" if all_on else ("amber" if any_on else "red"),
            "details": [(k, "On" if v else "Off") for k, v in enabled.items()]
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_bitlocker() -> Dict[str, Any]:
    try:
        c = _wmi_namespace(r"root\cimv2\Security\MicrosoftVolumeEncryption")
        volumes = c.Win32_EncryptableVolume()
        details = []
        c_protected = None
        for vol in volumes:
            ps = int(vol.ProtectionStatus or 0)
            label = {0: "Unprotected", 1: "Protected", 2: "Unknown"}.get(ps, str(ps))
            drive = str(vol.DriveLetter or "?")
            details.append((drive, label))
            if drive.upper().startswith("C"):
                c_protected = (ps == 1)
        if c_protected is True:
            return {"status": "C: Protected", "color": "green", "details": details}
        elif c_protected is False:
            return {"status": "C: Unprotected", "color": "red", "details": details}
        return {"status": "Unknown", "color": "amber", "details": details}
    except Exception as e:
        # `available: False` is what stops read() falling through to
        # read_value, whose `"Protected" in status` test answered False --
        # "your system drive is NOT encrypted" -- for a reading nobody was
        # allowed to take. It is the only control of the 149 that did this.
        return {"status": _wmi_failure_reason(e), "color": "amber",
                "available": False, "details": []}


def check_secure_boot_tpm() -> Dict[str, Any]:
    details = []
    # Secure Boot
    sb_ok = None
    try:
        rc, out, err = _ps("Confirm-SecureBootUEFI", timeout=15)
        if "not supported" in (err or "").lower():
            details.append(("Secure Boot", "N/A (BIOS/Legacy)"))
        elif rc != 0:
            # The cmdlet needs administrator rights ("Unable to set proper
            # privileges. Access was denied."). A refusal to answer is not
            # evidence of legacy firmware — the registry knows, unelevated.
            # Only the cmdlet's own "not supported" (above) is evidence of
            # legacy firmware. Denied here plus no key leaves us genuinely
            # unable to tell, which is what we then say.
            from_registry = _secure_boot_from_registry()
            if from_registry is None:
                details.append(("Secure Boot", f"Unknown — {NEEDS_ADMIN}"))
            else:
                details.append(
                    ("Secure Boot", "Enabled" if from_registry else "Disabled")
                )
                sb_ok = from_registry
        elif out.lower() == "true":
            details.append(("Secure Boot", "Enabled"))
            sb_ok = True
        else:
            details.append(("Secure Boot", "Disabled"))
            sb_ok = False
    except Exception as e:
        details.append(("Secure Boot", f"Error: {e}"))

    # TPM
    tpm_ok: Optional[bool] = None
    try:
        c = _wmi_namespace(r"root\cimv2\Security\MicrosoftTpm")
        tpms = c.Win32_Tpm()
        if tpms:
            tpm = tpms[0]
            tpm_enabled = bool(tpm.IsEnabled_InitialValue)
            details.append(("TPM", "Enabled" if tpm_enabled else "Disabled"))
            tpm_ok = tpm_enabled
            # Try to get spec version
            try:
                spec_ver = str(tpm.SpecVersion or "")
                if spec_ver:
                    details.append(("TPM Version", spec_ver))
            except Exception:
                logger.warning("Ignored Exception reading TPM spec version", exc_info=True)
        else:
            details.append(("TPM", "Not Found"))
            tpm_ok = False
    except Exception as e:
        details.append(("TPM", _wmi_failure_reason(e)))

    # A check that could not run is unknown, not failed. Scoring an
    # access-denied TPM query as "absent" put a red "Insecure" verdict on a
    # machine nobody had actually asked about.
    answered = [ok for ok in (sb_ok, tpm_ok) if ok is not None]
    all_ok = len(answered) == 2 and all(answered)
    any_ok = any(answered)
    unknown = len(answered) < 2

    if all_ok:
        status, color = "Secure", "green"
    elif any_ok:
        status, color = "Partial", "amber"
    elif unknown:
        status, color = "Unknown", "amber"
    else:
        status, color = "Insecure", "red"
    return {"status": status, "color": color, "details": details}


def check_uac() -> Dict[str, Any]:
    key = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    try:
        enable_lua = _reg_read(key, "EnableLUA")
        consent = _reg_read(key, "ConsentPromptBehaviorAdmin")
        secure_desktop = _reg_read(key, "PromptOnSecureDesktop")

        lua_on = enable_lua == 1
        details = [
            ("UAC Enabled", "Yes" if lua_on else "No"),
        ]

        if consent is not None:
            levels = {0: "Never notify (disabled)", 1: "Notify without dimming",
                      2: "Notify and dim desktop", 3: "Notify and dim (default)",
                      4: "Always notify (no auto-elevate)", 5: "Always notify (max)"}
            details.append(("Prompt Level", levels.get(consent, f"Unknown ({consent})")))
        if secure_desktop is not None:
            details.append(("Secure Desktop", "Yes" if secure_desktop == 1 else "No"))

        if lua_on:
            color, status = "green", "Enabled"
        elif lua_on is False:
            color, status = "red", "Disabled"
        else:
            color, status = "amber", "Unknown"
        return {"status": status, "color": color, "details": details, "enabled": lua_on}
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_smartscreen() -> Dict[str, Any]:
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System"
    try:
        val = _reg_read(key, "EnableSmartScreen")
        if val == 1:
            return {"status": "Enabled", "color": "green", "details": [("SmartScreen", "On")], "enabled": True}
        elif val == 0:
            return {"status": "Disabled", "color": "red", "details": [("SmartScreen", "Off")], "enabled": False}
        # Fallback: check Explorer key
        val2 = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer", "SmartScreenEnabled", "REG_SZ")
        if val2:
            return {"status": val2, "color": "green" if "off" not in str(val2).lower() else "red",
                    "details": [("SmartScreen", val2)], "enabled": "off" not in str(val2).lower()}
        return {"status": "Not Configured", "color": "amber", "details": [("SmartScreen", "Not configured via policy")]}
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_hvci() -> Dict[str, Any]:
    """Hypervisor-enforced Code Integrity, as it is RUNNING right now.

    This returned no `enabled` and no `available` on any path, so a control
    bound to it could never read a value at all.
    """
    try:
        rc, out, err = _ps(
            "Get-CimInstance -ClassName Win32_DeviceGuard -Namespace "
            r"root\Microsoft\Windows\DeviceGuard | "
            "Select-Object VirtualizationBasedSecurityStatus, RequiredSecurityProperties, "
            "AvailableSecurityProperties, SecurityServicesConfigured, SecurityServicesRunning | "
            "ConvertTo-Json -Compress", timeout=20
        )
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("HVCI / Memory Integrity",
                                 f"Could not read: {(err or '').strip()[:60]}")]}
        data = json.loads(out)
        vbs_status = data.get("VirtualizationBasedSecurityStatus", 0)
        vbs_map = {0: "Disabled", 1: "Enabled but not running",
                   2: "Enabled and running"}
        details = [("VBS Status", vbs_map.get(vbs_status, f"Unknown ({vbs_status})"))]
        # SecurityServicesRunning is a LIST of service ids in newer builds and
        # a bitmask in older ones. 2 is HVCI in both readings.
        running = data.get("SecurityServicesRunning", 0)
        if isinstance(running, list):
            hvci_on = 2 in running
        elif isinstance(running, int):
            hvci_on = bool(running & 2)
        else:
            hvci_on = False
        details.append(("HVCI / Memory Integrity", "On" if hvci_on else "Off"))
        if vbs_status >= 1 and hvci_on:
            status, color = "Protected", "green"
        elif vbs_status >= 1:
            status, color = "Partial (VBS on, HVCI off)", "amber"
        else:
            status, color = "Disabled", "red"
        return {"status": status, "color": color, "available": True,
                "enabled": hvci_on, "details": details}
    except Exception:
        return {"status": "Error", "color": "amber", "available": False,
                "details": [("HVCI", "Check failed")]}


def check_credential_guard() -> Dict[str, Any]:
    try:
        rc, out, err = _ps(
            "Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\\Microsoft\\Windows\\DeviceGuard | "
            "Select-Object SecurityServicesConfigured, SecurityServicesRunning | "
            "ConvertTo-Json -Compress", timeout=20
        )
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber",
                    "available": False,
                    "details": [("Credential Guard", "WMI unavailable")]}

        data = json.loads(out)
        sec_conf = data.get("SecurityServicesConfigured", 0)
        sec_run = data.get("SecurityServicesRunning", 0)
        cg_configured = bool(int(sec_conf) & 1) if isinstance(sec_conf, int) else False
        cg_running = bool(int(sec_run) & 1) if isinstance(sec_run, int) else False

        if cg_running:
            return {"status": "Running", "color": "green",
                    "available": True, "enabled": True,
                    "details": [("Credential Guard", "Active")]}
        elif cg_configured:
            return {"status": "Configured (needs reboot)", "color": "amber",
                    "available": True, "enabled": False,
                    "details": [("Credential Guard", "Configured, reboot required")]}
        return {"status": "Not Configured", "color": "amber",
                "available": True, "enabled": False,
                "details": [("Credential Guard", "Off")]}
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber",
                "details": [("Credential Guard", "Check failed")]}


def check_lsass_protection() -> Dict[str, Any]:
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
    try:
        val = _reg_read(key, "RunAsPPL")
        if val == 1:
            return {"status": "Protected", "color": "green",
                    "details": [("LSASS Protection", "On (RunAsPPL=1)")], "enabled": True}
        elif val == 2:
            return {"status": "Protected (UEFI Lock)", "color": "green",
                    "details": [("LSASS Protection", "On with UEFI lock (RunAsPPL=2)")], "enabled": True}
        elif val == 0:
            return {"status": "Not Protected", "color": "red",
                    "details": [("LSASS Protection", "Off (RunAsPPL=0)")], "enabled": False}
        return {"status": "Not Configured", "color": "amber",
                "details": [("LSASS Protection", "Not set")]}
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_tamper_protection() -> Dict[str, Any]:
    try:
        c = _wmi_namespace(r"root\Microsoft\Windows\Defender")
        status_obj = c.MSFT_MpComputerStatus()[0]
        try:
            tamper = bool(status_obj.IsTamperProtected)
        except Exception:
            return {"status": "Unknown (pre-1903?)", "color": "amber",
                    "details": [("Tamper Protection", "Not supported on this build")]}
        if tamper:
            return {"status": "On", "color": "green",
                    "details": [("Tamper Protection", "On")], "enabled": True}
        return {"status": "Off", "color": "red",
                "details": [("Tamper Protection", "Off")], "enabled": False}
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_pua_protection() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("PUA Protection", f"Could not read: {reason}")]}
        level = prefs.get("PUAProtection", 0)
        # Disabled=0, Enabled=1, AuditMode=2 -- read off the live cmdlet's own
        # enum, not from memory. This table had 1 and 2 the other way round and
        # coloured 2 green, so a machine that was only AUDITING potentially
        # unwanted applications was told it was blocking them.
        labels = {0: "Off", 1: "On (Block)", 2: "Audit Mode"}
        label = labels.get(level, f"Unknown ({level})")
        enabled = level == 1
        return {
            "status": label,
            "color": "green" if level == 1 else ("amber" if level == 2 else "red"),
            "available": True, "details": [("PUA Protection", label)],
            "enabled": enabled, "level": level,
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_controlled_folder_access() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Controlled Folder Access", f"Could not read: {reason}")]}
        cfa = prefs.get("EnableControlledFolderAccess", 0)
        enabled = cfa == 1
        return {
            "status": "On" if enabled else "Off",
            "color": "green" if enabled else "red",
            "available": True,
            "details": [("Controlled Folder Access", "On" if enabled else "Off")],
            "enabled": enabled,
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_cloud_protection() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Cloud Protection", f"Could not read: {reason}")]}
        maps = prefs.get("MAPSReporting", 0)
        samples = prefs.get("SubmitSamplesConsent", 0)
        maps_labels = {0: "Off", 1: "Basic", 2: "Advanced"}
        map_label = maps_labels.get(maps, str(maps))
        samp_labels = {0: "Never", 1: "Always prompt", 2: "Auto (safe)", 3: "Auto (all)"}
        samp_label = samp_labels.get(samples, str(samples))
        cloud_on = maps >= 1
        return {
            "status": map_label,
            "color": "green" if maps >= 2 else ("amber" if maps == 1 else "red"),
            "available": True,
            "details": [
                ("Cloud Protection", map_label),
                ("Sample Submission", samp_label),
            ],
            "enabled": cloud_on,
            "maps_level": maps,
            "samples_level": samples,
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_defender_signatures() -> Dict[str, Any]:
    try:
        status = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unavailable", "color": "amber", "available": False,
                    "details": [("Signatures", f"Could not retrieve: {reason}")]}
        data = status
        enabled = data.get("AntivirusEnabled", False)
        age_hours = data.get("AntivirusSignatureAge", -1)
        last_updated = str(data.get("AntivirusSignatureLastUpdated", ""))
        version = str(data.get("AntivirusSignatureVersion", "Unknown"))

        if age_hours >= 0:
            if age_hours < 24:
                age_color = "green"
                age_label = "Up to date"
            elif age_hours < 168:
                age_color = "amber"
                age_label = "Outdated"
            else:
                age_color = "red"
                age_label = "Stale"
        else:
            age_color = "amber"
            age_label = "Unknown"

        # Format the ISO timestamp
        if last_updated:
            try:
                from datetime import datetime
                dt = datetime.strptime(last_updated[:19], "%Y-%m-%dT%H:%M:%S")
                last_updated = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                import logging
                _log = logging.getLogger(__name__)
                _log.debug("Could not parse AV signature timestamp '%s'", last_updated, exc_info=True)

        details = [
            ("AV Enabled", "Yes" if enabled else "No"),
            ("Signature Version", version),
            ("Signature Age", f"{age_hours}h" if age_hours >= 0 else "N/A"),
            ("Last Updated", last_updated or "N/A"),
        ]
        return {
            "status": age_label,
            "color": age_color,
            "available": True,
            "details": details,
            "age_hours": age_hours,
            "version": version,
            "last_updated": last_updated,
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def check_rdp() -> Dict[str, Any]:
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
    try:
        val = _reg_read(key, "fDenyTSConnections")
        if val == 1:
            return {"status": "Disabled", "color": "green",
                    "details": [("RDP", "Off (deny connections)")], "enabled": False}
        elif val == 0:
            return {"status": "Enabled", "color": "red",
                    "details": [("RDP", "On (allow connections)")], "enabled": True}
        return {"status": "Not Configured", "color": "amber", "details": [("RDP", "Unknown")]}
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


def _feature_list_in_hand() -> bool:
    """Is the optional_features snapshot both BUILT and usable?

    A refusal is cached like any other answer, so `name in _cache` alone says
    "somebody asked", not "somebody got a list". Unelevated that is exactly
    what happens -- the prefetch asks, DISM refuses, the empty result is
    cached -- and reading it as "the list is in hand" sent both cheap readers
    back to an unusable snapshot, which is how Firewall & Network's unreadable
    count went UP after the cheap sources were added.
    """
    return ("optional_features" in snapshots._cache
            and snapshots.unavailable("optional_features") is None)


def _smbv1_from_server_config() -> Optional[Dict[str, Any]]:
    """SMBv1's state without enumerating every optional feature on the box.

    `Get-WindowsOptionalFeature -Online` is a DISM enumeration: 7.9s of the
    8.8s Firewall & Network cost elevated, to read one control. This asks the
    SMB server what it is doing instead -- 1.02s, measured, and it answers an
    ordinary user, where the feature list needs elevation even to refuse.

    Returns None when it could not get an answer, which is never the same as
    "SMBv1 is off": "Disabled" is precisely the answer that would let somebody
    stop worrying about EternalBlue.
    """
    rc, out, err = _ps(
        "Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol "
        "| ConvertTo-Json -Compress", timeout=20)
    if rc != 0 or not out:
        logger.debug("SMB server config unavailable (rc=%s): %s", rc, err)
        return None
    try:
        data = json.loads(out)
    except ValueError:
        # rc 0 and unparseable: a cmdlet talking to the human as well as the
        # pipeline. Get-SpeculationControlSettings does exactly this.
        logger.debug("SMB server config did not parse: %r", out[:120])
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    enabled = data.get("EnableSMB1Protocol")
    if enabled is None:
        return None
    if enabled:
        return {"status": "Enabled", "color": "red", "available": True,
                "details": [("SMBv1", "On (vulnerable to EternalBlue)"),
                            ("Read from", "Get-SmbServerConfiguration")],
                "enabled": True}
    return {"status": "Disabled", "color": "green", "available": True,
            "details": [("SMBv1", "Off"),
                        ("Read from", "Get-SmbServerConfiguration")],
            "enabled": False}


def check_smbv1() -> Dict[str, Any]:
    try:
        # The feature list is the better answer -- it knows about the client
        # half too -- but only worth having if somebody has already paid for
        # it. Asking for it here is what BUILDS it.
        if not _feature_list_in_hand():
            cheap = _smbv1_from_server_config()
            if cheap is not None:
                return cheap

        features = snapshots.optional_features()
        reason = snapshots.unavailable("optional_features")
        if reason is not None:
            # This used to answer "Probably Disabled (Win10+)", green, with
            # available=True -- a guess dressed as a reading, and on this
            # machine it fires every unelevated run because
            # Get-WindowsOptionalFeature requires elevation. "Probably" is
            # not a state of the machine.
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("SMBv1", f"Could not read: {reason}")]}
        state = features.get("smb1protocol")
        if isinstance(state, str) and "Enabled" in state:
            return {"status": "Enabled", "color": "red", "available": True,
                    "details": [("SMBv1", "On (vulnerable to EternalBlue)")], "enabled": True}
        return {"status": "Disabled", "color": "green", "available": True,
                "details": [("SMBv1", "Off")], "enabled": False}
    except Exception as e:
        # A reader that threw took no reading. Without `available: False` this
        # is the shape that published a refused BitLocker read as "not
        # encrypted".
        return {"status": f"Error: {e}", "color": "amber",
                "available": False, "details": []}


def check_network_protection_defender() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Network Protection", f"Could not read: {reason}")]}
        np_val = prefs.get("EnableNetworkProtection", 0)
        labels = {0: "Off", 1: "On (Block)", 2: "Audit Mode"}
        label = labels.get(np_val, f"Unknown ({np_val})")
        enabled = np_val >= 1
        return {
            "status": label,
            "color": "green" if np_val == 1 else ("amber" if np_val == 2 else "red"),
            "available": True,
            "details": [("Network Protection", label)],
            "enabled": enabled,
            "level": np_val,
        }
    except Exception as e:
        return {"status": f"Error: {e}", "color": "amber", "details": []}


# ── Toggle / Write Actions ─────────────────────────────────────────────────

def _set_mp_pref(pref_name: str, value: int, label: str) -> Dict[str, Any]:
    """Set a Defender preference and return result dict."""
    rc, out, err = _ps(
        f"$ErrorActionPreference='Stop'; try {{ Set-MpPreference -{pref_name} {value}; "
        f"Write-Output 'SUCCESS' }} catch {{ Write-Error $_.Exception.Message }}",
        timeout=30
    )
    if rc == 0 and "SUCCESS" in out:
        return {"success": True, "message": f"{label} set successfully"}
    return {"success": False, "message": err or out or "Unknown error setting preference"}


def get_defender_realtime() -> Dict[str, Any]:
    """Get current real-time protection state for toggling."""
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"enabled": None, "error": reason}
        disabled = prefs.get("DisableRealtimeMonitoring", False)
        return {"enabled": not disabled, "raw": prefs}
    except Exception as e:
        return {"enabled": None, "error": str(e)}


def set_defender_realtime(enabled: bool) -> Dict[str, Any]:
    """Enable or disable Defender real-time protection."""
    before = get_defender_realtime()
    before_val = before.get("enabled")
    result = _set_mp_pref("DisableRealtimeMonitoring", 0 if enabled else 1,
                          "Real-time protection")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result


def set_defender_cloud(enabled: bool, map_level: int = 2) -> Dict[str, Any]:
    """Enable or disable cloud-delivered protection (MAPS)."""
    before = check_cloud_protection()
    before_val = before.get("maps_level")
    result = _set_mp_pref("MAPSReporting", map_level if enabled else 0,
                          "Cloud protection")
    # Also set sample submission to auto-safe when enabling cloud
    if enabled:
        _set_mp_pref("SubmitSamplesConsent", 1, "Auto sample submission")
    result["before_value"] = before_val
    result["after_value"] = map_level if enabled else 0
    result["action"] = "enable" if enabled else "disable"
    return result


def set_defender_sample_submission(enabled: bool, level: int = 1) -> Dict[str, Any]:
    """Enable or disable automatic sample submission."""
    before_data = check_cloud_protection()
    before_val = before_data.get("samples_level")
    result = _set_mp_pref("SubmitSamplesConsent", level if enabled else 0,
                          "Sample submission")
    result["before_value"] = before_val
    result["after_value"] = level if enabled else 0
    result["action"] = "enable" if enabled else "disable"
    return result


def set_pua_protection(enabled: bool, level: int = 1) -> Dict[str, Any]:
    """Enable or disable PUA protection."""
    before = check_pua_protection()
    before_val = before.get("level")
    result = _set_mp_pref("PUAProtection", level if enabled else 0,
                          "PUA protection")
    result["before_value"] = before_val
    result["after_value"] = level if enabled else 0
    result["action"] = "enable" if enabled else "disable"
    return result


def set_controlled_folder_access(enabled: bool) -> Dict[str, Any]:
    """Enable or disable Controlled Folder Access."""
    before = check_controlled_folder_access()
    before_val = before.get("enabled")
    result = _set_mp_pref("EnableControlledFolderAccess", 1 if enabled else 0,
                          "Controlled Folder Access")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result


def set_network_protection_defender(enabled: bool, level: int = 1) -> Dict[str, Any]:
    """Enable or disable network protection."""
    before = check_network_protection_defender()
    before_val = before.get("level")
    result = _set_mp_pref("EnableNetworkProtection", level if enabled else 0,
                          "Network protection")
    result["before_value"] = before_val
    result["after_value"] = level if enabled else 0
    result["action"] = "enable" if enabled else "disable"
    return result


def set_tamper_protection(enabled: bool) -> Dict[str, Any]:
    """Enable or disable Tamper Protection."""
    before = check_tamper_protection()
    before_val = before.get("enabled")
    new_val = 0 if enabled else 1  # Inverted: DisableTamperProtection=0 means enabled
    result = _set_mp_pref("DisableTamperProtection", new_val, "Tamper protection")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result


def set_firewall_profile(profile: str, enabled: bool) -> Dict[str, Any]:
    """Enable or disable a firewall profile (Domain, Private, or Public)."""
    profile_map = {"Domain": "domainprofile", "Private": "privateprofile", "Public": "publicprofile"}
    netsh_profile = profile_map.get(profile, profile.lower() + "profile")
    state = "on" if enabled else "off"
    rc, out, err = _cmd_run(
        ["netsh", "advfirewall", "set", netsh_profile, "state", state],
        timeout=30
    )
    if rc == 0:
        return {"success": True, "message": f"{profile} firewall turned {state}",
                "before_value": not enabled, "after_value": enabled,
                "action": "enable" if enabled else "disable"}
    return {"success": False, "message": err or out or f"Failed to set {profile} firewall"}


def set_smartscreen(enabled: bool) -> Dict[str, Any]:
    """Enable or disable SmartScreen via registry policy."""
    before = check_smartscreen()
    before_val = before.get("enabled")
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System"
    ok = _reg_write(key, "EnableSmartScreen", 1 if enabled else 0, "REG_DWORD")
    if ok:
        return {"success": True, "message": f"SmartScreen {'enabled' if enabled else 'disabled'}",
                "before_value": before_val, "after_value": enabled,
                "action": "enable" if enabled else "disable"}
    return {"success": False, "message": "Failed to write registry key"}


def set_lsass_protection(enabled: bool) -> Dict[str, Any]:
    """Enable or disable LSASS Protected Process Light (requires reboot)."""
    before = check_lsass_protection()
    before_val = before.get("enabled")
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
    ok = _reg_write(key, "RunAsPPL", 1 if enabled else 0, "REG_DWORD")
    if ok:
        msg = f"LSASS protection {'enabled' if enabled else 'disabled'} (reboot required)"
        return {"success": True, "message": msg,
                "before_value": before_val, "after_value": enabled,
                "action": "enable" if enabled else "disable",
                "reboot_required": True}
    return {"success": False, "message": "Failed to write LSASS registry key"}


def set_uac_level(enable_lua: bool = True, consent_level: int = 2) -> Dict[str, Any]:
    """Configure UAC level. consent_level: 0=never, 1=no dim, 2=dim, 3=default, 4=always, 5=max."""
    before = check_uac()
    key = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    ok1 = _reg_write(key, "EnableLUA", 1 if enable_lua else 0)
    ok2 = _reg_write(key, "ConsentPromptBehaviorAdmin", consent_level)
    ok3 = _reg_write(key, "PromptOnSecureDesktop", 1)
    success = ok1 and ok2 and ok3
    return {
        "success": success,
        "message": f"UAC {'enabled' if enable_lua else 'disabled'} at level {consent_level}"
        if success else "Failed to configure UAC",
        "before_value": before, "after_value": {"enable_lua": enable_lua, "level": consent_level},
        "action": "configure_uac",
    }


def run_quick_scan() -> Dict[str, Any]:
    """Run a Windows Defender quick scan."""
    mp = _resolve_mpcmdrun()
    rc, out, err = _cmd_run([mp, "-Scan", "-ScanType", "1"], timeout=600)
    return {
        "success": rc == 0,
        "stdout": out, "stderr": err,
        "message": "Quick scan completed" if rc == 0 else
        f"Scan returned code {rc}: " + (err or out)[:200]
    }


def run_update_definitions() -> Dict[str, Any]:
    """Update Windows Defender signature definitions."""
    mp = _resolve_mpcmdrun()
    rc, out, err = _cmd_run([mp, "-SignatureUpdate"], timeout=300)
    return {
        "success": rc == 0,
        "stdout": out, "stderr": err,
        "message": "Definitions updated" if rc == 0 else
        f"Update returned code {rc}: " + (err or out)[:200]
    }


class SecurityLogUnreadable(Exception):
    """The Security log could not be read (almost always: not elevated)."""


def get_security_events(count: int = 30) -> List[Dict[str, str]]:
    """Retrieve recent security events from the Security log.

    Raises SecurityLogUnreadable when the read is refused. It used to return
    [] for that, so an unelevated session was told "Nothing found in the
    Security log" about a log it was never allowed to open.
    """
    try:
        rc, out, err = _cmd_run(
            ["wevtutil", "qe", "Security", f"/c:{count}", "/f:text", "/rd:true"],
            timeout=30
        )
        if rc != 0:
            reason = (err or out or "").strip().splitlines()
            raise SecurityLogUnreadable(
                reason[0] if reason else f"wevtutil exited {rc}")
        events = []
        current = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Event["):
                if current:
                    events.append(current)
                current = {}
            elif line.startswith("EventID:"):
                current["event_id"] = line.split(":", 1)[1].strip()
            elif line.startswith("TimeCreated:"):
                tc = line.split(":", 1)[1].strip()
                tc = tc.replace("SystemTime=", "")
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(tc[:19])
                    current["time"] = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    current["time"] = tc[:16]
            elif line.startswith("Message:"):
                current["message"] = line.split(":", 1)[1].strip()
                msg = current["message"]
                eid = current.get("event_id", "")
                if "4624" in eid:
                    current["description"] = "Logon success"
                elif "4625" in eid:
                    current["description"] = "Logon failure"
                elif "4634" in eid:
                    current["description"] = "Logoff"
                elif "4648" in eid:
                    current["description"] = "Explicit credential logon"
                elif "4657" in eid:
                    current["description"] = "Registry value modified"
                elif "4672" in eid:
                    current["description"] = "Special privileges assigned"
                elif "4688" in eid:
                    current["description"] = "Process created"
                elif "4720" in eid:
                    current["description"] = "User account created"
                elif "4722" in eid:
                    current["description"] = "User account enabled"
                elif "4723" in eid:
                    current["description"] = "Password change attempt"
                elif "4724" in eid:
                    current["description"] = "Password reset attempt"
                elif "4725" in eid:
                    current["description"] = "User account disabled"
                elif "4726" in eid:
                    current["description"] = "User account deleted"
                elif "4740" in eid:
                    current["description"] = "Account locked out"
                elif "1102" in eid:
                    current["description"] = "🛑 Audit log cleared"
                elif "5140" in eid:
                    current["description"] = "Network share accessed"
                elif "5156" in eid:
                    current["description"] = "WFP connection allowed"
                elif "5157" in eid:
                    current["description"] = "WFP connection blocked"
                else:
                    current["description"] = msg[:80] if len(msg) > 80 else msg
        if current:
            events.append(current)

        known_ids = {"4624", "4625", "4634", "4648", "4657", "4672", "4688",
                     "4720", "4722", "4723", "4724", "4725", "4726", "4740",
                     "1102", "5140", "5156", "5157"}
        return [e for e in events if e.get("event_id") in known_ids]
    except SecurityLogUnreadable:
        raise
    except Exception:
        logger.warning("could not parse the Security log output", exc_info=True)
        return []


# ── Aggregation ────────────────────────────────────────────────────────────

def get_all_security_status() -> dict:
    return {
        "defender": check_defender(),
        "firewall": check_firewall(),
        "bitlocker": check_bitlocker(),
        "secure_boot_tpm": check_secure_boot_tpm(),
    }


def check_applocker() -> Dict[str, Any]:
    """Check if AppLocker (AppIDSvc) is configured via policy."""
    try:
        key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\SrpV2"
        for sub in ["Exe", "Dll", "Msi", "Script"]:
            val = _reg_read(f"{key}\\{sub}", "EnforcementMode")
            if val is not None:
                mode_map = {0: "Audit Only", 1: "Enforce Rules"}
                mode = mode_map.get(val, f"Unknown ({val})")
                return {"status": f"Active ({mode})", "color": "green",
                        "details": [("AppLocker", f"Configured — {mode}")], "enabled": True}
        # Also check if just the service is running
        info = snapshots.service_states().get("appidsvc")
        if info and info.get("status") == "Running":
            return {"status": "Service Running", "color": "amber",
                    "details": [("AppLocker", "Service running, no rules detected")]}
        return {"status": "Not Configured", "color": "amber",
                "details": [("AppLocker", "Not configured")], "enabled": False}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("AppLocker", "Check failed")]}


def check_windows_hello() -> Dict[str, Any]:
    """Check if Windows Hello for Business is configured."""
    try:
        key = r"HKLM\SOFTWARE\Policies\Microsoft\PassportForWork"
        val = _reg_read(key, "Enabled")
        if val == 1:
            return {"status": "Enabled", "color": "green",
                    "details": [("Windows Hello", "Configured via policy")], "enabled": True}
        # Check local machine config
        key2 = r"HKLM\SOFTWARE\Microsoft\Policies\PassportForWork"
        val2 = _reg_read(key2, "Enabled")
        if val2 == 1:
            return {"status": "Enabled", "color": "green",
                    "details": [("Windows Hello", "Policy enabled")], "enabled": True}
        # Check NGC key existence (indicates PIN/Hello configured for at least one user)
        rc, out, _ = _ps(
            "Get-ChildItem -Path 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography\\Ngc\\*' "
            "-ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count",
            timeout=15
        )
        if rc == 0 and out.strip() and int(out.strip()) > 0:
            return {"status": "Configured (users enrolled)", "color": "green",
                    "details": [("Windows Hello", f"{out.strip()} user(s) enrolled")],
                    "enabled": True}
        return {"status": "Not Configured", "color": "amber",
                "details": [("Windows Hello", "Not set up")], "enabled": False}
    except Exception:
        return {"status": "Unknown", "color": "amber",
                "details": [("Windows Hello", "Check failed")]}


def get_overview_status() -> dict:
    """Just the fourteen cards the Overview tab draws.

    The pane used to call `get_extended_status()` and read fourteen of its
    seventy-eight keys. Measured unelevated on 2026-08-24: 37.3s for the full
    sweep against 12.4s for these fourteen -- and the module's own auto-refresh
    interval is 30 SECONDS, so the timer kept relaunching a sweep that had not
    finished. What the user saw was a pane stuck on "Loading...".
    """
    return {
        "defender": check_defender(),
        "firewall": check_firewall(),
        "bitlocker": check_bitlocker(),
        "secure_boot_tpm": check_secure_boot_tpm(),
        "uac": check_uac(),
        "smartscreen": check_smartscreen(),
        "hvci": check_hvci(),
        "credential_guard": check_credential_guard(),
        "lsass_protection": check_lsass_protection(),
        "tamper_protection": check_tamper_protection(),
        "rdp": check_rdp(),
        "smbv1": check_smbv1(),
        "applocker": check_applocker(),
        "windows_hello": check_windows_hello(),
    }


def get_extended_status() -> dict:
    """Full security posture: all checks in one call.

    Nothing in the app calls this on a paint path any more -- see
    `get_overview_status`. It stays whole as the full-report entry point.
    """
    return {
        **get_overview_status(),
        "pua_protection": check_pua_protection(),
        "controlled_folder_access": check_controlled_folder_access(),
        "cloud_protection": check_cloud_protection(),
        "network_protection": check_network_protection_defender(),
        # --- Defender Detail ---
        "def_behavior": check_defender_behavior_monitoring(),
        "def_nis": check_defender_nis(),
        "def_script_scan": check_defender_script_scanning(),
        "def_ioav": check_defender_ioav(),
        "def_email_scan": check_defender_email_scanning(),
        "def_archive_scan": check_defender_archive_scanning(),
        "def_removable_scan": check_defender_removable_drive(),
        "def_cloud_timeout": check_defender_cloud_timeout(),
        "def_cloud_block": check_defender_cloud_block_level(),
        "def_cpu_limit": check_defender_cpu_usage(),
        "def_check_sigs": check_defender_check_signatures(),
        "def_catchup_scan": check_defender_catchup_scan(),
        "def_threats": check_defender_threats(),
        "def_quarantine": check_defender_quarantine(),
        "def_last_scan": check_defender_last_scan(),
        "def_engine_ver": check_defender_engine_version(),
        "def_av_mode": check_defender_av_mode(),
        "def_oobe": check_defender_oobe(),
        "def_asr_rules": check_defender_asr_rules(),
        "def_elam": check_elam(),
        "def_scan_history": check_defender_scanning_history(),
        # --- Network ---
        "llmnr": check_llmnr(),
        "netbios": check_netbios_tcpip(),
        "wpad": check_wpad(),
        "mdns": check_mdns(),
        "winrm": check_winrm(),
        "remote_registry": check_remote_registry(),
        "telnet": check_telnet(),
        "smb_signing": check_smb_signing(),
        "ntlm_level": check_ntlm_level(),
        "network_profile": check_network_profile(),
        "fw_stealth": check_firewall_stealth(),
        "listening_ports": check_listening_ports(),
        "rdp_nla": check_rdp_nla(),
        "anonymous_restrict": check_anonymous_restrict(),
        "admin_shares": check_admin_shares(),
        "autorun": check_autorun(),
        # --- System Hardening ---
        "powershell_v2": check_powershell_v2(),
        "ps_constrained": check_ps_constrained_lang(),
        "ps_exec_policy": check_ps_execution_policy(),
        "ps_script_log": check_ps_script_block_logging(),
        "ps_transcription": check_ps_transcription(),
        "wdigest": check_wdigest(),
        "cached_logons": check_cached_logons(),
        "account_lockout": check_account_lockout(),
        "password_min_len": check_password_min_length(),
        "pagefile_clear": check_pagefile_clear(),
        "ctrl_alt_del": check_ctrl_alt_del(),
        # --- Features ---
        "sandbox": check_sandbox(),
        "hyperv": check_hyperv(),
        "wsl": check_wsl(),
        "audit_policy": check_audit_policy(),
        "tpm_details": check_tpm_details(),
        "bios_mode": check_bios_mode(),
        "test_signing": check_test_signing(),
        "windows_version": check_windows_version(),
        "wu_service": check_wu_service(),
        "wu_auto_update": check_wu_auto_update(),
        "dump_encryption": check_dump_encryption(),
        "bitlocker_enc": check_bitlocker_encryption(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY A — DEFENDER DETAILS (15 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def _mp_pref_flag_check(field: str, label: str, invert: bool = True,
                         good_color: str = "green", bad_color: str = "red") -> Dict[str, Any]:
    """Shared shape for a single-field Get-MpPreference on/off reader.

    `invert=True` matches the many `Disable*` fields, where 0 means enabled.
    """
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(label, f"Could not read: {reason}")]}
        raw = prefs.get(field, 0)
        enabled = (not bool(raw)) if invert else bool(raw)
        return {"status": "On" if enabled else "Off",
                "color": good_color if enabled else bad_color,
                "available": True, "enabled": enabled,
                "details": [(label, "On" if enabled else "Off")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}


def check_defender_behavior_monitoring() -> Dict[str, Any]:
    return _mp_pref_flag_check("DisableBehaviorMonitoring", "Behavior Monitoring")

def check_defender_nis() -> Dict[str, Any]:
    try:
        status = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("NIS", f"Could not read: {reason}")]}
        nis = bool(status.get("NISEnabled", False))
        return {"status": "On" if nis else "Off", "color": "green" if nis else "red",
                "available": True,
                "details": [("Network Inspection System", "On" if nis else "Off")], "enabled": nis}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_script_scanning() -> Dict[str, Any]:
    return _mp_pref_flag_check("DisableScriptScanning", "Script Scanning")

def check_defender_ioav() -> Dict[str, Any]:
    return _mp_pref_flag_check("DisableIOAVProtection", "Downloaded Files Scanning")

def check_defender_email_scanning() -> Dict[str, Any]:
    return _mp_pref_flag_check("DisableEmailScanning", "Email Scanning")

def check_defender_archive_scanning() -> Dict[str, Any]:
    return _mp_pref_flag_check("DisableArchiveScanning", "Archive Scanning")

def check_defender_removable_drive() -> Dict[str, Any]:
    return _mp_pref_flag_check("DisableRemovableDriveScanning", "Removable Drive Scanning")

def check_defender_cloud_timeout() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Cloud Timeout", f"Could not read: {reason}")]}
        # Get-MpPreference returns CloudExtendedTimeout, never "CloudTimeout".
        # Asking for the name that does not exist meant the `.get()` default
        # was the answer on every machine: a flat "50s / green" that had read
        # nothing. It is 0 on this one.
        timeout = prefs.get("CloudExtendedTimeout", 0)
        color, label = (("green", f"+{timeout}s") if timeout >= 30
                        else ("amber", f"+{timeout}s (short)"))
        return {"status": label, "color": color, "available": True,
                "seconds": timeout,
                "details": [("Cloud Extended Timeout",
                             f"+{timeout}s on top of the 10s default")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_cloud_block_level() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Cloud Block Level", f"Could not read: {reason}")]}
        level = prefs.get("CloudBlockLevel", 0)
        # Microsoft's own value names for Set-MpPreference -CloudBlockLevel.
        # This table used to read 2 as "Low" and 6 as "High", naming every
        # level above Default one step too low.
        labels = {0: "Default", 1: "Moderate", 2: "High", 4: "High+",
                  6: "Zero Tolerance"}
        label = labels.get(level, f"Unknown ({level})")
        # High (2) is the level the catalog asks for, so it cannot render amber.
        color = "green" if level >= 2 else "amber"
        return {"status": label, "color": color, "available": True,
                "level": level,
                "details": [("Cloud Block Level", f"Level {level} - {label}")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_cpu_usage() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("CPU Load Limit", f"Could not read: {reason}")]}
        cpu = prefs.get("ScanAvgCPULoadFactor", 50)
        color = "green" if cpu <= 50 else "amber"
        return {"status": f"{cpu}%", "color": color, "available": True,
                "percent": cpu,
                "details": [("Scan CPU Load Limit", f"{cpu}%")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_check_signatures() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Check Signatures Before Scan", f"Could not read: {reason}")]}
        enabled = bool(prefs.get("CheckForSignaturesBeforeRunningScan", False))
        return {"status": "On" if enabled else "Off", "color": "green" if enabled else "amber",
                "available": True,
                "details": [("Check Signatures Before Scan", "Yes" if enabled else "No")], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_catchup_scan() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Catchup Scans", f"Could not read: {reason}")]}
        quick = bool(prefs.get("DisableCatchupQuickScan", False))
        full = bool(prefs.get("DisableCatchupFullScan", False))
        both_disabled = quick and full
        return {"status": "Enabled" if not both_disabled else "Both Disabled",
                "color": "green" if not both_disabled else "red",
                "available": True,
                "details": [("Catchup Quick Scan", "Enabled" if not quick else "Disabled"),
                            ("Catchup Full Scan", "Enabled" if not full else "Disabled")],
                "enabled": not both_disabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_threats() -> Dict[str, Any]:
    try:
        rc, out, _ = _ps("(Get-MpThreatDetection -ErrorAction SilentlyContinue | Measure-Object).Count", timeout=30)
        if rc != 0 or out is None:
            return {"status": "Unknown", "color": "amber", "details": [("Threats Detected", "WMI failed")]}
        count = int(out.strip()) if out.strip().isdigit() else 0
        return {"status": "No active threats" if count == 0 else f"{count} threat(s)",
                "color": "green" if count == 0 else "red",
                "details": [("Active Threats", str(count))]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_quarantine() -> Dict[str, Any]:
    try:
        rc, out, _ = _ps("(Get-MpThreat -ErrorAction SilentlyContinue | Measure-Object).Count", timeout=30)
        if rc != 0 or out is None:
            return {"status": "Unknown", "color": "amber", "details": [("Quarantined Items", "WMI failed")]}
        count = int(out.strip()) if out.strip().isdigit() else 0
        return {"status": "Empty" if count == 0 else f"{count} item(s)",
                "color": "green" if count == 0 else "amber",
                "details": [("Quarantined Items", str(count))]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_last_scan() -> Dict[str, Any]:
    try:
        data = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Last Scans", f"Could not read: {reason}")]}
        def fmt(raw):
            if not raw or str(raw).startswith("0001"):
                return "Never"
            try:
                from datetime import datetime
                dt = datetime.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S")
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                return str(raw)[:16]
        quick_raw = data.get("QuickScanStartTime", "")
        full_raw = data.get("FullScanStartTime", "")
        quick = fmt(quick_raw)
        full = fmt(full_raw)
        if full != "Never":
            return {"status": "Scanned", "color": "green", "available": True,
                    "details": [("Last Quick Scan", quick), ("Last Full Scan", full)]}
        elif quick != "Never":
            return {"status": "Quick scan only", "color": "amber", "available": True,
                    "details": [("Last Quick Scan", quick), ("Last Full Scan", "Never")]}
        return {"status": "Never scanned", "color": "red", "available": True,
                "details": [("Last Quick Scan", "Never"), ("Last Full Scan", "Never")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_engine_version() -> Dict[str, Any]:
    try:
        status = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Engine Version", f"Could not read: {reason}")]}
        ver = str(status.get("AMEngineVersion", "Unknown"))
        return {"status": ver, "color": "green", "available": True,
                "details": [("AM Engine Version", ver)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_av_mode() -> Dict[str, Any]:
    try:
        status = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("AV Mode", f"Could not read: {reason}")]}
        mode = status.get("AMRunningMode", -1)
        if isinstance(mode, str):
            label = mode
        else:
            mode_map = {0: "Normal", 1: "Passive", 2: "EDR Block", 3: "SxS"}
            label = mode_map.get(mode, f"Unknown ({mode})")
        color = "green" if "Normal" in str(label) else ("amber" if "Passive" in str(label) else "red")
        return {"status": label, "color": color, "available": True,
                "details": [("Defender AV Mode", label)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_oobe() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("OOBE RTP", f"Could not read: {reason}")]}
        enabled = bool(prefs.get("OobeEnableRtpAndSigUpdate", False))
        return {"status": "On" if enabled else "Off", "color": "green" if enabled else "amber",
                "available": True,
                "details": [("OOBE RTP + Sig Update", "On" if enabled else "Off")], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_asr_rules() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("ASR Rules", f"Could not read: {reason}")]}
        ids = prefs.get("AttackSurfaceReductionRules_Ids", []) or []
        count = len(ids) if isinstance(ids, list) else (1 if ids else 0)
        if count == 0:
            return {"status": "No Rules", "color": "red", "available": True,
                    "details": [("ASR Rules", "None configured")], "enabled": False}
        return {"status": f"{count} Rules", "color": "green", "available": True,
                "details": [("ASR Rules Configured", str(count))], "enabled": True, "rule_count": count}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_elam() -> Dict[str, Any]:
    try:
        status = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("ELAM", f"Could not read: {reason}")]}
        enabled = bool(status.get("AMServiceEnabled", False))
        return {"status": "On" if enabled else "Off", "color": "green" if enabled else "red",
                "available": True,
                "details": [("ELAM (Early Launch Antimalware)", "On" if enabled else "Off")], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_defender_scanning_history() -> Dict[str, Any]:
    try:
        status = snapshots.mp_computer_status()
        reason = snapshots.unavailable("mp_computer_status")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Scan History", f"Could not read: {reason}")]}
        quick = str(status.get("QuickScanEndTime", "Never"))
        sig = str(status.get("QuickScanSignatureVersion", "?"))
        return {"status": "History Available" if quick != "Never" else "No History",
                "color": "green" if quick != "Never" else "amber",
                "available": True,
                "details": [("Last Quick Scan", quick[:19]), ("Signature Version", sig)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY B — NETWORK SECURITY (17 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def check_llmnr() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient", "EnableMulticast")
        if val is None:
            # No policy means LLMNR is ON: that is Windows' default,
            # and it is the state this control exists to change.
            return {"status": "Not Configured", "color": "amber",
                    "available": True, "enabled": True,
                    "details": [("LLMNR", "No policy — enabled by default")]}
        disabled = val == 0
        return {"status": "Disabled" if disabled else "Enabled",
                "available": True,
                "color": "green" if disabled else "red",
                "details": [("LLMNR", "Off" if disabled else "On (MitM risk)")],
                "enabled": not disabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_netbios_tcpip() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Services\NetBT\Parameters", "NodeType")
        if val is None:
            # No NodeType means H-node, which has NetBIOS ON.
            return {"status": "Not Configured", "color": "amber",
                    "available": True, "enabled": True,
                    "details": [("NetBIOS", "No NodeType — default H-node")]}
        labels = {1: "B-node (broadcast)", 2: "P-node (NetBIOS disabled)", 4: "M-node", 8: "H-node (default)"}
        label = labels.get(val, f"Unknown ({val})")
        disabled = val == 2
        return {"status": label, "available": True, "node_type": val,
                "color": "green" if disabled else ("amber" if val == 8 else "red"),
                "details": [("NetBIOS NodeType", label)],
                "enabled": not disabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

#: Bit 0x08 of DefaultConnectionSettings byte 8 is "Automatically detect
#: settings" -- the switch that makes this machine ask the network where its
#: proxy configuration is, which is what WPAD poisoning answers.
_WPAD_AUTODETECT_BIT = 0x08


def check_wpad() -> Dict[str, Any]:
    """WPAD auto-detect, per user.

    The `AutoDetect` value this used to read is absent on a default Windows,
    and absence was reported as "Disabled (default)" in GREEN. It is not the
    setting: Internet Settings keeps auto-detect in bit 0x08 of byte 8 of
    DefaultConnectionSettings, and on this machine that bit is SET.
    """
    try:
        val = _reg_read(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings", "AutoDetect")
        if val is not None:
            enabled = val != 0
            return {"status": "Enabled" if enabled else "Disabled",
                    "color": "red" if enabled else "green", "available": True,
                    "details": [("WPAD Auto-Detect (AutoDetect value)",
                                 "On" if enabled else "Off")],
                    "enabled": enabled}
        raw = _reg_read(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion"
                        r"\Internet Settings\Connections",
                        "DefaultConnectionSettings", "REG_BINARY")
        if not raw or len(raw) < 9:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("WPAD Auto-Detect",
                                 "Neither AutoDetect nor DefaultConnectionSettings "
                                 "could be read")]}
        enabled = bool(raw[8] & _WPAD_AUTODETECT_BIT)
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "red" if enabled else "green", "available": True,
                "details": [("WPAD Auto-Detect",
                             "On (this machine asks the network for its proxy)"
                             if enabled else "Off"),
                            ("Connection flags", f"0x{raw[8]:02X}")],
                "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_mdns() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters", "EnableMDNS")
        if val is None:
            # Windows enables mDNS by default.
            return {"status": "Not Configured", "color": "amber",
                    "available": True, "enabled": True,
                    "details": [("mDNS", "No policy — enabled by default")]}
        disabled = val == 0
        return {"status": "Disabled" if disabled else "Enabled",
                "available": True,
                "color": "green" if disabled else "red",
                "details": [("mDNS", "Off" if disabled else "On")],
                "enabled": not disabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_winrm() -> Dict[str, Any]:
    try:
        services = snapshots.service_states()
        reason = snapshots.unavailable("service_states")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("WinRM", f"Could not read: {reason}")]}
        info = services.get("winrm")
        if info is None:
            return {"status": "Not Installed", "color": "green", "available": True,
                    "details": [("WinRM", "Not found")], "enabled": False}
        status = str(info.get("status") or "")
        running = "Running" in status or status == "4"
        return {"status": "Running" if running else "Stopped",
                "color": "red" if running else "green", "available": True,
                "details": [("WinRM", "Running" if running else "Stopped")], "enabled": running}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_remote_registry() -> Dict[str, Any]:
    try:
        services = snapshots.service_states()
        reason = snapshots.unavailable("service_states")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Remote Registry", f"Could not read: {reason}")]}
        info = services.get("remoteregistry")
        if info is None:
            return {"status": "Not Installed", "color": "green", "available": True,
                    "details": [("Remote Registry", "Not found")], "enabled": False}
        status = str(info.get("status") or "")
        running = "Running" in status or status == "4"
        return {"status": "Running" if running else "Stopped",
                "color": "red" if running else "green", "available": True,
                "details": [("Remote Registry", "Running" if running else "Stopped")], "enabled": running}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def _telnet_from_disk() -> Optional[Dict[str, Any]]:
    """Is the Telnet Client installed? Its binary is the feature's payload.

    Installing the TelnetClient optional feature puts telnet.exe in System32,
    and removing it takes the file away, so the file answers in 0.039s --
    measured, unelevated -- where the feature list costs 8.10s to build and
    needs elevation. Checked against the authoritative reading: the elevated
    catalog run recorded telnet_client False, and telnet.exe is absent here.

    None when the question could not be answered at all.
    """
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        return None
    # A 64-bit process reads the real System32; this app is 64-bit.
    present = os.path.exists(os.path.join(system_root, "System32", "telnet.exe"))
    if present:
        return {"status": "Enabled", "color": "red", "available": True,
                "details": [("Telnet Client", "Installed"),
                            ("Read from", "System32\\telnet.exe")],
                "enabled": True}
    return {"status": "Not Installed", "color": "green", "available": True,
            "details": [("Telnet Client", "Feature not present"),
                        ("Read from", "System32\\telnet.exe")],
            "enabled": False}


def check_telnet() -> Dict[str, Any]:
    try:
        # Only worth the feature list if somebody has already built it: it
        # distinguishes Disabled from absent, where the binary cannot. Asking
        # for it here is what costs 8.10s.
        if not _feature_list_in_hand():
            cheap = _telnet_from_disk()
            if cheap is not None:
                return cheap

        features = snapshots.optional_features()
        reason = snapshots.unavailable("optional_features")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Telnet Client", f"Could not read: {reason}")]}
        state = features.get("telnetclient")
        if state is None:
            return {"status": "Not Installed", "color": "green", "available": True,
                    "details": [("Telnet Client", "Feature not present")], "enabled": False}
        enabled = "Enabled" in state
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "red" if enabled else "green", "available": True,
                "details": [("Telnet Client", state)], "enabled": enabled}
    except Exception:
        # No reading was taken; `available: False` keeps read() from falling
        # through to read_value and inventing one.
        return {"status": "Error", "color": "amber", "available": False,
                "details": []}

def check_smb_signing() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters", "RequireSecuritySignature")
        if val is None:
            return {"status": "Not Required", "color": "red",
                    "details": [("SMB Signing", "Not configured — not required")], "enabled": False}
        required = val == 1
        return {"status": "Required" if required else "Not Required",
                "color": "green" if required else "red",
                "details": [("SMB Signing Required", "Yes" if required else "No")], "enabled": required}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ntlm_level() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa", "LmCompatibilityLevel")
        if val is None:
            # No value means Windows' own default, level 3.
            return {"status": "Not Configured", "color": "amber",
                    "available": True, "level": 3,
                    "details": [("NTLM Level", "Default (send NTLMv2)")]}
        labels = {0: "Send LM & NTLM", 1: "Negotiate", 2: "Send NTLM only",
                  3: "NTLMv2 only", 4: "NTLMv2, refuse LM", 5: "NTLMv2, refuse LM & NTLM"}
        label = labels.get(val, f"Unknown ({val})")
        color = "green" if val >= 5 else ("green" if val >= 3 else ("amber" if val >= 1 else "red"))
        return {"available": True, "level": val,
                "status": f"Level {val} — {label}", "color": color,
                "details": [("LM Compatibility Level", f"Level {val} — {label}")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_network_profile() -> Dict[str, Any]:
    try:
        rc, out, err = _ps("Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select -ExpandProperty NetworkCategory -First 1", timeout=20)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Network Profile",
                                 f"Could not read: {(err or '').strip()[:60]}")]}
        # PowerShell renders the enum by NAME here, so the numeric table this
        # used to consult never matched and every machine read
        # "Unknown (Private)". Both forms are accepted now.
        cat = out.strip()
        by_number = {"0": "Public", "1": "Private", "2": "DomainAuthenticated"}
        label = by_number.get(cat, cat)
        public = label == "Public"
        return {"status": "Public (most secure)" if public else label,
                "color": "green" if public else "amber",
                "available": True, "category": label,
                "details": [("Network Category", label)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

#: Get-NetFirewallProfile's DefaultInbound/OutboundAction, read off the live
#: cmdlet's own enum: NotConfigured=0, Allow=2, Block=4. Nothing maps 0 to
#: Allow -- NotConfigured means Windows' default applies, and reading 0 as
#: "allow" put a red "Inbound Allowed" on every machine that had never had
#: an explicit policy set. The label stays direction-agnostic here because
#: Windows' own default is OPPOSITE by direction (block inbound, allow
#: outbound) -- an earlier version hardcoded "(default: block)" onto this
#: dict entry and displayed that same wrong claim on the OUTBOUND row too;
#: `_firewall_default_hint` below adds the correct direction-specific
#: annotation instead.
_FIREWALL_ACTIONS = {0: "Not configured", 2: "Allow", 4: "Block"}


def _firewall_action(raw) -> str:
    if isinstance(raw, str) and not raw.isdigit():
        return raw
    try:
        return _FIREWALL_ACTIONS.get(int(raw), f"Unknown ({raw})")
    except (TypeError, ValueError):
        return f"Unknown ({raw})"


def _firewall_default_hint(action: str, direction_default: str) -> str:
    """`action` with its effective meaning spelled out when Windows' own
    default applies -- `direction_default` is "block" for inbound, "allow"
    for outbound."""
    return f"{action} (default: {direction_default})" if action == "Not configured" else action


def check_firewall_stealth() -> Dict[str, Any]:
    try:
        rc, out, err = _ps("Get-NetFirewallProfile | Select Name,DefaultInboundAction,DefaultOutboundAction | ConvertTo-Json -Compress", timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Firewall profiles",
                                 f"Could not read: {(err or out or '').strip()[:80]}")]}
        data = json.loads(out) if out.strip().startswith("[") else [json.loads(out)]
        details, allowed = [], []
        for prof in data:
            name = prof.get("Name", "?")
            ib = _firewall_action(prof.get("DefaultInboundAction"))
            ob = _firewall_action(prof.get("DefaultOutboundAction"))
            details.append((f"{name} Inbound", _firewall_default_hint(ib, "block")))
            details.append((f"{name} Outbound", _firewall_default_hint(ob, "allow")))
            if ib == "Allow":
                allowed.append(name)
        stealth_ok = not allowed
        return {"status": ("Inbound Blocked (Stealth)" if stealth_ok
                           else "Inbound Allowed on " + ", ".join(allowed)),
                "color": "green" if stealth_ok else "red",
                "available": True, "enabled": stealth_ok, "details": details}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}


def check_firewall_outbound() -> Dict[str, Any]:
    """Default outbound action per profile.

    The OPPOSITE trap from `check_firewall_stealth`'s inbound reading:
    "NotConfigured" means Windows' own default applies, and for OUTBOUND
    that default is ALLOW -- confirmed live, this machine's three profiles
    all report NotConfigured for DefaultOutboundAction, and Windows Firewall
    does not restrict outbound traffic in that state. Reusing the inbound
    comparison (`action == "Allow"`) here would read NotConfigured as
    "blocked" and put a green "Outbound Blocked" on a machine that in fact
    allows everything out -- so only an explicit "Block" counts here.
    """
    try:
        rc, out, err = _ps(
            "Get-NetFirewallProfile | Select Name,DefaultOutboundAction | ConvertTo-Json -Compress", timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Firewall profiles",
                                 f"Could not read: {(err or out or '').strip()[:80]}")]}
        data = json.loads(out) if out.strip().startswith("[") else [json.loads(out)]
        details, open_profiles = [], []
        for prof in data:
            name = prof.get("Name", "?")
            action = _firewall_action(prof.get("DefaultOutboundAction"))
            blocked = action == "Block"
            details.append((name, _firewall_default_hint(action, "allow")))
            if not blocked:
                open_profiles.append(name)
        all_blocked = bool(data) and not open_profiles
        return {"status": ("Outbound Blocked (all profiles)" if all_blocked
                           else "Outbound Allowed on " + ", ".join(open_profiles)),
                "color": "green" if all_blocked else "amber",
                "available": True, "enabled": all_blocked, "details": details}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}


#: HKLM\...\FirewallPolicy is the same branch CLAUDE.md already documents as
#: readable unelevated for firewall rules -- confirmed live 2026-09-28,
#: unelevated, for the `Logging` subkey too: all three profiles read
#: `LogDroppedPackets`=0 (Windows' own shipped default -- logging is off out
#: of the box). Registry, not `Get-NetFirewallProfile`, for the same reason
#: every other writable control in this file is registry-first: BackupService
#: can restore a DWORD exactly and cannot revert a script.
_FIREWALL_LOGGING_KEYS = {
    "Domain": r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile\Logging",
    "Private": r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile\Logging",
    "Public": r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile\Logging",
}


def check_firewall_logging() -> Dict[str, Any]:
    """Whether a connection the firewall dropped is written to pfirewall.log.

    Distinct from every other firewall control here: those decide what gets
    through, this decides whether a block leaves any trace at all. Measured
    live 2026-09-28 (unelevated): all three profiles' `LogDroppedPackets` DWORD
    is absent, which is Windows' own default of 0/off -- a probe the firewall
    blocked today is gone the moment the packet is dropped, nothing to review
    after the fact. A missing value is a definite "off", the same reading
    `check_admin_shares`/`check_smb_signing` give their own absent DWORDs, not
    a refusal -- this branch is confirmed readable without elevation.
    """
    try:
        details, off_profiles = [], []
        for name, key in _FIREWALL_LOGGING_KEYS.items():
            val = _reg_read(key, "LogDroppedPackets")
            logging_on = val == 1
            details.append((f"{name} dropped-packet logging",
                            "On" if logging_on else "Off"))
            if not logging_on:
                off_profiles.append(name)
        all_on = not off_profiles
        return {"status": ("Logging blocked connections (all profiles)" if all_on
                           else "Not logging on " + ", ".join(off_profiles)),
                "color": "green" if all_on else "amber",
                "available": True, "enabled": all_on, "details": details}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}


def check_listening_ports() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["netstat", "-an"], timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "details": [("Ports", "Could not query")]}
        lines = [line for line in out.splitlines() if "LISTENING" in line.upper()]
        count = len(lines)
        color = "green" if count <= 20 else ("amber" if count <= 40 else "red")
        return {"status": f"{count} listening", "color": color,
                "details": [("Listening Ports", str(count))]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_rdp_nla() -> Dict[str, Any]:
    try:
        key = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
        val = _reg_read(key, "UserAuthentication")
        if val == 1:
            return {"status": "NLA Required", "color": "green",
                    "details": [("RDP NLA", "On — auth before session")], "enabled": True}
        elif val == 0:
            return {"status": "NLA Not Required", "color": "red",
                    "details": [("RDP NLA", "Off — MitM risk")], "enabled": False}
        return {"status": "Not Configured", "color": "amber", "details": [("RDP NLA", "Not set")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_anonymous_restrict() -> Dict[str, Any]:
    try:
        key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
        ra = _reg_read(key, "RestrictAnonymous")
        ras = _reg_read(key, "RestrictAnonymousSAM")
        details = [("RestrictAnonymous", "On" if ra == 1 else "Off"),
                   ("RestrictAnonymousSAM", "On" if ras == 1 else "Off")]
        if ra == 1 and ras == 1:
            return {"status": "Fully Restricted", "color": "green",
                    "available": True, "enabled": True, "details": details}
        return {"status": "Partially Restricted" if (ra == 1 or ras == 1) else "Not Restricted",
                "color": "amber" if (ra == 1 or ras == 1) else "red",
                "available": True, "enabled": False, "details": details}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_admin_shares() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "AutoShareWks")
        if val == 0:
            return {"status": "Disabled", "color": "green",
                    "details": [("Admin Shares (C$, ADMIN$)", "Off")], "enabled": False}
        return {"status": "Enabled", "color": "red",
                "details": [("Admin Shares (C$, ADMIN$)", "On — lateral movement risk")], "enabled": True}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_autorun() -> Dict[str, Any]:
    """Whether AutoRun can still run something from a drive.

    `enabled` means "AutoRun can run", and it used to be present only on the
    fully-disabled path -- so a machine where AutoRun was live read None, and
    the catalog could not tell that apart from a refusal.
    """
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
                        r"\Policies\Explorer", "NoDriveTypeAutoRun")
        if val == 255:
            return {"status": "Disabled (All)", "color": "green",
                    "available": True, "enabled": False,
                    "details": [("AutoRun", "Off on all drive types")]}
        if val is not None:
            return {"status": f"Partial (value={val})", "color": "amber",
                    "available": True, "enabled": True,
                    "details": [("AutoRun", f"NoDriveTypeAutoRun = {val}")]}
        return {"status": "Not Configured", "color": "red",
                "available": True, "enabled": True,
                "details": [("AutoRun", "No policy set, so AutoRun can run")]}
    except Exception:
        return {"status": "Error", "color": "amber", "available": False,
                "details": []}


def check_usb_storage() -> Dict[str, Any]:
    """Whether Windows will mount a USB mass-storage device at all.

    `Start` under the USBSTOR driver's own service key is the same value
    `sc config USBSTOR start=` writes: 3 lets it load on demand (the
    out-of-box default, since Windows 2000/XP), 4 disables it -- Explorer
    never sees the drive at all, not even to show it as inaccessible. This
    is the standard data-exfiltration/BadUSB control, distinct from
    `check_autorun` (which only stops autorun.inf from firing on media
    Windows already mounted).

    Measured live on this machine 2026-09-29, unelevated: Start=3, i.e. USB
    storage is allowed -- the Windows default, not itself a finding.

    `enabled` follows the same polarity as `check_autorun`: True means "USB
    storage CAN be used" (the permissive/bad state), so the catalog's
    `desired=False` asks for it to be blocked.
    """
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\USBSTOR"
    hive_name, _, sub = key.partition("\\")
    hive = _HIVES.get(hive_name.upper())
    try:
        with winreg.OpenKey(hive, sub) as handle:
            start, _ = winreg.QueryValueEx(handle, "Start")
    except FileNotFoundError:
        # USBSTOR not existing at all would mean no USB mass-storage class
        # driver is installed on this machine -- unusual enough that it is
        # reported as a definite state (nothing to block) rather than
        # guessed at.
        return {"status": "Not Found", "color": "green", "available": True,
                "enabled": False,
                "details": [("USBSTOR", "Service key not present")]}
    except PermissionError as exc:
        logger.warning("check_usb_storage: access denied reading Start: %s",
                        exc)
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("USBSTOR", "Access denied reading Start value")]}
    except OSError as exc:
        logger.warning("check_usb_storage: registry read failed: %s", exc)
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("USBSTOR", f"Could not read: {exc}")]}
    blocked = start == 4
    return {
        "status": "Blocked" if blocked else "Allowed",
        "color": "green" if blocked else "amber",
        "available": True, "enabled": not blocked,
        "details": [("USBSTOR Start", str(start)),
                     ("USB Mass Storage",
                      "Blocked -- drives will not mount" if blocked
                      else "Allowed -- any USB drive can be read/written")],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY C — SYSTEM HARDENING (16 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def check_powershell_v2() -> Dict[str, Any]:
    try:
        features = snapshots.optional_features()
        reason = snapshots.unavailable("optional_features")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("PowerShell v2", f"Could not read: {reason}")]}
        state = features.get("microsoftwindowspowershellv2root")
        if state is None:
            return {"status": "Not Installed", "color": "green", "available": True,
                    "details": [("PowerShell v2", "Not present")], "enabled": False}
        enabled = "Enabled" in state
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "red" if enabled else "green", "available": True,
                "details": [("PowerShell v2 Engine", "Enabled (downgrade risk)" if enabled else "Disabled")],
                "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ps_constrained_lang() -> Dict[str, Any]:
    try:
        rc, out, _ = _ps("$ExecutionContext.SessionState.LanguageMode", timeout=10)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "details": [("Language Mode", "Could not query")]}
        mode = out.strip()
        constrained = mode == "ConstrainedLanguage"
        return {"status": mode, "color": "green" if constrained else "amber",
                "details": [("PS Language Mode", mode)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ps_execution_policy() -> Dict[str, Any]:
    """The machine's execution policy, not this process's.

    `Get-ExecutionPolicy` with no scope returns the EFFECTIVE policy of the
    shell it runs in. Run from a launcher started with -ExecutionPolicy
    Bypass, this reported "Bypass" -- a fact about its own launcher. The
    control writes LocalMachine, so LocalMachine is what must be read back.
    """
    try:
        rc, out, _ = _ps("Get-ExecutionPolicy -Scope LocalMachine", timeout=10)
        if rc != 0 or not out or not out.strip():
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Execution Policy", "Could not read")]}
        policy = out.strip()
        color = ("green" if "Restricted" in policy or "AllSigned" in policy
                 else ("amber" if "RemoteSigned" in policy else "red"))
        return {"status": policy, "color": color, "available": True,
                "policy": policy,
                "details": [("PowerShell Execution Policy (LocalMachine)",
                             policy)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ps_script_block_logging() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging", "EnableScriptBlockLogging")
        if val == 1:
            return {"status": "Enabled", "color": "green",
                    "details": [("Script Block Logging", "On")], "enabled": True}
        return {"status": "Disabled", "color": "red",
                "details": [("Script Block Logging", "Off")], "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ps_transcription() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\Transcription", "EnableTranscripting")
        if val == 1:
            return {"status": "Enabled", "color": "green",
                    "details": [("PS Transcription", "On")], "enabled": True}
        return {"status": "Disabled", "color": "amber",
                "details": [("PS Transcription", "Off (not configured)")], "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_wdigest() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest", "UseLogonCredential")
        if val is None:
            # Absent is the SECURE state on 8.1 and later: WDigest does
            # not cache plaintext unless this value turns it back on.
            return {"status": "Not Configured (secure default)",
                    "color": "green", "available": True,
                    "enabled": False,
                    "details": [("WDigest", "Key not present")]}
        if val == 0:
            return {"status": "Disabled (secure)", "color": "green",
                    "available": True,
                    "details": [("WDigest Caching", "Off — credentials not cached")], "enabled": False}
        return {"status": "Enabled (insecure)", "color": "red",
                "available": True,
                "details": [("WDigest Caching", "On — credentials cached")], "enabled": True}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_cached_logons() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", "CachedLogonsCount", "REG_SZ")
        if val is None:
            return {"status": "10 (default)", "color": "amber",
                    "available": True, "count": 10,
                    "details": [("Cached Logons", "Default: 10")]}
        count = int(val) if str(val).isdigit() else 10
        color = "green" if count <= 2 else ("amber" if count <= 10 else "red")
        return {"status": f"{count} cached logons", "color": color,
                "available": True, "count": count,
                "details": [("Cached Logon Count", str(count))]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_account_lockout() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["net", "accounts"], timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "details": [("Account Lockout", "Could not query")]}
        threshold = "Never"
        for line in out.splitlines():
            if "Lockout threshold" in line:
                threshold = line.split(":", 1)[-1].strip()
        if threshold.lower() == "never":
            return {"status": "Never (disabled)", "color": "red",
                    "details": [("Lockout Threshold", "Never — brute-force risk")], "enabled": False}
        return {"status": f"{threshold} attempts", "color": "green",
                "details": [("Lockout Threshold", threshold)], "enabled": True}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_password_min_length() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["net", "accounts"], timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber",
                    "available": False,
                    "details": [("Password Min Length", "Unknown")]}
        min_len = 0
        for line in out.splitlines():
            if "Minimum password length" in line:
                raw = line.split(":", 1)[-1].strip()
                min_len = int(raw) if raw.isdigit() else 0
        color = "green" if min_len >= 14 else ("amber" if min_len >= 8 else "red")
        return {"status": f"{min_len} chars", "color": color,
                "available": True, "length": min_len,
                "details": [("Minimum Password Length", str(min_len))]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_pagefile_clear() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management", "ClearPageFileAtShutdown")
        if val == 1:
            return {"status": "Enabled", "color": "green",
                    "details": [("Pagefile Clear", "On — wiped on restart")], "enabled": True}
        return {"status": "Disabled", "color": "amber",
                "details": [("Pagefile Clear", "Off — data may persist")], "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ctrl_alt_del() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "DisableCAD")
        if val == 0:
            return {"status": "Required", "color": "green",
                    "details": [("Ctrl+Alt+Del", "Required for logon")], "enabled": True}
        elif val == 1:
            return {"status": "Not Required", "color": "amber",
                    "details": [("Ctrl+Alt+Del", "Not required")], "enabled": False}
        # Not configured means CAD is not required at the logon screen,
        # which is Windows' default outside a domain.
        return {"status": "Not Configured", "color": "amber",
                "available": True, "enabled": False,
                "details": [("Ctrl+Alt+Del", "Not configured")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY D — FEATURES & MISC (12 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def check_sandbox() -> Dict[str, Any]:
    try:
        features = snapshots.optional_features()
        reason = snapshots.unavailable("optional_features")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Sandbox", f"Could not read: {reason}")]}
        state = features.get("containers-disposableclientvm")
        if state is None:
            return {"status": "Not Installed", "color": "amber", "available": True,
                    "details": [("Sandbox", "Feature not present")], "enabled": False}
        enabled = "Enabled" in state
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "green" if enabled else "amber", "available": True,
                "details": [("Windows Sandbox", state)], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_hyperv() -> Dict[str, Any]:
    try:
        features = snapshots.optional_features()
        reason = snapshots.unavailable("optional_features")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Hyper-V", f"Could not read: {reason}")]}
        state = features.get("microsoft-hyper-v-all")
        if state is None:
            return {"status": "Not Installed", "color": "amber", "available": True,
                    "details": [("Hyper-V", "Feature not present")], "enabled": False}
        enabled = "Enabled" in state
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "green" if enabled else "amber", "available": True,
                "details": [("Hyper-V Platform", state)], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_wsl() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["wsl", "--status"], timeout=20)
        if rc != 0:
            return {"status": "Not Installed", "color": "amber",
                    "details": [("WSL", "Not installed")], "enabled": False}
        details = [("WSL", "Installed")]
        for line in out.splitlines():
            if ":" in line:
                k, v = line.strip().split(":", 1)
                details.append((k.strip(), v.strip()))
        v2 = any("version: 2" in line.lower() or "wsl2" in line.lower() for line in out.splitlines())
        return {"status": "Installed (WSL2)" if v2 else "Installed",
                "color": "green", "details": details, "enabled": True}
    except FileNotFoundError:
        return {"status": "Not Installed", "color": "amber",
                "details": [("WSL", "Not installed")], "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_audit_policy() -> Dict[str, Any]:
    """How many audit subcategories are recording anything.

    auditpol prints one line per subcategory ending in Success, Failure,
    "Success and Failure" or "No Auditing". The word "Enabled" never appears
    in its output, and counting lines that contained it therefore counted
    zero on every machine and rendered "No Categories Enabled" in red -- on
    this one, elevated, 12 of 60 subcategories are auditing.
    """
    try:
        rc, out, _ = _cmd_run(["auditpol", "/get", "/category:*"], timeout=30)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Audit Policy",
                                 "auditpol needs administrator rights")]}
        subcategories = [line for line in out.splitlines()
                         if line.startswith("  ") and line.strip()]
        audited = [line for line in subcategories
                   if "No Auditing" not in line]
        total = len(subcategories)
        count = len(audited)
        if count == 0:
            color, status = "red", "Nothing is audited"
        elif count < 10:
            color, status = "amber", f"{count} of {total} subcategories audited"
        else:
            color, status = "green", f"{count} of {total} subcategories audited"
        return {"status": status, "color": color, "available": True,
                "audited": count, "total": total, "enabled": count > 0,
                "details": [("Subcategories auditing", f"{count} of {total}")]
                           + [(line.strip()[:38].strip(), line.strip()[38:].strip())
                              for line in audited[:8]]}
    except Exception:
        return {"status": "Error", "color": "amber", "available": False,
                "details": []}

def check_tpm_details() -> Dict[str, Any]:
    """TPM state, or an honest refusal — never a claim about absent hardware.

    `Get-Tpm` needs administrator rights and, unelevated, **exits 0** while
    answering `{"TpmPresent":null,"TpmReady":null,"TpmEnabled":null}`. The
    `rc != 0` guard therefore never fired, and `bool(None)` is `False`, so a
    refusal came out as a red "No TPM — Not present" on a machine whose TPM 2.0
    was confirmed elevated on 2026-08-22. A check that could not run is
    Unknown, not False; `rc` is not a success signal for this cmdlet.
    """
    try:
        rc, out, _ = _ps("Get-Tpm | Select TpmPresent, TpmReady, TpmEnabled | ConvertTo-Json -Compress", timeout=15)
        if rc != 0 or not out:
            return {"status": f"Unknown — {NEEDS_ADMIN}", "color": "amber",
                    "details": [("TPM", NEEDS_ADMIN)]}
        data = json.loads(out)
        present = data.get("TpmPresent")
        if present is None:
            return {"status": f"Unknown — {NEEDS_ADMIN}", "color": "amber",
                    "details": [("TPM", NEEDS_ADMIN)]}
        ready = bool(data.get("TpmReady", False))
        enabled = bool(data.get("TpmEnabled", False))
        if not present:
            return {"status": "No TPM", "color": "red", "details": [("TPM", "Not present")]}
        return {"status": "Ready" if ready else "Present (not ready)", "color": "green" if ready else "amber",
                "details": [("TPM Ready", str(ready)), ("TPM Enabled", str(enabled))], "enabled": ready}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_bios_mode() -> Dict[str, Any]:
    try:
        uefi = os.path.exists(r"C:\Windows\System32\winload.efi")
        return {"status": "UEFI" if uefi else "Legacy BIOS",
                "color": "green" if uefi else "amber",
                "details": [("Firmware", "UEFI" if uefi else "Legacy")], "enabled": uefi}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_test_signing() -> Dict[str, Any]:
    """Whether the boot configuration allows test-signed drivers.

    This used to be `"testsigning" in out and "yes" in out`, matched against
    the WHOLE of bcdedit's output -- and `recoveryenabled Yes` is on nearly
    every machine, so an explicit `testsigning No` rendered as a red
    "Enabled (DANGER)". The flag's own line is what decides.
    """
    try:
        rc, out, err = _cmd_run(["bcdedit", "/enum"], timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Test Signing",
                                 "bcdedit needs administrator rights")]}
        value = None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == "testsigning":
                value = parts[1].lower()
                break
        if value is None:
            # bcdedit omits the flag entirely when it has never been set,
            # which is Windows' default: off.
            return {"status": "Disabled (not set)", "color": "green",
                    "available": True, "enabled": False,
                    "details": [("Test Signing", "Not present in the BCD")]}
        on = value == "yes"
        return {"status": "Enabled (DANGER)" if on else "Disabled",
                "color": "red" if on else "green",
                "available": True, "enabled": on,
                "details": [("Test Signing",
                             "On, unsigned drivers may load" if on else "Off")]}
    except Exception:
        return {"status": "Error", "color": "amber", "available": False,
                "details": []}

def check_windows_version() -> Dict[str, Any]:
    try:
        rc, out, _ = _ps("Get-ComputerInfo -Property OsBuildNumber | Select OsBuildNumber | ConvertTo-Json -Compress", timeout=20)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "details": [("Version", "Unknown")]}
        data = json.loads(out)
        build = str(data.get("OsBuildNumber", "?"))
        return {"status": f"Build {build}", "color": "green",
                "details": [("OS Build", build)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_wu_service() -> Dict[str, Any]:
    """The Windows Update service.

    It is trigger-started, so "Stopped" is its normal state and only
    "Disabled" is a finding -- which is why this carries `disabled` as well as
    `enabled`, and why the catalog control compares against `disabled`.
    """
    try:
        services = snapshots.service_states()
        reason = snapshots.unavailable("service_states")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("WU Service", f"Could not read: {reason}")]}
        info = services.get("wuauserv")
        if info is None:
            return {"status": "Service Not Found", "color": "red",
                    "available": True, "enabled": False, "disabled": True,
                    "details": [("WU Service", "Not found")]}
        facts = _service_facts(info)
        return {"status": facts["status_label"],
                "color": "red" if facts["disabled"] else "green",
                "available": True, "enabled": facts["running"],
                "disabled": facts["disabled"],
                "start_type": facts["start_label"],
                "details": [("Windows Update Service", facts["status_label"]),
                            ("Start Type", facts["start_label"])]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_wu_auto_update() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU", "AUOptions")
        if val is None:
            return {"status": "Default (auto-install)", "color": "green",
                    "details": [("Auto Update", "Default policy")]}
        labels = {2: "Notify before download", 3: "Auto download, notify",
                  4: "Auto install", 5: "Admin chooses"}
        label = labels.get(val, f"Unknown ({val})")
        return {"status": label, "color": "green" if val in (3, 4) else "amber",
                "details": [("Auto Update Policy", label)]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_dump_encryption() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Control\CrashControl", "EncryptionEnabled")
        if val is None:
            return {"status": "Not Supported", "color": "amber",
                    "details": [("Crash Dump Encryption", "Feature not present")]}
        return {"status": "Enabled" if val == 1 else "Disabled",
                "color": "green" if val == 1 else "amber",
                "details": [("Crash Dump Encryption", "On" if val == 1 else "Off")], "enabled": val == 1}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

#: Get-BitLockerVolume serialises these as INTEGERS too, and the card used to
#: render them literally: "Drive C:  0 (0)".
_BITLOCKER_VOLUME_STATUS = {0: "Fully Decrypted", 1: "Fully Encrypted",
                            2: "Encryption In Progress",
                            3: "Decryption In Progress",
                            4: "Encryption Suspended",
                            5: "Decryption Suspended"}
_BITLOCKER_METHODS = {0: "None", 1: "AES 128 with Diffuser",
                      2: "AES 256 with Diffuser", 3: "AES 128", 4: "AES 256",
                      5: "Hardware Encryption", 6: "XTS-AES 128",
                      7: "XTS-AES 256"}


def _bitlocker_label(table: Dict[int, str], raw) -> str:
    if isinstance(raw, str) and not raw.isdigit():
        return raw
    try:
        return table.get(int(raw), f"Unknown ({raw})")
    except (TypeError, ValueError):
        return str(raw)


def check_bitlocker_encryption() -> Dict[str, Any]:
    if not is_admin():
        # Get-BitLockerVolume takes a measured 5.41s to refuse an ordinary
        # user — and refuses by exiting 0 with empty stdout, so the wait buys
        # nothing but the answer we already have.
        return {"status": NEEDS_ADMIN, "color": "amber", "available": False,
                "details": [("BitLocker", NEEDS_ADMIN)]}
    try:
        rc, out, err = _ps("Get-BitLockerVolume | Select MountPoint,EncryptionMethod,VolumeStatus,ProtectionStatus | ConvertTo-Json -Compress", timeout=30)
        # Refused, this cmdlet ALSO exits 0: empty stdout and
        # "Get-CimInstance : Access denied" on stderr. An empty answer from a
        # query that was not allowed to run is not an absence of volumes.
        if "access denied" in (err or "").lower():
            return {"status": NEEDS_ADMIN, "color": "amber", "available": False,
                    "details": [("BitLocker", NEEDS_ADMIN)]}
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("BitLocker", "No answer from Get-BitLockerVolume")]}
        data = json.loads(out) if out.strip().startswith("[") else [json.loads(out)]
        details, c_protected = [], False
        for vol in data:
            mp = vol.get("MountPoint", "?")
            method = _bitlocker_label(_BITLOCKER_METHODS,
                                      vol.get("EncryptionMethod"))
            vstat = _bitlocker_label(_BITLOCKER_VOLUME_STATUS,
                                     vol.get("VolumeStatus"))
            prot = vol.get("ProtectionStatus", 0)
            details.append((f"Drive {mp}", f"{vstat} ({method})"))
            if str(mp).upper() == "C:" and prot == 1:
                c_protected = True
        return {"status": "C: Protected" if c_protected else "C: Not Protected",
                "color": "green" if c_protected else "red",
                "available": True, "enabled": c_protected, "details": details}
    except Exception:
        return {"status": "Error", "color": "amber", "available": False,
                "details": []}


# ═══════════════════════════════════════════════════════════════════════════════
# Toggle helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _get_mp_pref_value(pref_name: str) -> Optional[int]:
    try:
        return snapshots.mp_preference().get(pref_name)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Additional toggle functions (15)
# ═══════════════════════════════════════════════════════════════════════════════

def set_defender_behavior_monitoring(enabled: bool) -> Dict[str, Any]:
    before_raw = _get_mp_pref_value("DisableBehaviorMonitoring")
    before_val = None if before_raw is None else (before_raw == 0)
    result = _set_mp_pref("DisableBehaviorMonitoring", 0 if enabled else 1, "Behaviour monitoring")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_script_scanning(enabled: bool) -> Dict[str, Any]:
    before_raw = _get_mp_pref_value("DisableScriptScanning")
    before_val = None if before_raw is None else (before_raw == 0)
    result = _set_mp_pref("DisableScriptScanning", 0 if enabled else 1, "Script scanning")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_email_scanning(enabled: bool) -> Dict[str, Any]:
    before_raw = _get_mp_pref_value("DisableEmailScanning")
    before_val = None if before_raw is None else (before_raw == 0)
    result = _set_mp_pref("DisableEmailScanning", 0 if enabled else 1, "Email scanning")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_archive_scanning(enabled: bool) -> Dict[str, Any]:
    before_raw = _get_mp_pref_value("DisableArchiveScanning")
    before_val = None if before_raw is None else (before_raw == 0)
    result = _set_mp_pref("DisableArchiveScanning", 0 if enabled else 1, "Archive scanning")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_ioav(enabled: bool) -> Dict[str, Any]:
    before_raw = _get_mp_pref_value("DisableIOAVProtection")
    before_val = None if before_raw is None else (before_raw == 0)
    result = _set_mp_pref("DisableIOAVProtection", 0 if enabled else 1, "IOAV protection")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_removable_drive_scanning(enabled: bool) -> Dict[str, Any]:
    before_raw = _get_mp_pref_value("DisableRemovableDriveScanning")
    before_val = None if before_raw is None else (before_raw == 0)
    result = _set_mp_pref("DisableRemovableDriveScanning", 0 if enabled else 1, "Removable drive scanning")
    result["before_value"] = before_val
    result["after_value"] = enabled
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_catchup_scans(enabled: bool) -> Dict[str, Any]:
    before_quick = _get_mp_pref_value("DisableCatchupQuickScan")
    before_full = _get_mp_pref_value("DisableCatchupFullScan")
    before_val = {"quick": None if before_quick is None else (before_quick == 0),
                  "full": None if before_full is None else (before_full == 0)}
    new_val = 0 if enabled else 1
    r1 = _set_mp_pref("DisableCatchupQuickScan", new_val, "Catch-up quick scan")
    r2 = _set_mp_pref("DisableCatchupFullScan", new_val, "Catch-up full scan")
    success = r1["success"] and r2["success"]
    return {"success": success, "message": f"Catch-up scans {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": enabled,
            "action": "enable" if enabled else "disable"}

def set_defender_cloud_block_level(level: int) -> Dict[str, Any]:
    if level not in (0, 2, 4, 6):
        return {"success": False, "message": f"Invalid level: {level}. Use 0, 2, 4, or 6.",
                "before_value": None, "after_value": None, "action": "set_cloud_block_level"}
    before_val = _get_mp_pref_value("CloudBlockLevel")
    result = _set_mp_pref("CloudBlockLevel", level, f"Cloud block level ({level})")
    result["before_value"] = before_val
    result["after_value"] = level
    result["action"] = "set_cloud_block_level"
    return result

def set_llmnr(enabled: bool) -> Dict[str, Any]:
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"
    before_val = _reg_read(key, "EnableMulticast")
    ok = _reg_write(key, "EnableMulticast", 1 if enabled else 0)
    return {"success": ok, "message": f"LLMNR {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": 1 if enabled else 0,
            "action": "enable" if enabled else "disable"}

def set_wpad(enabled: bool) -> Dict[str, Any]:
    key = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings"
    before_val = _reg_read(key, "AutoDetect")
    ok = _reg_write(key, "AutoDetect", 1 if enabled else 0)
    return {"success": ok, "message": f"WPAD {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": 1 if enabled else 0,
            "action": "enable" if enabled else "disable"}

def set_wdigest_credential_caching(enabled: bool) -> Dict[str, Any]:
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest"
    before_val = _reg_read(key, "UseLogonCredential")
    ok = _reg_write(key, "UseLogonCredential", 1 if enabled else 0)
    return {"success": ok, "message": f"WDigest caching {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": 1 if enabled else 0,
            "action": "enable" if enabled else "disable"}

def set_ntlm_level(level: int) -> Dict[str, Any]:
    if not isinstance(level, int) or level < 0 or level > 5:
        return {"success": False, "message": f"Invalid NTLM level: {level}. Must be 0-5.",
                "before_value": None, "after_value": None, "action": "set_ntlm_level"}
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
    before_val = _reg_read(key, "LmCompatibilityLevel")
    ok = _reg_write(key, "LmCompatibilityLevel", level)
    return {"success": ok, "message": f"NTLM level set to {level}",
            "before_value": before_val, "after_value": level,
            "action": "set_ntlm_level"}

def set_cached_logons(count: int) -> Dict[str, Any]:
    if not isinstance(count, int) or count < 0 or count > 50:
        return {"success": False, "message": f"Invalid count: {count}. Must be 0-50.",
                "before_value": None, "after_value": None, "action": "set_cached_logons"}
    key = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    before_val = _reg_read(key, "CachedLogonsCount", "REG_SZ")
    ok = _reg_write(key, "CachedLogonsCount", str(count), "REG_SZ")
    return {"success": ok, "message": f"Cached logons set to {count}",
            "before_value": before_val, "after_value": count, "action": "set_cached_logons"}

def set_pagefile_clear(enabled: bool) -> Dict[str, Any]:
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
    before_val = _reg_read(key, "ClearPageFileAtShutdown")
    ok = _reg_write(key, "ClearPageFileAtShutdown", 1 if enabled else 0)
    return {"success": ok, "message": f"Pagefile clear {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": 1 if enabled else 0,
            "action": "enable" if enabled else "disable"}

def set_ps_script_block_logging(enabled: bool) -> Dict[str, Any]:
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging"
    before_val = _reg_read(key, "EnableScriptBlockLogging")
    ok = _reg_write(key, "EnableScriptBlockLogging", 1 if enabled else 0)
    return {"success": ok, "message": f"PS script block logging {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": 1 if enabled else 0,
            "action": "enable" if enabled else "disable"}


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY E — SERVICES STATUS (20 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def _check_service(service_name: str, display: str, good_running: bool = True) -> Dict[str, Any]:
    try:
        services = snapshots.service_states()
        reason = snapshots.unavailable("service_states")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(display, f"Could not read: {reason}")]}
        info = services.get(service_name.lower())
        if info is None:
            return {"status": "Not Found", "color": "amber", "available": True,
                    "details": [(display, "Service not found")],
                    "enabled": False, "disabled": True}
        facts = _service_facts(info)
        running = facts["running"]
        if running:
            color, status = ("green", "Running") if good_running else ("red", "Running")
        else:
            color, status = ("red", "Stopped") if good_running else ("green", "Stopped")
        return {"status": status, "color": color, "available": True,
                "enabled": running, "disabled": facts["disabled"],
                "start_type": facts["start_label"],
                "details": [(f"{display} Service",
                             f"{facts['status_label']} "
                             f"({facts['start_label']} start)")]}
    except Exception:
        return {"status": "Error", "color": "amber",
                "details": [(display, "Check failed")], "enabled": False}


def check_service_lanman_workstation() -> Dict[str, Any]:
    return _check_service("LanmanWorkstation", "Lanman Workstation", good_running=True)

def check_service_lanman_server() -> Dict[str, Any]:
    return _check_service("LanmanServer", "Lanman Server", good_running=False)

def check_service_xbox_game_save() -> Dict[str, Any]:
    return _check_service("XblGameSave", "Xbox Game Save", good_running=False)

def check_service_xbox_accessory() -> Dict[str, Any]:
    return _check_service("XboxGipSvc", "Xbox Accessory Management", good_running=False)

def check_service_diag_track() -> Dict[str, Any]:
    return _check_service("DiagTrack", "Connected User Experiences / Telemetry", good_running=False)

def check_service_maps_broker() -> Dict[str, Any]:
    return _check_service("MapsBroker", "Maps Broker", good_running=False)

def check_service_walletsvc() -> Dict[str, Any]:
    return _check_service("WalletService", "Wallet Service", good_running=False)

def check_service_fdrespub() -> Dict[str, Any]:
    return _check_service("FDResPub", "Function Discovery Publication", good_running=False)

def check_service_net_tcp_port_sharing() -> Dict[str, Any]:
    return _check_service("NetTcpPortSharing", "Net.Tcp Port Sharing", good_running=False)

def check_service_remote_access_connection() -> Dict[str, Any]:
    return _check_service("RasMan", "Remote Access Connection Manager", good_running=False)

def check_service_telephony() -> Dict[str, Any]:
    return _check_service("TapiSrv", "Telephony", good_running=False)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY F — SERVICE TOGGLES (10)
# ═══════════════════════════════════════════════════════════════════════════════

def _set_service_startup(svc: str, label: str, enabled: bool) -> Dict[str, Any]:
    startup = "Automatic" if enabled else "Disabled"
    try:
        info = snapshots.service_states().get(svc.lower())
        before_val = info.get("start_type", "Unknown") if info else None
    except Exception:
        before_val = None

    rc, out, err = _ps(
        f"$ErrorActionPreference='Stop'; try {{ Set-Service {svc} -StartupType {startup}; Write-Output 'SUCCESS' }} "
        f"catch {{ Write-Error $_.Exception.Message }}",
        timeout=30)
    success = rc == 0 and "SUCCESS" in out
    msg = f"{label} set to {startup}" if success else (err or out or f"Failed to set {label}")
    return {"success": success, "message": msg, "before_value": before_val,
            "after_value": startup, "action": "enable" if enabled else "disable"}


def set_service_wsearch(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("WSearch", "Windows Search", enabled)

def set_service_sysmain(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("SysMain", "SysMain / Superfetch", enabled)

def set_service_fax(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("Fax", "Fax", enabled)

def set_service_xbox_live(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("XboxNetApiSvc", "Xbox Live Networking", enabled)

def set_service_diag_track(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("DiagTrack", "Connected User Experiences", enabled)

def set_service_wpn(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("WpnService", "Push Notifications", enabled)

def set_service_maps_broker(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("MapsBroker", "Maps Broker", enabled)

def set_service_fdphost(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("fdPHost", "Function Discovery Provider", enabled)

def set_service_webclient(enabled: bool) -> Dict[str, Any]:
    return _set_service_startup("WebClient", "WebClient", enabled)

def set_service_remote_registry_disabled() -> Dict[str, Any]:
    try:
        info = snapshots.service_states().get("remoteregistry")
        before_val = info.get("start_type", "Unknown") if info else None
    except Exception:
        before_val = None
    rc, out, err = _cmd_run(["sc", "config", "RemoteRegistry", "start=", "disabled"], timeout=15)
    success = rc == 0
    msg = "RemoteRegistry set to disabled" if success else (err or out or "Failed to set RemoteRegistry")
    return {"success": success, "message": msg, "before_value": before_val,
            "after_value": "Disabled", "action": "disable"}


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY G — WINDOWS FEATURES (10 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def _check_win_feature(feature: str, label: str, good_enabled: bool = True) -> Dict[str, Any]:
    try:
        features = snapshots.optional_features()
        reason = snapshots.unavailable("optional_features")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(label, f"Could not read: {reason}")]}
        state = features.get(feature.lower())
        if state is None:
            return {"status": "Not Available", "color": "amber", "available": True,
                    "details": [(label, "Feature not found")], "enabled": False}
        enabled = "Enabled" in state
        if enabled:
            color, status = ("green", "Enabled") if good_enabled else ("red", "Enabled")
        else:
            color, status = ("red", "Disabled") if good_enabled else ("green", "Disabled")
        return {"status": status, "color": color, "available": True,
                "details": [(label, state)], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [(label, "Check failed")], "enabled": False}


def check_feature_windows_media_player() -> Dict[str, Any]:
    return _check_win_feature("MediaPlayback", "Windows Media Player", good_enabled=False)

def check_feature_work_folders() -> Dict[str, Any]:
    return _check_win_feature("WorkFolders-Client", "Work Folders Client", good_enabled=False)

def check_feature_print_xps() -> Dict[str, Any]:
    return _check_win_feature("Printing-XPSServices-Features", "XPS Document Services", good_enabled=False)

def check_feature_internet_explorer() -> Dict[str, Any]:
    return _check_win_feature("Internet-Explorer-Optional-amd64", "Internet Explorer 11", good_enabled=False)

def check_feature_netfx3() -> Dict[str, Any]:
    return _check_win_feature("NetFx3", ".NET Framework 3.5", good_enabled=True)

def check_feature_iis() -> Dict[str, Any]:
    return _check_win_feature("IIS-WebServerRole", "IIS Web Server", good_enabled=False)

def check_feature_windows_fax_scan() -> Dict[str, Any]:
    return _check_win_feature("Printing-Fax-Features", "Windows Fax & Scan", good_enabled=False)

def check_feature_simple_tcpip() -> Dict[str, Any]:
    return _check_win_feature("SimpleTCP", "Simple TCP/IP Services", good_enabled=False)

def check_feature_legacy_components() -> Dict[str, Any]:
    return _check_win_feature("LegacyComponents", "Legacy Components", good_enabled=False)

def check_feature_direct_play() -> Dict[str, Any]:
    return _check_win_feature("DirectPlay", "DirectPlay", good_enabled=False)


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY H — MISCELLANEOUS CHECKS (10)
# ═══════════════════════════════════════════════════════════════════════════════

def check_disk_cleanup_state() -> Dict[str, Any]:
    try:
        details = []
        rc, out, _ = _ps("Get-Command cleanmgr.exe -ErrorAction SilentlyContinue", timeout=10)
        cleanmgr_available = rc == 0
        details.append(("Disk Cleanup (cleanmgr)", "Available" if cleanmgr_available else "Not found"))
        val = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy",
                        "01", "REG_DWORD")
        if val is not None:
            sense_on = val == 1
            details.append(("Storage Sense", "On" if sense_on else "Off"))
        else:
            sense_on = None
            details.append(("Storage Sense", "Not configured"))
        if cleanmgr_available:
            return {"status": "Available", "color": "green", "details": details, "enabled": True}
        return {"status": "Limited", "color": "amber", "details": details, "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("Disk Cleanup", "Check failed")]}

def check_system_restore_disks() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["vssadmin", "list", "shadowstorage"], timeout=20)
        if rc != 0:
            return {"status": "N/A (not elevated?)", "color": "amber",
                    "details": [("Shadow Storage", "Could not query")]}
        details = []
        used_total = 0
        for line in out.splitlines():
            if "Used Shadow Copy Storage space" in line:
                try:
                    pct_str = line.split("(")[-1].split("%")[0].strip()
                    used_total = float(pct_str)
                except Exception:
                    logger.warning("Ignored Exception parsing vssadmin output", exc_info=True)
        if used_total > 0:
            details.append(("Shadow Storage Used", f"{used_total:.1f}%"))
            color = "green" if used_total < 50 else ("amber" if used_total < 80 else "red")
            return {"status": f"{used_total:.1f}% used", "color": color, "details": details, "enabled": True}
        details.append(("Shadow Storage", "0% used"))
        return {"status": "0% used", "color": "green", "details": details, "enabled": True}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("System Restore", "Check failed")]}

def check_hibernation() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["powercfg", "/hibernate"], timeout=15)
        if rc != 0:
            return {"status": "N/A", "color": "amber",
                    "details": [("Hibernation", f"Query returned code {rc}")], "enabled": False}
        details = []
        # powercfg /hibernate alone returns status; /availoffsets shows more
        rc2, out2, _ = _cmd_run(["powercfg", "/a"], timeout=15)
        hibernate_available = "hibernate" in out2.lower() if out2 else False
        details.append(("Hibernation Available", "Yes" if hibernate_available else "No"))
        if hibernate_available:
            rc3, out3, _ = _cmd_run(["powercfg", "/hibernate", "on"], timeout=10)
            details.append(("Hibernation Status", "Enabled"))
            return {"status": "Enabled", "color": "amber", "details": details, "enabled": True}
        return {"status": "Disabled / Not Available", "color": "green", "details": details, "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("Hibernation", "Check failed")]}

# Reports "Not Configured" (not Enabled/Disabled) when HiberbootEnabled is absent, because an
# absent value means the Windows default applies and that default differs by build.
def check_fast_startup() -> Dict[str, Any]:
    try:
        key = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
        val = _reg_read(key, "HiberbootEnabled")
        if val is None:
            return {"status": "Not Configured", "color": "amber",
                    "details": [("Fast Startup", "Not set (default enabled)")], "enabled": True}
        enabled = val == 1
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "green" if not enabled else "amber",
                "details": [("Fast Startup", "On" if enabled else "Off")], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("Fast Startup", "Check failed")]}

def check_core_isolation_summary() -> Dict[str, Any]:
    try:
        key = r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
        val = _reg_read(key, "Enabled")
        if val is None:
            return {"status": "Not Configured", "color": "amber",
                    "details": [("Core Isolation", "No registry entry")], "enabled": False}
        enabled = val == 1
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "green" if enabled else "red",
                "details": [("Core Isolation / Memory Integrity", "On" if enabled else "Off")], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("Core Isolation", "Check failed")]}

def check_memory_integrity_registry() -> Dict[str, Any]:
    try:
        key = r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
        val = _reg_read(key, "Enabled")
        if val is None:
            return {"status": "Not Configured", "color": "amber",
                    "details": [("Memory Integrity", "No registry policy")], "enabled": False}
        enabled = val == 1
        return {"status": "Enabled" if enabled else "Disabled",
                "color": "green" if enabled else "red",
                "details": [("HypervisorEnforcedCodeIntegrity\\Enabled", str(val))], "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("Memory Integrity", "Check failed")]}

#: Get-ProcessMitigation's tri-state. NOTSET is "Windows' default applies",
#: which is emphatically not the same as OFF.
_MITIGATION_NOTSET, _MITIGATION_ON, _MITIGATION_OFF = 0, 1, 2


def _mitigation_state(data: Dict[str, Any], group: str, field: str):
    """One tri-state field out of a Get-ProcessMitigation -System result."""
    settings = data.get(group)
    if not isinstance(settings, dict):
        return None
    return settings.get(field)


def _mitigation_census(data: Dict[str, Any]):
    """(on, off, notset) lists of "Group.Field" names, over every tri-state."""
    on, off, notset = [], [], []
    for group, settings in (data or {}).items():
        if not isinstance(settings, dict):
            continue
        for field, value in settings.items():
            if not isinstance(value, int) or isinstance(value, bool):
                continue  # Override* flags are booleans, not tri-states
            name = f"{group}.{field}"
            if value == _MITIGATION_ON:
                on.append(name)
            elif value == _MITIGATION_OFF:
                off.append(name)
            elif value == _MITIGATION_NOTSET:
                notset.append(name)
    return on, off, notset


def check_exploit_protection_system() -> Dict[str, Any]:
    """How many system-wide process mitigations are explicitly turned ON.

    Get-ProcessMitigation reports a tri-state per mitigation: 0 NOTSET,
    1 ON, 2 OFF. NOTSET means Windows' own default applies -- DEP, bottom-up
    ASLR and CFG are all on by default for images that opt in -- so a machine
    with nothing overridden is NOT a machine with no mitigations. This used to
    count keys whose NAME contained "enable" and whose value was 1, then
    colour the result red at 0, which is what every default install produces.
    """
    try:
        data = snapshots.process_mitigation()
        reason = snapshots.unavailable("process_mitigation")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("System Exploit Protections",
                                 f"Could not read: {reason}")]}
        on, off, notset = _mitigation_census(data)
        return {"status": f"{len(on)} enforced, {len(notset)} at Windows default",
                "color": "red" if off else "green",
                "available": True,
                "details": [("Explicitly enabled", str(len(on))),
                            ("Explicitly DISABLED", str(len(off))),
                            ("Windows default (not overridden)", str(len(notset)))]
                           + [("Disabled", name) for name in off[:6]],
                "enabled": not off,
                "mitigation_count": len(on)}
    except Exception:
        return {"status": "Error", "color": "amber",
                "details": [("Exploit Protection", "Check failed")]}


def check_exploit_protection_cfg() -> Dict[str, Any]:
    """System-wide Control Flow Guard.

    The real shape of `Get-ProcessMitigation -System | ConvertTo-Json` is one
    object per mitigation group at the TOP level -- {"Cfg": {"Enable": 0,
    ...}, "Aslr": {...}} -- so the old walk, which looked for a key named
    "CFG" INSIDE each sub-object, could never match anything and reported
    "Off / red" on every machine.
    """
    try:
        data = snapshots.process_mitigation()
        reason = snapshots.unavailable("process_mitigation")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("CFG", f"Could not read: {reason}")]}
        state = _mitigation_state(data, "Cfg", "Enable")
        labels = {_MITIGATION_ON: ("On (enforced system-wide)", "green", True),
                  _MITIGATION_OFF: ("Off (explicitly disabled)", "red", False),
                  _MITIGATION_NOTSET: ("Not enforced system-wide "
                                       "(Windows default applies)", "amber",
                                       False)}
        label, color, enabled = labels.get(
            state, (f"Unknown ({state})", "amber", False))
        return {"status": label, "color": color, "available": True,
                "details": [("Control Flow Guard (CFG)", label)],
                "enabled": enabled}
    except Exception:
        return {"status": "Error", "color": "amber",
                "details": [("CFG", "Check failed")]}


def check_exploit_protection_dep() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["bcdedit", "/enum"], timeout=15)
        dep_enabled = False
        if rc == 0 and out:
            dep_enabled = "nx" in out.lower() and "optin" in out.lower() or "alwayson" in out.lower() or "optout" in out.lower()
            if not dep_enabled:
                rc2, out2, _ = _ps("(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management').ClearPageFileAtShutdown", timeout=10)
        val = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management", "DataExecutionPrevention_Available")
        pol = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management", "DataExecutionPrevention_Policy")
        details = []
        if val is not None:
            details.append(("DEP Hardware Support", "Yes" if val == 1 else "No"))
        if pol is not None:
            dep_map = {0: "OptIn (default)", 1: "Disabled", 2: "OptOut (enforce)", 3: "AlwaysOn"}
            dep_label = dep_map.get(pol, f"Unknown ({pol})")
            details.append(("DEP Policy", dep_label))
            dep_enabled = pol in (0, 2, 3)
        if not details:
            details.append(("DEP", "No policy found — Windows default (OptIn)"))
            dep_enabled = True
        return {"status": "Enabled" if dep_enabled else "Disabled",
                "color": "green" if dep_enabled else "red", "details": details, "enabled": dep_enabled}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("DEP", "Check failed")]}

def check_exploit_protection_aslr() -> Dict[str, Any]:
    try:
        key = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
        mb = _reg_read(key, "MoveImages")
        details = []
        if mb is not None:
            mb_on = mb == 0xFFFFFFFF  # bottom-up randomization is ASLR
            details.append(("Bottom-Up Randomization", "On" if mb_on else "Off"))
        else:
            details.append(("Bottom-Up Randomization", "Default (On)"))
        he = _reg_read(key, "HighEntropyASLROn64Bit")
        if he is not None:
            he_on = he == 1
            details.append(("High Entropy ASLR (64-bit)", "On" if he_on else "Off"))
        else:
            details.append(("High Entropy ASLR (64-bit)", "Not configured (Windows default)"))
        # Get-ProcessMitigation's own view, from the shared snapshot rather
        # than a fourth run of the same cmdlet. Counting how many times a
        # substring appeared in str(data) -- what this used to do -- said 3
        # on a machine with nothing configured.
        if snapshots.unavailable("process_mitigation") is None:
            aslr = snapshots.process_mitigation().get("Aslr") or {}
            enforced = [f for f, v in aslr.items()
                        if isinstance(v, int) and not isinstance(v, bool)
                        and v == _MITIGATION_ON]
            details.append(("ASLR mitigations enforced system-wide",
                            str(len(enforced))))
        # `enabled` used to be the literal True, whatever the two registry
        # values said, so a catalog control bound here would have read a
        # constant. It now agrees with the colour, which was already right.
        on = all("Off" not in d[1] for d in details)
        return {"status": "Active" if on else "Partly off",
                "color": "green" if on else "amber",
                "details": details, "enabled": on}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("ASLR", "Check failed")]}


# ═══════════════════════════════════════════════════════════════════════════════
# BONUS BATCH — Services, Network Toggles, Account Security (~40 functions)
# ═══════════════════════════════════════════════════════════════════════════════

#: `Get-Service | ConvertTo-Json` serialises both enums as INTEGERS, so the
#: snapshot carries numbers and every reader below used to hand them straight
#: to the card -- "Windows Update Service: 1" -- and, because only Automatic
#: was ever tested for, called a DISABLED service "Manual".
_SERVICE_STATUS_LABELS = {1: "Stopped", 2: "Start Pending", 3: "Stop Pending",
                          4: "Running", 5: "Continue Pending",
                          6: "Pause Pending", 7: "Paused"}
_SERVICE_START_LABELS = {0: "Boot", 1: "System", 2: "Automatic", 3: "Manual",
                         4: "Disabled"}


def _service_status_label(raw) -> str:
    """"Running" / "Stopped" / ... from either the number or the name."""
    try:
        return _SERVICE_STATUS_LABELS.get(int(raw), f"Unknown ({raw})")
    except (TypeError, ValueError):
        return str(raw or "Unknown")


def _service_start_label(raw) -> str:
    try:
        return _SERVICE_START_LABELS.get(int(raw), f"Unknown ({raw})")
    except (TypeError, ValueError):
        return str(raw or "Unknown")


def _service_facts(info: Dict[str, Any]) -> Dict[str, Any]:
    """(status label, start-type label, running, disabled) for one service."""
    status = _service_status_label(info.get("status"))
    start = _service_start_label(info.get("start_type"))
    return {"status_label": status, "start_label": start,
            "running": status == "Running", "disabled": start == "Disabled"}


def _svc_check(name: str, label: str, running_bad: bool = True) -> Dict[str, Any]:
    try:
        services = snapshots.service_states()
        reason = snapshots.unavailable("service_states")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(label, f"Could not read: {reason}")]}
        info = services.get(name.lower())
        if info is None:
            return {"status": "Not Found", "color": "green", "available": True,
                    "details": [(label, "Not found")], "enabled": False,
                    "disabled": True}
        facts = _service_facts(info)
        running, auto = facts["running"], facts["start_label"] == "Automatic"
        if running_bad:
            color = "red" if running else ("amber" if auto else "green")
        else:
            color = "green" if running else ("amber" if auto else "red")
        return {"status": facts["status_label"], "color": color,
                "available": True, "enabled": running,
                "disabled": facts["disabled"],
                "start_type": facts["start_label"],
                "details": [(label, f"{facts['status_label']} "
                                    f"({facts['start_label']} start)")]}
    except Exception:
        return {"status": "Error", "color": "amber",
                "details": [(label, "Check failed")]}

def _svc_toggle(name: str, label: str, enabled: bool) -> Dict[str, Any]:
    before = _svc_check(name, label, running_bad=True)
    start_type = "Automatic" if enabled else "Disabled"
    try:
        rc, out, err = _ps(f"Set-Service {name} -StartupType {start_type} -ErrorAction Stop 2>&1; if($?){{'SUCCESS'}}else{{$Error[0]}}", timeout=20)
        ok = "SUCCESS" in (out or "")
        return {"success": ok, "message": f"{label} set to {start_type}" if ok else (err or out or "Failed"),
                "before_value": before.get("enabled"), "after_value": enabled,
                "action": "enable" if enabled else "disable"}
    except Exception as ex:
        return {"success": False, "message": str(ex), "before_value": before.get("enabled"),
                "after_value": before.get("enabled"), "action": "enable" if enabled else "disable"}

def check_service_print_spooler(): return _svc_check("Spooler", "Print Spooler", running_bad=True)
# Dnscache (DNS Client) must be running -- stopping it breaks name resolution, so running is good.
def check_service_dnscache(): return _svc_check("Dnscache", "DNS Client", running_bad=False)
# Dhcp (DHCP Client) must be running -- stopping it breaks IP lease renewal, so running is good.
def check_service_dhcp(): return _svc_check("Dhcp", "DHCP Client", running_bad=False)
# WSearch: polarity retained as-was (both original definitions agreed on running_bad=True) --
# the security verdict is the catalog's to make, not this reader's; see task-1-report.md.
def check_service_wsearch(): return _svc_check("WSearch", "Windows Search", running_bad=True)
# SysMain: polarity retained as-was, same reasoning as WSearch above -- catalog decides desired=None.
def check_service_sysmain(): return _svc_check("SysMain", "SysMain (Superfetch)", running_bad=True)
# Fax should be stopped -- legacy service, unnecessary attack surface, so running is bad.
def check_service_fax(): return _svc_check("Fax", "Fax Service", running_bad=True)
# XboxNetApiSvc should be stopped on a non-gaming/managed machine, so running is bad.
def check_service_xbox_live(): return _svc_check("XboxNetApiSvc", "Xbox Networking", running_bad=True)
def check_service_diagtrack(): return _svc_check("DiagTrack", "Diagnostics Tracking", running_bad=True)
# WpnService (push notifications) stopped is the hardened state, so running is bad.
def check_service_wpn(): return _svc_check("WpnService", "Push Notifications", running_bad=True)
def check_service_mapsbroker(): return _svc_check("MapsBroker", "Maps Broker", running_bad=True)
# fdPHost (function discovery) should be stopped on an untrusted network, so running is bad.
def check_service_fdphost(): return _svc_check("fdPHost", "Function Discovery", running_bad=True)
# WebClient (WebDAV) should be stopped -- a known lateral-movement path, so running is bad.
def check_service_webclient(): return _svc_check("WebClient", "WebClient", running_bad=True)
def check_service_bthserv(): return _svc_check("bthserv", "Bluetooth", running_bad=True)
def check_service_snmp(): return _svc_check("SNMP", "SNMP Service", running_bad=True)
def check_service_upnp(): return _svc_check("upnphost", "UPnP Device Host", running_bad=True)

def check_service_defender_status():
    try:
        services = snapshots.service_states()
        reason = snapshots.unavailable("service_states")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("WinDefend", f"Could not read: {reason}")]}
        info = services.get("windefend")
        if info is None:
            return {"status": "Not Found", "color": "red", "available": True,
                    "details": [("WinDefend", "Not found!")], "enabled": False}
        status_val = info.get("status")
        running = "Running" in str(status_val or "") or status_val == 4
        return {"status": "Running" if running else "Stopped", "color": "green" if running else "red",
                "available": True,
                "details": [("Defender Service", "Running" if running else "Stopped")], "enabled": running}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("WinDefend", "Check failed")]}

# Service toggles live ~500 lines above as `def set_service_*`, using
# _set_service_startup. There used to be a second set of them here, as
# lambdas over _svc_toggle, which silently replaced three of the defs by
# being later in the file. The two are NOT equivalent: _set_service_startup
# reports before_value as the start-type string from
# snapshots.service_states(), _svc_toggle reports it as a bool, and which
# one a caller got was decided by file order rather than by intent.
# Nothing references any of them, so both sets were dead; the defs are kept
# because they return the shape the applier's revert path reads.

def set_remote_desktop(enabled: bool) -> Dict[str, Any]:
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
    before_val = _reg_read(key, "fDenyTSConnections")
    ok = _reg_write(key, "fDenyTSConnections", 0 if enabled else 1)
    return {"success": ok, "message": f"RDP {'enabled' if enabled else 'disabled'}",
            "before_value": before_val, "after_value": 0 if enabled else 1,
            "action": "enable" if enabled else "disable"}

def set_network_discovery(enabled: bool) -> Dict[str, Any]:
    state = "yes" if enabled else "no"
    rc, out, err = _cmd_run(["netsh", "advfirewall", "firewall", "set", "rule",
                              "group=\"Network Discovery\"", "new", f"enable={state}"], timeout=30)
    ok = rc == 0 and "error" not in (err or "").lower()
    return {"success": ok, "message": f"Network Discovery {'enabled' if enabled else 'disabled'}",
            "before_value": not enabled, "after_value": enabled,
            "action": "enable" if enabled else "disable"}

def set_file_sharing(enabled: bool) -> Dict[str, Any]:
    state = "yes" if enabled else "no"
    rc, out, err = _cmd_run(["netsh", "advfirewall", "firewall", "set", "rule",
                              "group=\"File and Printer Sharing\"", "new", f"enable={state}"], timeout=30)
    ok = rc == 0
    return {"success": ok, "message": f"File Sharing {'enabled' if enabled else 'disabled'}",
            "before_value": not enabled, "after_value": enabled,
            "action": "enable" if enabled else "disable"}

# Account checks
def check_guest_account() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["net", "user", "guest"], timeout=10)
        if rc != 0:
            return {"status": "Not Found", "color": "green", "details": [("Guest", "Account not found")], "enabled": False}
        active = "Account active" in out and "Yes" in out
        return {"status": "Active" if active else "Inactive", "color": "red" if active else "green",
                "details": [("Guest Account", "Active" if active else "Disabled")], "enabled": active}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("Guest", "Check failed")]}

def check_autologon() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", "AutoAdminLogon", "REG_SZ")
        # `val and ...` is None when the value is absent, not False, so
        # a machine with no auto-logon reported "could not look".
        active = bool(val is not None and str(val) != "0")
        return {"status": "Enabled" if active else "Disabled", "color": "red" if active else "green",
                "details": [("Auto Logon", "On (security risk)" if active else "Off")],
                "available": True, "enabled": active}
    except Exception:
        return {"status": "Error", "color": "amber", "details": [("AutoLogon", "Check failed")]}

def check_last_username_hidden() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", "DontDisplayLastUserName")
        hidden = val == 1
        return {"status": "Hidden" if hidden else "Shown", "color": "green" if hidden else "amber",
                "details": [("Last Username", "Hidden on logon" if hidden else "Shown (less secure)")], "enabled": hidden}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_screensaver_secure() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKCU\Control Panel\Desktop", "ScreenSaverIsSecure", "REG_SZ")
        secure = bool(val is not None and str(val) == "1")
        return {"status": "Lock on resume" if secure else "No lock", "color": "green" if secure else "amber",
                "details": [("Screen Saver Lock",
                             "On" if secure else "Off (no lock on resume)")],
                "available": True, "enabled": secure}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_screensaver_active() -> Dict[str, Any]:
    try:
        val = _reg_read(r"HKCU\Control Panel\Desktop", "SCRNSAVE.EXE", "REG_SZ")
        if val and str(val).strip():
            return {"status": "Configured", "color": "green",
                    "available": True,
                    "details": [("Screen Saver", str(val)[:50])], "enabled": True}
        return {"status": "Not Configured", "color": "amber",
                "available": True,
                "details": [("Screen Saver", "None")], "enabled": False}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_dns_servers() -> Dict[str, Any]:
    try:
        rc, out, _ = _ps("Get-DnsClientServerAddress -AddressFamily IPv4 | Select -ExpandProperty ServerAddresses | ConvertTo-Json -Compress", timeout=15)
        if rc != 0 or not out:
            return {"status": "Unknown", "color": "amber", "details": [("DNS", "Could not query")]}
        servers = json.loads(out) if out.startswith("[") else [out.strip('"')]
        if isinstance(servers, str):
            servers = [servers]
        details = [("DNS Server", s) for s in servers[:3]]
        return {"status": f"{len(servers)} server(s)", "color": "green",
                "details": details or [("DNS", "DHCP-assigned")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

def check_ntp_sync() -> Dict[str, Any]:
    try:
        rc, out, _ = _cmd_run(["w32tm", "/query", "/status"], timeout=10)
        if rc != 0:
            return {"status": "Not Synced", "color": "amber", "details": [("NTP", "Could not query")]}
        source = ""
        for line in out.splitlines():
            if "Source:" in line:
                source = line.split(":", 1)[1].strip()
        if source:
            return {"status": f"Synced to {source}", "color": "green",
                    "details": [("NTP Source", source)]}
        return {"status": "Unknown", "color": "amber", "details": [("NTP", "No source found")]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

# Defender exclusions
#: What Get-MpPreference puts in an exclusion list for a non-administrator.
#: It does not refuse and it does not return an empty list -- it returns one
#: string saying it will not tell you, which len() happily counted as one
#: exclusion. Three of those read as "3 exclusions".
_EXCLUSION_REDACTED = "must be an administrator"


def _exclusion_count(raw) -> Optional[int]:
    """How many exclusions, or None if Defender redacted the list."""
    if raw is None:
        return 0
    if isinstance(raw, str):
        raw = [raw]
    if any(isinstance(item, str) and _EXCLUSION_REDACTED in item.lower()
           for item in raw):
        return None
    return len(raw)


def check_defender_exclusions() -> Dict[str, Any]:
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Exclusions", f"Could not read: {reason}")]}
        counts = {label: _exclusion_count(prefs.get(field))
                  for label, field in (("Paths", "ExclusionPath"),
                                       ("Processes", "ExclusionProcess"),
                                       ("Extensions", "ExclusionExtension"))}
        if any(c is None for c in counts.values()):
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("Exclusions",
                                 "Defender will not list exclusions to a "
                                 "non-administrator -- run elevated to see "
                                 "them")]}
        total = sum(counts.values())
        color = "green" if total == 0 else ("amber" if total <= 5 else "red")
        return {"status": f"{total} exclusions", "color": color,
                "available": True, "total": total,
                "details": [(label, str(count))
                            for label, count in counts.items()]}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}

# Threat actions
#: ThreatAction, read off the live cmdlet on 2026-08-28:
#:   Clean=1 Quarantine=2 Remove=3 Allow=6 UserDefined=8 NoAction=9
#:   Block=10 None=11
#: 0 is NOT a member -- it is what Get-MpPreference reports for a severity
#: nobody has configured, which is why 11 ("None", the enum's own way of
#: saying unspecified) is what a revert can set and 0 is not.
_THREAT_ACTION_LABELS = {0: "Default (Defender decides)", 1: "Clean",
                         2: "Quarantine", 3: "Remove", 6: "Allow",
                         8: "UserDefined", 9: "NoAction", 10: "Block",
                         11: "None (not configured)"}

#: Action codes that leave a detection in place, or leave the decision unmade.
_THREAT_ACTION_UNPROTECTED = (0, 6, 9, 11)


def _threat_label(v):
    return _THREAT_ACTION_LABELS.get(v, f"Unknown ({v})")

def _threat_check(level_name: str, pref: str):
    """Defender's default action for one threat severity.

    A refused Get-MpPreference used to leave `v` as None, which is not in
    (0, 6, 9), so the card rendered GREEN while `enabled` said False: green
    to the eye and "unsafe" to the catalog, from a read that never happened.
    The refusal is now reported as a refusal, and `value` carries the raw
    action code the catalog compares against.
    """
    try:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(f"{level_name} Threat Action",
                                 f"Could not read: {reason}")]}
        v = prefs.get(pref)
        if v is None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(f"{level_name} Threat Action",
                                 "Defender did not return this preference")]}
        lbl = _threat_label(v)
        protected = v not in _THREAT_ACTION_UNPROTECTED
        return {"status": lbl, "color": "green" if protected else "red",
                "available": True, "value": v,
                "details": [(f"{level_name} Threat Action", lbl)],
                "enabled": protected}
    except Exception:
        return {"status": "Error", "color": "amber", "details": []}


# `def`, not `check_x = lambda: ...`: a lambda's __name__ is '<lambda>', which
# is useless in a traceback or a log line (Task 5, Ruling 18).
def check_defender_threat_low() -> Dict[str, Any]:
    return _threat_check("Low", "LowThreatDefaultAction")


def check_defender_threat_moderate() -> Dict[str, Any]:
    return _threat_check("Moderate", "ModerateThreatDefaultAction")


def check_defender_threat_high() -> Dict[str, Any]:
    return _threat_check("High", "HighThreatDefaultAction")


def check_defender_threat_severe() -> Dict[str, Any]:
    return _threat_check("Severe", "SevereThreatDefaultAction")

# Toggle: set threat actions
def _toggle_threat(pref: str, level: int, label: str) -> Dict[str, Any]:
    before = _get_mp_pref_value(pref)
    result = _set_mp_pref(pref, level, label)
    result["before_value"] = before
    result["after_value"] = level
    result["action"] = f"set_{label.lower().replace(' ','_')}"
    return result

def set_defender_threat_low(level: int):
    return _toggle_threat("LowThreatDefaultAction", level, "Low threat action")


def set_defender_threat_moderate(level: int):
    return _toggle_threat("ModerateThreatDefaultAction", level, "Moderate threat action")


def set_defender_threat_high(level: int):
    return _toggle_threat("HighThreatDefaultAction", level, "High threat action")


def set_defender_threat_severe(level: int):
    return _toggle_threat("SevereThreatDefaultAction", level, "Severe threat action")

# More Defender toggles
def set_defender_scan_only_idle(enabled: bool) -> Dict[str, Any]:
    before = _get_mp_pref_value("ScanOnlyIfIdle")
    result = _set_mp_pref("ScanOnlyIfIdle", 1 if enabled else 0, "Scan only if idle")
    result["before_value"] = before
    result["after_value"] = 1 if enabled else 0
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_restore_point(enabled: bool) -> Dict[str, Any]:
    before = _get_mp_pref_value("DisableRestorePoint")
    result = _set_mp_pref("DisableRestorePoint", 0 if enabled else 1, "Restore point before scan")
    result["before_value"] = before
    result["after_value"] = 0 if enabled else 1
    result["action"] = "enable" if enabled else "disable"
    return result

def set_defender_intrusion_prevention(enabled: bool) -> Dict[str, Any]:
    before = _get_mp_pref_value("DisableIntrusionPreventionSystem")
    result = _set_mp_pref("DisableIntrusionPreventionSystem", 0 if enabled else 1, "Intrusion prevention")
    result["before_value"] = before
    result["after_value"] = 0 if enabled else 1
    result["action"] = "enable" if enabled else "disable"
    return result

# Screen saver toggles
def set_screensaver_secure(enabled: bool) -> Dict[str, Any]:
    before = _reg_read(r"HKCU\Control Panel\Desktop", "ScreenSaverIsSecure", "REG_SZ")
    ok = _reg_write(r"HKCU\Control Panel\Desktop", "ScreenSaverIsSecure", "1" if enabled else "0", "REG_SZ")
    return {"success": ok, "message": f"Screen saver lock {'enabled' if enabled else 'disabled'}",
            "before_value": before, "after_value": "1" if enabled else "0",
            "action": "enable" if enabled else "disable"}

def set_last_username_hidden(enabled: bool) -> Dict[str, Any]:
    before = _reg_read(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", "DontDisplayLastUserName")
    ok = _reg_write(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", "DontDisplayLastUserName", 1 if enabled else 0)
    return {"success": ok, "message": f"Last username {'hidden' if enabled else 'shown'}",
            "before_value": before, "after_value": 1 if enabled else 0,
            "action": "enable" if enabled else "disable"}

# Log size checks
def _event_log_size(log: str, label: str, green_at: int) -> Dict[str, Any]:
    """Maximum size of one event log, in MB.

    Both callers used to ignore `rc` and fall back to `float(out or 0)`, so a
    refused or failed query rendered "0 MB" in red -- a verdict about the
    machine produced by a question that was never answered.
    """
    try:
        rc, out, err = _ps(f"(Get-WinEvent -ListLog {log}).MaximumSizeInBytes / 1MB",
                           timeout=15)
        # `rc` is not the signal here: unelevated, this exits 0 and prints "0"
        # while putting "Attempted to perform an unauthorized operation" on
        # stderr, so the old reader called the Security log 0 MB and red.
        refused = snapshots._looks_refused(rc, out or "", err or "")
        if refused is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(label, f"Could not read: {refused[:70]}")]}
        mb = float(out.strip())
        color = "green" if mb >= green_at else ("amber" if mb >= green_at / 2 else "red")
        return {"status": f"{mb:.0f} MB", "color": color, "available": True,
                "megabytes": int(mb),
                "details": [(label, f"{mb:.0f} MB")]}
    except Exception:
        return {"status": "Error", "color": "amber", "available": False,
                "details": []}


def check_security_log_size() -> Dict[str, Any]:
    return _event_log_size("Security", "Security Log Max Size", 128)

def check_system_log_size() -> Dict[str, Any]:
    return _event_log_size("System", "System Log Max Size", 64)


# ═══════════════════════════════════════════════════════════════════════════════
# CPU VULNERABILITY MITIGATIONS — Spectre, Meltdown, all known variants
#
# Backed by snapshots.speculation_control() -- ONE cached PowerShell call for
# all fourteen readers below (task-3b defect B), which uses
# Get-SpeculationControlSettings when already present and NEVER downloads or
# installs it (task-3b defect C1: a *read* must not fetch and execute code
# from the internet). When the module is absent the snapshot is a small
# registry-only dict that does not carry the per-CVE fields these readers
# look for -- `_speculation_read` treats a missing field as "could not
# determine" (status Unknown, amber) rather than letting `.get(key, False)`
# silently turn "we don't know" into "not mitigated" / red (defect C2, the
# project's canonical refused-read-reported-as-a-fact bug in its most
# alarming form: a failed module load must never read as "you are
# vulnerable").
# ═══════════════════════════════════════════════════════════════════════════════

def _speculation_fallback_details(d: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Informational detail lines surfacing the raw registry values behind
    the fallback, when the module is absent -- so an Unknown verdict still
    hands the user something real rather than a fetch nobody reads. This
    project deliberately does not decode these bits into a mitigated/
    vulnerable verdict (task-3b defect C1): that would risk a confident
    wrong answer, which is worse than a visibly missing one.
    """
    lines = []
    override = d.get("FeatureSettingsOverride")
    mask = d.get("FeatureSettingsOverrideMask")
    vbs = d.get("VirtualizationBasedSecurityEnabled")
    if override is not None:
        lines.append(("FeatureSettingsOverride",
                       hex(override) if isinstance(override, int) else str(override)))
    if mask is not None:
        lines.append(("FeatureSettingsOverrideMask",
                       hex(mask) if isinstance(mask, int) else str(mask)))
    if vbs is not None:
        lines.append(("Virtualization Based Security", "Enabled" if vbs else "Disabled"))
    return lines


def _speculation_status(raw) -> str:
    """Classify a SYSTEM_SPECULATION_CONTROL_*_STATUS string.

    Returns "immune", "mitigated", "disabled" or "unknown".

    DISABLED is tested BEFORE mitigated, and that is the whole point: the
    string SYSTEM_SPECULATION_CONTROL_SRSO_MITIGATION_DISABLED contains the
    substring "MITIGATION", so a naive `"MITIGATION" in status` classified an
    explicitly disabled mitigation as mitigated.
    """
    text = str(raw or "").upper()
    if not text:
        return "unknown"
    if "IMMUNE" in text or "NOT_AFFECTED" in text or "NOT_APPLICABLE" in text:
        return "immune"
    if "DISABLED" in text or "NOT_SUPPORTED" in text:
        return "disabled"
    if "MITIGATED" in text or "ENABLED" in text:
        return "mitigated"
    return "unknown"


def _speculation_read(cve_label: str, required_field: str):
    """(data, unavailable_response_or_None) for one CVE reader.

    `unavailable_response_or_None` is None when it is safe to read
    `required_field` from `data`. Otherwise it is the full Unknown/amber
    response the caller should return as-is -- either the snapshot fetch
    itself was refused (`snapshots.unavailable` had a reason), or it
    succeeded but is the registry-only fallback, which does not carry this
    field. Both are "could not determine", never a guessed verdict.
    """
    d = snapshots.speculation_control()
    reason = snapshots.unavailable("speculation_control")
    if reason is not None:
        return None, {"status": "Unknown", "color": "amber", "available": False,
                       "details": [(cve_label, f"Could not read: {reason}")]}
    if required_field not in d:
        return None, {"status": "Unknown", "color": "amber", "available": False,
                       "details": [(cve_label,
                                     "SpeculationControl module not present -- "
                                     "the registry fallback cannot determine this")]
                                  + _speculation_fallback_details(d)}
    return d, None


def check_spectre_v2() -> Dict[str, Any]:
    """Spectre v2 / BTI (CVE-2017-5715)."""
    try:
        d, unavailable = _speculation_read("Spectre v2", "BTIHardwarePresent")
        if unavailable is not None:
            return unavailable
        hw = d.get("BTIHardwarePresent", False)
        os_ok = d.get("BTIWindowsSupportEnabled", False) or d.get("BTIWindowsSupportPresent", False)
        retpoline = d.get("BTIKernelRetpolineEnabled", False)
        enabled = hw and os_ok
        status = "Mitigated" if enabled else ("Hardware OK but OS off" if hw else "Not mitigated")
        color = "green" if enabled else "red" if hw else "amber"
        return {"status": status, "color": color, "available": True,
                "details": [("BTI Hardware", "Present" if hw else "N/A"),
                            ("BTI OS Support", "Enabled" if os_ok else "Disabled"),
                            ("Retpoline", "Enabled" if retpoline else "N/A")], "enabled": enabled}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("Spectre v2", "Check failed")]}

def check_meltdown() -> Dict[str, Any]:
    """Meltdown (CVE-2017-5754)."""
    try:
        d, unavailable = _speculation_read("Meltdown", "KVAShadowWindowsSupportPresent")
        if unavailable is not None:
            return unavailable
        hw_vuln = d.get("RdclHardwareProtectedReported") and not d.get("RdclHardwareProtected", True)
        kva_enabled = d.get("KVAShadowWindowsSupportEnabled", False)
        kva_required = d.get("KVAShadowRequired", False)
        if not hw_vuln and not kva_required:
            return {"status": "Not vulnerable (HW)", "color": "green", "available": True,
                    "details": [("Hardware", "Not vulnerable to Meltdown")], "enabled": True}
        status = "Mitigated (KPTI on)" if kva_enabled else ("Vulnerable (KPTI off)" if hw_vuln else "Unknown")
        color = "green" if kva_enabled or not hw_vuln else "red"
        return {"status": status, "color": color, "available": True,
                "details": [("Hardware Vulnerable", str(hw_vuln)), ("KVA Shadow", "On" if kva_enabled else "Off"),
                            ("KVA Required", str(kva_required))], "enabled": not hw_vuln or kva_enabled}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("Meltdown", "Check failed")]}

def check_l1tf() -> Dict[str, Any]:
    """L1TF/Foreshadow (CVE-2018-3615, -3620, -3646)."""
    try:
        d, unavailable = _speculation_read("L1TF", "L1TFHardwareVulnerable")
        if unavailable is not None:
            return unavailable
        hw_vuln = d.get("L1TFHardwareVulnerable", True)
        os_present = d.get("L1TFWindowsSupportPresent", False)
        os_enabled = d.get("L1TFWindowsSupportEnabled", False)
        if not hw_vuln:
            return {"status": "Not vulnerable (HW)", "color": "green", "available": True,
                    "details": [("Hardware", "Not vulnerable to L1TF")], "enabled": True}
        enabled = os_present and os_enabled
        return {"status": "Mitigated" if enabled else "Vulnerable",
                "color": "green" if enabled else "red", "available": True,
                "details": [("OS Support", "Present" if os_present else "N/A"),
                            ("OS Enabled", "Yes" if os_enabled else "No")], "enabled": enabled}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("L1TF", "Check failed")]}

def check_mds() -> Dict[str, Any]:
    """MDS/Zombieload (CVE-2018-12126/127/130, CVE-2019-11091)."""
    try:
        d, unavailable = _speculation_read("MDS", "MDSHardwareVulnerable")
        if unavailable is not None:
            return unavailable
        hw_vuln = d.get("MDSHardwareVulnerable", True)
        os_present = d.get("MDSWindowsSupportPresent", False)
        if not hw_vuln:
            return {"status": "Not vulnerable (HW)", "color": "green", "available": True,
                    "details": [("Hardware", "Not vulnerable to MDS")], "enabled": True}
        return {"status": "Mitigated" if os_present else "Vulnerable (no OS support)",
                "color": "green" if os_present else "red", "available": True,
                "details": [("OS MDS Support", "Present" if os_present else "Absent")], "enabled": os_present}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("MDS", "Check failed")]}

def check_ssbd() -> Dict[str, Any]:
    """Spectre v4 / SSBD (CVE-2018-3639)."""
    try:
        d, unavailable = _speculation_read("SSBD/Spectre v4", "SSBDHardwarePresent")
        if unavailable is not None:
            return unavailable
        hw_present = d.get("SSBDHardwarePresent", False)
        os_present = d.get("SSBDWindowsSupportPresent", False)
        sys_enabled = d.get("SSBDWindowsSupportEnabledSystemWide", False)
        enabled = hw_present and os_present and sys_enabled
        status = "System-wide enabled" if enabled else ("HW/OS present, not enabled" if hw_present else "N/A")
        color = "green" if enabled else "amber"
        return {"status": status, "color": color, "available": True,
                "details": [("HW Support", str(hw_present)), ("OS Support", str(os_present)),
                            ("System-Wide", "Yes" if sys_enabled else "No")], "enabled": enabled}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("SSBD/Spectre v4", "Check failed")]}

def check_swapgs() -> Dict[str, Any]:
    """SWAPGS (CVE-2019-1125)."""
    try:
        d, unavailable = _speculation_read("SWAPGS", "BhbEnabled")
        if unavailable is not None:
            return unavailable
        # `BhbEnabled or BhbDisabledSystemPolicy is False` used to be the test,
        # which reads "no policy turned it off" as "it is on" -- and that
        # second clause is True on any machine with no such policy, so this
        # rendered GREEN with BhbEnabled False. The mitigation's own flag is
        # the only thing that says it is running.
        enabled = bool(d.get("BhbEnabled", False))
        by_policy = bool(d.get("BhbDisabledSystemPolicy", False))
        no_hardware = bool(d.get("BhbDisabledNoHardwareSupport", False))
        if enabled:
            status, color = "Mitigated", "green"
        elif by_policy:
            status, color = "Disabled by policy", "red"
        elif no_hardware:
            status, color = "Not applicable (no hardware support)", "green"
        else:
            status, color = "Not enabled", "amber"
        return {"status": status, "color": color, "available": True,
                "details": [("BHB Mitigation", status),
                            ("BhbEnabled", str(enabled)),
                            ("Disabled by policy", str(by_policy))],
                "enabled": enabled}
    except Exception:
        # "N/A" here (not "Unknown") used to be swept into the aggregate's
        # "mitigated" bucket by its "N/A" substring check -- an unexpected
        # exception told the user they were protected against SWAPGS.
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("SWAPGS", "Check failed")]}

def check_tsx_async_abort() -> Dict[str, Any]:
    """TSX Async Abort / TAA (CVE-2019-11135) — checked via MDS/SBDR/FBSDP."""
    try:
        d, unavailable = _speculation_read("TAA", "SBDRSSDPHardwareVulnerable")
        if unavailable is not None:
            return unavailable
        ok = not d.get("SBDRSSDPHardwareVulnerable", True) and not d.get("FBSDPHardwareVulnerable", True)
        return {"status": "Mitigated (HW immune)" if ok else "Unknown", "color": "green" if ok else "amber",
                "available": True,
                "details": [("SBDR HW Vulnerable", str(d.get("SBDRSSDPHardwareVulnerable", "?"))),
                            ("FBSDP HW Vulnerable", str(d.get("FBSDPHardwareVulnerable", "?")))]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("TAA", "Check failed")]}

def check_srbds() -> Dict[str, Any]:
    """SRBDS/CrossTalk (CVE-2020-0543) — checked via SBDR."""
    try:
        d, unavailable = _speculation_read("SRBDS", "SBDRSSDPHardwareVulnerable")
        if unavailable is not None:
            return unavailable
        ok = not d.get("SBDRSSDPHardwareVulnerable", True)
        return {"status": "Not vulnerable (HW)" if ok else "Unknown", "color": "green" if ok else "amber",
                "available": True, "details": [("SBDR HW", "Immune" if ok else "Unknown")]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("SRBDS", "Check failed")]}

def check_retbleed() -> Dict[str, Any]:
    """Retbleed (CVE-2022-29900/29901)."""
    try:
        d, unavailable = _speculation_read("Retbleed", "BranchConfusionStatus")
        if unavailable is not None:
            return unavailable
        branch_ok = d.get("BranchConfusionStatus", "").upper() == "SYSTEM_SPECULATION_CONTROL_BRANCH_CONFUSION_HARDWARE_IMMUNE"
        retpoline = d.get("BTIKernelRetpolineEnabled", False)
        cured = branch_ok or retpoline
        return {"status": "Mitigated" if cured else "Unknown", "color": "green" if cured else "amber",
                "available": True,
                "details": [("Branch Confusion", "HW Immune" if branch_ok else "Unknown"),
                            ("Retpoline", "On" if retpoline else "Off")]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("Retbleed", "Check failed")]}

def check_mmio_stale_data() -> Dict[str, Any]:
    """MMIO Stale Data (CVE-2022-21123/125/127/166) — checked via FBClear."""
    try:
        d, unavailable = _speculation_read("MMIO", "FBClearWindowsSupportPresent")
        if unavailable is not None:
            return unavailable
        # Support being PRESENT is the mitigation shipping, not the mitigation
        # running. This reported "Mitigated / green" off the present flag
        # alone, on a machine whose FBClearWindowsSupportEnabled is False.
        present = bool(d.get("FBClearWindowsSupportPresent", False))
        enabled = bool(d.get("FBClearWindowsSupportEnabled", False))
        vulnerable_fields = ("SBDRSSDPHardwareVulnerable",
                             "FBSDPHardwareVulnerable",
                             "PSDPHardwareVulnerable")
        reported = [f for f in vulnerable_fields if f in d]
        hw_vulnerable = [f for f in reported if d.get(f)]
        details = [("Fill Buffer Clear support",
                    "Present" if present else "Absent"),
                   ("Fill Buffer Clear enabled", "Yes" if enabled else "No")]
        details += [(f, str(d.get(f))) for f in reported]
        if reported and not hw_vulnerable:
            return {"status": "Not vulnerable (hardware)", "color": "green",
                    "available": True, "details": details, "enabled": True}
        if enabled:
            return {"status": "Mitigated", "color": "green", "available": True,
                    "details": details, "enabled": True}
        return {"status": "Not enabled" if present else "No OS support",
                "color": "amber", "available": True,
                "details": details, "enabled": False}
    except Exception:
        # Same reasoning as check_swapgs's except-branch above: "N/A" read
        # as "mitigated" to the aggregate's classifier, so an exception here
        # told the user they were protected against MMIO Stale Data.
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("MMIO", "Check failed")]}

def check_downfall_gds() -> Dict[str, Any]:
    """Downfall / GDS (CVE-2022-40982)."""
    try:
        d, unavailable = _speculation_read("Downfall", "GdsStatus")
        if unavailable is not None:
            return unavailable
        status = d.get("GdsStatus", "")
        immune = "HARDWARE_IMMUNE" in str(status).upper()
        return {"status": "Not vulnerable (HW immune)" if immune else status,
                "color": "green" if immune else "amber", "available": True,
                "details": [("GDS Status", str(status))]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("Downfall", "Check failed")]}

def check_zenbleed() -> Dict[str, Any]:
    """Zenbleed (CVE-2023-20593) — AMD Zen 2."""
    try:
        d, unavailable = _speculation_read("Zenbleed", "DivideByZeroStatus")
        if unavailable is not None:
            return unavailable
        div_by_zero = str(d.get("DivideByZeroStatus", "")).upper()
        mitigated = "MITIGATED" in div_by_zero
        return {"status": "Mitigated" if mitigated else "Unknown",
                "color": "green" if mitigated else "amber", "available": True,
                "details": [("Divide by Zero", str(d.get("DivideByZeroStatus", "N/A")))]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("Zenbleed", "N/A")]}

def check_inception() -> Dict[str, Any]:
    """Inception (CVE-2023-20569) — AMD Zen 3/4, checked via SRSO."""
    try:
        d, unavailable = _speculation_read("Inception", "SrsoStatus")
        if unavailable is not None:
            return unavailable
        # `"MITIGATION" in srso` matched SRSO_MITIGATION_DISABLED, so a status
        # that says the mitigation is DISABLED rendered as Mitigated, green,
        # on an AMD machine -- and Inception is an AMD Zen 3/4 flaw.
        raw = d.get("SrsoStatus")
        labels = {"immune": ("Not vulnerable (hardware immune)", "green", True),
                  "mitigated": ("Mitigated", "green", True),
                  "disabled": ("Mitigation disabled", "red", False),
                  "unknown": (f"Unknown ({raw})", "amber", False)}
        status, color, ok = labels[_speculation_status(raw)]
        return {"status": status, "color": color, "available": True,
                "details": [("SRSO Status", str(raw or "N/A"))], "enabled": ok}
    except Exception:
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("Inception", "Check failed")]}

def check_rfds() -> Dict[str, Any]:
    """RFDS (CVE-2023-28746) — Register File Data Sampling (Atom)."""
    try:
        d, unavailable = _speculation_read("RFDS", "RfdsStatus")
        if unavailable is not None:
            return unavailable
        status = str(d.get("RfdsStatus", "")).upper()
        immune = "IMMUNE" in status
        return {"status": "Not vulnerable (HW immune)" if immune else "Unknown",
                "color": "green" if immune else "amber", "available": True,
                "details": [("RFDS Status", str(d.get("RfdsStatus", "N/A")))]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "details": [("RFDS", "N/A")]}


# ═══════════════════════════════════════════════════════════════════════════════
# TOP WINDOWS 11 CVE MITIGATION CHECKS (PrintNightmare, Zerologon, etc.)
# ═══════════════════════════════════════════════════════════════════════════════

def check_printnightmare() -> Dict[str, Any]:
    """PrintNightmare (CVE-2021-34527, CVE-2021-36958)."""
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Printers\PointAndPrint"
    restrict = _reg_read(key, "RestrictDriverInstallationToAdministrators")
    no_warn = _reg_read(key, "NoWarningNoElevationOnInstall")
    update_pr = _reg_read(key, "UpdatePromptSettings")
    protected = restrict == 1
    details = [
        ("Restrict Drivers to Admins", "Yes" if protected else "No (vulnerable)"),
        ("No Warning on Install", "Disabled" if no_warn == 0 else "Enabled" if no_warn == 1 else "Not set"),
        ("Update Prompt", "On" if update_pr == 1 else "Off" if update_pr == 0 else "Not set"),
    ]
    return {"status": "Mitigated" if protected else "Not Mitigated",
            "color": "green" if protected else "red",
            "details": details, "enabled": protected}

def check_zerologon() -> Dict[str, Any]:
    """Zerologon (CVE-2020-1472) — Netlogon secure channel."""
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters"
    full_secure = _reg_read(key, "FullSecureChannelProtection")
    seal_secure = _reg_read(key, "SealSecureChannel")
    sign_secure = _reg_read(key, "SignSecureChannel")
    protected = full_secure == 1
    details = [
        ("Full Secure Channel", "On" if full_secure == 1 else "Off" if full_secure == 0 else "Not set"),
        ("Seal Secure", "Yes" if seal_secure == 1 else "No" if seal_secure == 0 else "Not set"),
        ("Sign Secure", "Yes" if sign_secure == 1 else "No" if sign_secure == 0 else "Not set"),
    ]
    return {"status": "Mitigated" if protected else "Not Mitigated",
            "color": "green" if protected else "red",
            "details": details, "enabled": protected}

def check_petitpotam() -> Dict[str, Any]:
    """PetitPotam (CVE-2021-36942) — NTLM relay via EPA."""
    wk_key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    sv_key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
    wk_epa = _reg_read(wk_key, "RequireExtendedProtection")
    sv_epa = _reg_read(sv_key, "RequireExtendedProtection")
    protected = (wk_epa == 1) or (sv_epa == 1)
    details = [
        ("Workstation EPA", "On" if wk_epa == 1 else "Off" if wk_epa == 0 else "Not set"),
        ("Server EPA", "On" if sv_epa == 1 else "Off" if sv_epa == 0 else "Not set"),
    ]
    return {"status": "Mitigated" if protected else "Not Mitigated",
            "color": "green" if protected else "red",
            "details": details, "enabled": protected}

def check_follina() -> Dict[str, Any]:
    """Follina (CVE-2022-30190) — MSDT URL protocol."""
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\ScriptedDiagnosticsProvider\Policy"
    disabled = _reg_read(key, "DisableQueryRemoteServer")
    protected = disabled == 1
    return {"status": "Protected" if protected else "Not Protected",
            "color": "green" if protected else "red",
            "details": [("MSDT Remote Query", "Disabled" if disabled == 1 else "Enabled" if disabled == 0 else "Not set")],
            "enabled": protected}

def check_blacklotus() -> Dict[str, Any]:
    """BlackLotus (CVE-2023-24932) — Secure Boot bypass."""
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\SecureBoot"
    updates = _reg_read(key, "AvailableUpdates")
    applied = _reg_read(key, "AppliedUpdates")
    details = [("Available Updates", f"0x{updates:X}" if updates else "None"),
               ("Applied Updates", f"0x{applied:X}" if applied else "None")]
    ok = (updates is not None and applied is not None and applied >= updates) or (applied and applied > 0)
    return {"status": "Patched" if ok else "May need updates",
            "color": "green" if ok else "amber",
            "details": details, "enabled": ok}

def check_kerberos_armoring() -> Dict[str, Any]:
    """Kerberos PAC armoring (CVE-2022-33679, CVE-2022-33647)."""
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa\Kerberos\Parameters"
    pac = _reg_read(key, "KrbtgtFullPacSignature")
    details = [("Full PAC Signature", "On" if pac == 1 else "Off" if pac == 0 else "Not set")]
    return {"status": "ARMORED" if pac == 1 else "Not Armored",
            "color": "green" if pac == 1 else "amber",
            "details": details, "enabled": pac == 1}

def check_credential_guard_vbs() -> Dict[str, Any]:
    """VBS-based Credential Guard (CVE-2022-22047, CVE-2021-36934)."""
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard"
    vbs = _reg_read(key, "EnableVirtualizationBasedSecurity")
    req = _reg_read(key, "RequirePlatformSecurityFeatures")
    details = [("VBS Enabled", "Yes" if vbs == 1 else "No" if vbs == 0 else "Not configured"),
               ("Platform Security", "Required" if req and req > 0 else "Not required")]
    return {"status": "VBS Active" if vbs == 1 else "VBS Off",
            "color": "green" if vbs == 1 else "red",
            "details": details, "enabled": vbs == 1}

#: Windows 10 1903 and 1909. No other build shipped SMBv3.1.1 compression.
_SMBGHOST_AFFECTED_BUILDS = (18362, 18363)


def _windows_build() -> Optional[int]:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return None


def _file_present(path: str) -> Optional[bool]:
    """True, False, or None when the answer itself was refused.

    `os.path.exists` returns False for a file it is not allowed to stat, so
    "not found" and "not allowed to look" arrive identically -- which is how
    the SAM hive check reported a clean bill of health unelevated.
    """
    try:
        os.stat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def check_smb_ghost() -> Dict[str, Any]:
    """SMBGhost (CVE-2020-0796) — SMBv3 compression RCE."""
    try:
        # This used to read the SMB1 value and report "SMBv1 Disabled (safe)".
        # SMBGhost is not about SMBv1 at all: it is the SMBv3.1.1 compression
        # RCE, its registry mitigation is DisableCompression, and only builds
        # 18362 and 18363 (1903/1909) ever shipped the vulnerable code. The
        # card was answering confidently about a different vulnerability.
        build = _windows_build()
        if build is not None and build not in _SMBGHOST_AFFECTED_BUILDS:
            return {"status": f"Not affected (build {build})", "color": "green",
                    "available": True, "enabled": True,
                    "details": [("SMBv3.1.1 compression",
                                 "Only Windows 10 1903/1909 were affected")]}
        disabled = _reg_read(r"HKLM\SYSTEM\CurrentControlSet\Services"
                             r"\LanmanServer\Parameters", "DisableCompression")
        mitigated = disabled == 1
        return {"status": "Mitigated" if mitigated else "Not mitigated",
                "color": "green" if mitigated else "red",
                "available": True, "enabled": mitigated,
                "details": [("SMBv3 compression",
                             "Disabled" if mitigated else "Enabled"),
                            ("Windows build", str(build))]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("SMBGhost", "Check failed")]}

def check_sam_hive_permissions() -> Dict[str, Any]:
    """HiveNightmare (CVE-2021-36934) — SAM file readable by non-admin (Win10/11 pre-July 2021)."""
    try:
        sam_path = r"C:\Windows\System32\config\SAM"
        # Both halves of this used to fall through to GREEN when they failed.
        # Unelevated, os.stat on the SAM hive raises PermissionError (winerror
        # 5) and os.path.exists flattens that to False -- "file not found",
        # scored as "not vulnerable". icacls then fails too (rc 1, "Failed
        # processing 1 files"), and an empty result matched no BUILTIN\Users
        # line, which also scored as safe.
        present = _file_present(sam_path)
        if present is None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("SAM hive",
                                 "Cannot examine the file unelevated -- this "
                                 "is a refusal, not an absence")]}
        if present is False:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("SAM hive", f"{sam_path} not found")]}
        rc, out, err = _ps(f"icacls '{sam_path}'", timeout=10)
        # EVERY successful icacls run ends with "Failed processing 0 files",
        # so testing for that substring rejected the success case as well --
        # found by running the probe elevated, where icacls works.
        failed = any(line.strip().startswith("Failed processing")
                     and not line.strip().startswith("Failed processing 0")
                     for part in (out or "").split(";")
                     for line in [part])
        if rc != 0 or not out or failed or "Access is denied" in (out or ""):
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [("SAM ACL",
                                 f"icacls could not read the ACL: "
                                 f"{(out or err or '').strip()[:60]}")]}
        users_lines = [line for line in out.splitlines()
                       if "BUILTIN\\Users" in line]
        has_users_read = any(marker in line for line in users_lines
                             for marker in (":(R", ":(F)", ":(M)", "(RX)"))
        if not has_users_read:
            return {"status": "Restricted (safe)", "color": "green",
                    "available": True, "enabled": True,
                    "details": [("SAM ACL", "BUILTIN\\Users has no read access")]}
        return {"status": "Exposed (vulnerable)", "color": "red",
                "available": True, "enabled": False,
                "details": [("SAM ACL", "BUILTIN\\Users has read access!")]}
    except Exception:
        return {"status": "Unknown", "color": "amber", "available": False,
                "details": [("HiveNightmare", "Check failed")]}

def check_ntlm_relay_protection() -> Dict[str, Any]:
    """NTLM relay attack mitigations (PetitPotam, DFSCoerce, etc.)."""
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa\MSV1_0"
    restrict_out = _reg_read(key, "RestrictSendingNTLMTraffic")
    restrict_in = _reg_read(key, "RestrictReceivingNTLMTraffic")
    details = [
        ("Outgoing NTLM Restricted", "Yes" if restrict_out == 2 else "Partial" if restrict_out == 1 else "No"),
        ("Incoming NTLM Restricted", "Yes" if restrict_in == 2 else "Partial" if restrict_in == 1 else "No"),
    ]
    ok = restrict_out == 2 and restrict_in == 2
    return {"status": "Hardened" if ok else "Not Hardened",
            "color": "green" if ok else "amber",
            "details": details, "enabled": ok}

#: id (the reader's own function name) -> (display label, reader function).
#: The id is what a caller's `readings` mapping keys on -- see
#: `check_windows_defender_cve_mitigations` below.
_CVE_MITIGATION_CHECKS = [
    ("check_spectre_v2", "Spectre v2", check_spectre_v2),
    ("check_meltdown", "Meltdown", check_meltdown),
    ("check_l1tf", "L1TF/Foreshadow", check_l1tf),
    ("check_mds", "MDS/Zombieload", check_mds),
    ("check_ssbd", "Spectre v4/SSBD", check_ssbd),
    ("check_printnightmare", "PrintNightmare", check_printnightmare),
    ("check_zerologon", "Zerologon", check_zerologon),
    ("check_petitpotam", "PetitPotam", check_petitpotam),
    ("check_follina", "Follina", check_follina),
    ("check_blacklotus", "BlackLotus", check_blacklotus),
    ("check_kerberos_armoring", "Kerberos Armoring", check_kerberos_armoring),
    ("check_credential_guard_vbs", "Credential Guard", check_credential_guard_vbs),
    ("check_smb_ghost", "SMB Ghost", check_smb_ghost),
    ("check_sam_hive_permissions", "HiveNightmare", check_sam_hive_permissions),
    ("check_ntlm_relay_protection", "NTLM Relay", check_ntlm_relay_protection),
    ("check_swapgs", "SWAPGS", check_swapgs),
    ("check_tsx_async_abort", "TSX Abort", check_tsx_async_abort),
    ("check_srbds", "SRBDS", check_srbds),
    ("check_retbleed", "Retbleed", check_retbleed),
    ("check_mmio_stale_data", "MMIO Stale", check_mmio_stale_data),
    ("check_downfall_gds", "Downfall/GDS", check_downfall_gds),
    ("check_zenbleed", "Zenbleed", check_zenbleed),
    ("check_inception", "Inception", check_inception),
    ("check_rfds", "RFDS", check_rfds),
]


def check_windows_defender_cve_mitigations(
        readings: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Aggregate check: all major Win10/11 CVEs.

    Pass `readings` -- a mapping of reader function name (e.g.
    "check_spectre_v2") to that reader's already-computed result dict -- to
    have this consume readings a caller already took instead of re-running
    them. Any of the 24 sub-checks not present in `readings` is still called
    directly here.

    Called with no arguments, as other code and tests may depend on being
    able to do, this re-runs all 24 sub-readers itself every time. That is
    the slow path (task-3b measured it at ~30s before the sub-readers were
    cached) -- prefer passing `readings` wherever the caller already has
    them, e.g. the CVE tab's own sweep.
    """
    readings = readings or {}
    mitigated, vulnerable, na = 0, 0, 0
    details = []
    for key, name, fn in _CVE_MITIGATION_CHECKS:
        try:
            r = readings[key] if key in readings else fn()
            s = r.get("status", "")
            if r.get("available") is False or s == "Unknown":
                na += 1
                details.append((name, f"Unknown: {s[:40]}" if s and s != "Unknown" else "Unknown"))
            elif "Mitigated" in s or "Protected" in s or "Patched" in s or "Hardened" in s or "Not vulnerable" in s or "ARMORED" in s or "VBS Active" in s or "N/A" in s or "Restricted" in s or "Disabled (safe)" in s:
                mitigated += 1
                details.append((name, s[:50]))
            else:
                vulnerable += 1
                details.append((name, f"CHECK: {s[:40]}"))
        except Exception:
            na += 1
            details.append((name, "Check failed"))
    if vulnerable > 0:
        color, overall = "red", f"{vulnerable} issue(s) need attention"
    elif na > 0:
        color, overall = "amber", f"{mitigated} OK, {na} unknown"
    else:
        color, overall = "green", f"All {mitigated} mitigations active"
    return {"status": overall, "color": color, "details": details[:15]}
