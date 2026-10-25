"""Accounts, credentials, and what an attacker can run once inside.

Everything here is either an LSA/Winlogon registry value or a `net accounts`
setting, so most entries revert exactly. Three conventions:

* `desired` on a numeric control is compared for EQUALITY. A machine already
  stricter than the catalog's value -- 2 cached logons against a desired 4 --
  therefore reads as "not at desired". That is a limitation of the comparison,
  not a finding about the machine, and it is recorded for the review dialog.
* HKCU values are read-only here for the same reason as WPAD: the elevated
  helper writes the administrator's hive, not the signed-in user's.
* A control that could lock somebody out of their own machine is `Risk.HIGH`,
  and the batch that carries one forces a restore point.
"""
from core.system_paths import powershell_exe
from typing import Any, Dict, Tuple

from ..security_reader import _windows_build
from ..security_reader import (
    check_account_lockout, check_anonymous_restrict, check_audit_policy,
    check_autologon, check_cached_logons, check_credential_guard,
    check_ctrl_alt_del, check_guest_account, check_last_username_hidden,
    check_lsass_protection, check_ntlm_level, check_ntlm_relay_protection,
    check_password_min_length, check_ps_constrained_lang,
    check_ps_execution_policy, check_ps_script_block_logging,
    check_ps_transcription, check_sam_hive_permissions,
    check_screensaver_active, check_screensaver_secure, check_uac,
    check_wdigest, check_windows_hello,
)
from .model import Category, Risk, SecurityControl

_POLICIES_SYSTEM = (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
                    r"\Policies\System")
_WINLOGON = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
_LSA = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
_MSV1_0 = _LSA + r"\MSV1_0"
_WDIGEST = r"HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest"
_DEVICE_GUARD = r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard"
_CREDENTIAL_GUARD = _DEVICE_GUARD + r"\Scenarios\CredentialGuard"
_PS_POLICY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell"


def _dword(key: str, value: str, data: int) -> Dict[str, Any]:
    return {"type": "registry", "key": key, "value": value, "data": data,
            "kind": "DWORD"}


def _sz(key: str, value: str, data: str) -> Dict[str, Any]:
    return {"type": "registry", "key": key, "value": value, "data": data,
            "kind": "SZ"}


#: The only tokens Set-ExecutionPolicy accepts. This doubles as the guard that
#: keeps a string read off the machine out of a shell command: a `from_value`
#: not in this map yields no revert command at all.
_EXECUTION_POLICIES = {name: name for name in
                       ("Restricted", "AllSigned", "RemoteSigned",
                        "Unrestricted", "Bypass", "Undefined")}


def _execution_policy_step(policy: str) -> Dict[str, Any]:
    prefix = f'"{powershell_exe()}" -NoProfile -Command Set-ExecutionPolicy '
    return {"type": "script",
            "command": f"{prefix}{policy} -Scope LocalMachine -Force",
            "revert_template": prefix + "${old} -Scope LocalMachine -Force",
            "revert_values": _EXECUTION_POLICIES}


def _cached_logon_steps(value: Any) -> Tuple[Dict, ...]:
    if type(value) is not int or not 0 <= value <= 50:
        raise ValueError("cached_logons target must be an integer in 0..50")
    return (_sz(_WINLOGON, "CachedLogonsCount", str(value)),)


def _lsa_steps(enabled: Any) -> Tuple[Dict, ...]:
    if not isinstance(enabled, bool):
        raise ValueError("lsass_protection target must be a boolean")
    build = _windows_build()
    if enabled and build is None:
        raise ValueError("lsass_protection target requires a known Windows build")
    value = (2 if build >= 22621 else 1) if enabled else 0
    return (_dword(_LSA, "RunAsPPL", value),)


CONTROLS: Tuple[SecurityControl, ...] = (

    # -- the elevation boundary --------------------------------------------
    SecurityControl(
        id="uac",
        title="User Account Control",
        category=Category.ACCOUNTS,
        description="The prompt that separates a standard token from an "
                    "administrator one.",
        why_it_matters="With EnableLUA off there is no elevation boundary at "
                       "all: every process an administrator starts is already "
                       "fully privileged, and no prompt ever appears.",
        reader=check_uac,
        on_steps=(_dword(_POLICIES_SYSTEM, "EnableLUA", 1),),
        off_steps=(_dword(_POLICIES_SYSTEM, "EnableLUA", 0),),
        desired=True,
        risk=Risk.HIGH,
        requires_reboot=True,
    ),

    # -- accounts -----------------------------------------------------------
    SecurityControl(
        id="guest_account",
        title="Guest account",
        category=Category.ACCOUNTS,
        description="The built-in Guest account, which has no password.",
        why_it_matters="An enabled Guest account is an unauthenticated "
                       "foothold, and it is a member of Everyone.",
        reader=check_guest_account,
        on_steps=({"type": "command", "cmd": "net user guest /active:yes"},),
        off_steps=({"type": "command", "cmd": "net user guest /active:no"},),
        desired=False,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="autologon",
        title="Automatic logon",
        category=Category.ACCOUNTS,
        description="Whether Windows signs a user in without asking for a "
                    "password.",
        why_it_matters="Auto-logon keeps the password in the registry in "
                       "cleartext (DefaultPassword), and anyone who can boot "
                       "the machine is already signed in as that user.",
        reader=check_autologon,
        on_steps=(_sz(_WINLOGON, "AutoAdminLogon", "1"),),
        off_steps=(_sz(_WINLOGON, "AutoAdminLogon", "0"),),
        desired=False,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="last_username_hidden",
        title="Hide the last signed-in user",
        category=Category.ACCOUNTS,
        description="Whether the logon screen shows who signed in last.",
        why_it_matters="A displayed username is half of a credential, handed "
                       "to anyone who walks past the machine.",
        reader=check_last_username_hidden,
        on_steps=(_dword(_POLICIES_SYSTEM, "DontDisplayLastUserName", 1),),
        off_steps=(_dword(_POLICIES_SYSTEM, "DontDisplayLastUserName", 0),),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="ctrl_alt_del_required",
        title="Ctrl+Alt+Del required at sign-in",
        category=Category.ACCOUNTS,
        description="The secure attention sequence, which only the kernel can "
                    "intercept.",
        why_it_matters="It is what guarantees the logon box you are typing "
                       "into is Windows' own and not a program imitating it.",
        reader=check_ctrl_alt_del,
        on_steps=(_dword(_POLICIES_SYSTEM, "DisableCAD", 0),),
        off_steps=(_dword(_POLICIES_SYSTEM, "DisableCAD", 1),),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="account_lockout",
        title="Account lockout threshold",
        category=Category.ACCOUNTS,
        description="How many failed sign-ins lock an account.",
        why_it_matters="Without a threshold, a local account can be guessed "
                       "at indefinitely, which is exactly what an offline "
                       "password spray does.",
        reader=check_account_lockout,
        on_steps=({"type": "command", "cmd": "net accounts /lockoutthreshold:10"},),
        off_steps=({"type": "command", "cmd": "net accounts /lockoutthreshold:0"},),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="password_min_length",
        title="Minimum password length",
        category=Category.ACCOUNTS,
        description="The shortest password a local account may have.",
        why_it_matters="Length is the only property that reliably defeats "
                       "offline cracking of a stolen hash.",
        reader=check_password_min_length,
        read_value=lambda d: d.get("length"),
        on_steps=({"type": "command", "cmd": "net accounts /minpwlen:14"},),
        off_steps=({"type": "command", "cmd": "net accounts /minpwlen:0"},),
        desired=14,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="cached_logons",
        title="Cached domain logons",
        category=Category.ACCOUNTS,
        description="How many previous sign-ins are cached so a user can log "
                    "in with the network unreachable (0..50 cached sign-ins).",
        why_it_matters="Each cached logon is a credential verifier sitting on "
                       "disk for an offline attack. A machine that is never "
                       "off-network needs none; a laptop needs at least one.",
        reader=check_cached_logons,
        read_value=lambda d: d.get("count"),
        on_steps=(_sz(_WINLOGON, "CachedLogonsCount", "4"),),
        off_steps=(_sz(_WINLOGON, "CachedLogonsCount", "10"),),
        desired=4,
        steps_builder=_cached_logon_steps,
        risk=Risk.MEDIUM,
    ),

    # -- what is in LSASS memory -------------------------------------------
    SecurityControl(
        id="wdigest_credential_caching",
        title="WDigest credential caching",
        category=Category.ACCOUNTS,
        description="Whether WDigest keeps plaintext credentials in LSASS "
                    "memory.",
        why_it_matters="With this on, mimikatz reads your password in "
                       "cleartext out of memory rather than a hash it has to "
                       "crack. There is no modern reason to enable it.",
        reader=check_wdigest,
        on_steps=(_dword(_WDIGEST, "UseLogonCredential", 1),),
        off_steps=(_dword(_WDIGEST, "UseLogonCredential", 0),),
        desired=False,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="lsass_protection",
        title="LSASS run as a protected process",
        category=Category.ACCOUNTS,
        description="RunAsPPL: only Microsoft-signed code may open the "
                    "process that holds every credential on the machine. "
                    "Uses 2 without a UEFI lock on Windows 11 22H2+; "
                    "otherwise 1 creates a firmware lock on Secure Boot/UEFI "
                    "machines that a registry revert does not remove.",
        why_it_matters="It is the single control that stops a credential "
                       "dumper from reading LSASS even as SYSTEM. It can also "
                       "block a debugger or an antivirus plug-in that expects "
                       "to inject there.",
        reader=check_lsass_protection,
        steps_builder=_lsa_steps,
        on_steps=(_dword(_LSA, "RunAsPPL", 1),),
        off_steps=(_dword(_LSA, "RunAsPPL", 0),),
        desired=True,
        risk=Risk.MEDIUM,
        requires_reboot=True,
    ),

    SecurityControl(
        id="credential_guard",
        title="Credential Guard",
        category=Category.ACCOUNTS,
        description="Moves derived credentials into a VBS-isolated process "
                    "the normal kernel cannot read.",
        why_it_matters="It puts hashes and Kerberos tickets somewhere that "
                       "administrator rights on the running system do not "
                       "reach at all.",
        reader=check_credential_guard,
        on_steps=(_dword(_DEVICE_GUARD, "EnableVirtualizationBasedSecurity", 1),
                  _dword(_DEVICE_GUARD, "RequirePlatformSecurityFeatures", 1),
                  _dword(_CREDENTIAL_GUARD, "Enabled", 1)),
        off_steps=(_dword(_CREDENTIAL_GUARD, "Enabled", 0),),
        desired=True,
        risk=Risk.HIGH,
        requires_reboot=True,
    ),

    # -- NTLM ---------------------------------------------------------------
    SecurityControl(
        id="ntlm_level",
        title="LAN Manager authentication level",
        category=Category.ACCOUNTS,
        description="Which authentication protocols this machine will send "
                    "and accept. 5 is NTLMv2 only, refusing LM and NTLM.",
        why_it_matters="Below level 3 this machine will send an LM or NTLMv1 "
                       "response, and both are trivially cracked from a "
                       "captured handshake.",
        reader=check_ntlm_level,
        read_value=lambda d: d.get("level"),
        on_steps=(_dword(_LSA, "LmCompatibilityLevel", 5),),
        off_steps=(_dword(_LSA, "LmCompatibilityLevel", 3),),
        desired=5,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="ntlm_relay_protection",
        title="Restrict NTLM traffic",
        category=Category.ACCOUNTS,
        description="Blocks this machine from sending or accepting NTLM "
                    "authentication at all.",
        why_it_matters="Every coercion attack -- PetitPotam, DFSCoerce, "
                       "PrinterBug -- ends in a relayed NTLM authentication. "
                       "Refusing NTLM removes the whole class, and will break "
                       "any share or application that has no Kerberos path.",
        reader=check_ntlm_relay_protection,
        on_steps=(_dword(_MSV1_0, "RestrictSendingNTLMTraffic", 2),
                  _dword(_MSV1_0, "RestrictReceivingNTLMTraffic", 2)),
        off_steps=(_dword(_MSV1_0, "RestrictSendingNTLMTraffic", 0),
                   _dword(_MSV1_0, "RestrictReceivingNTLMTraffic", 0)),
        desired=True,
        risk=Risk.HIGH,
    ),

    SecurityControl(
        id="anonymous_restrict",
        title="Restrict anonymous enumeration",
        category=Category.ACCOUNTS,
        description="Stops an unauthenticated session listing this machine's "
                    "accounts and shares.",
        why_it_matters="Anonymous enumeration hands an attacker the user list "
                       "to spray against, before authenticating at all.",
        reader=check_anonymous_restrict,
        on_steps=(_dword(_LSA, "RestrictAnonymous", 1),
                  _dword(_LSA, "RestrictAnonymousSAM", 1)),
        off_steps=(_dword(_LSA, "RestrictAnonymous", 0),
                   _dword(_LSA, "RestrictAnonymousSAM", 1)),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    # -- PowerShell ---------------------------------------------------------
    SecurityControl(
        id="ps_script_block_logging",
        title="PowerShell script block logging",
        category=Category.ACCOUNTS,
        description="Records every block of PowerShell that runs, including "
                    "code that was decoded or generated at runtime.",
        why_it_matters="It is the one log that survives obfuscation: whatever "
                       "the script decodes itself into is what gets written "
                       "to the event log.",
        reader=check_ps_script_block_logging,
        on_steps=(_dword(_PS_POLICY + r"\ScriptBlockLogging",
                         "EnableScriptBlockLogging", 1),),
        off_steps=(_dword(_PS_POLICY + r"\ScriptBlockLogging",
                          "EnableScriptBlockLogging", 0),),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="ps_transcription",
        title="PowerShell transcription",
        category=Category.ACCOUNTS,
        description="Writes a transcript of every PowerShell session to disk.",
        why_it_matters="Complete, readable history of what was run -- and a "
                       "steady stream of files that may themselves contain "
                       "secrets someone typed. The catalog has no opinion "
                       "here (`desired=None`): it is a real trade, and where "
                       "the transcripts land matters as much as the switch.",
        reader=check_ps_transcription,
        on_steps=(_dword(_PS_POLICY + r"\Transcription",
                         "EnableTranscripting", 1),),
        off_steps=(_dword(_PS_POLICY + r"\Transcription",
                          "EnableTranscripting", 0),),
        desired=None,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="ps_execution_policy",
        title="PowerShell execution policy",
        category=Category.ACCOUNTS,
        description="Which scripts PowerShell will run without complaint.",
        why_it_matters="Microsoft is explicit that this is not a security "
                       "boundary -- `-ExecutionPolicy Bypass` and piping a "
                       "script to stdin both sidestep it entirely. It stops "
                       "accidents, not attackers, so the catalog has no "
                       "opinion on its value.",
        reader=check_ps_execution_policy,
        read_value=lambda d: d.get("policy"),
        on_steps=(_execution_policy_step("RemoteSigned"),),
        off_steps=(_execution_policy_step("Undefined"),),
        desired=None,
        risk=Risk.LOW,
    ),

    # -- read-only, each with the reason -----------------------------------
    SecurityControl(
        id="ps_constrained_language",
        read_value=lambda d: d.get("status"),
        title="PowerShell Constrained Language Mode",
        category=Category.ACCOUNTS,
        description="Restricts PowerShell to a subset of the language with no "
                    "arbitrary .NET or Win32 calls.",
        why_it_matters="Most offensive PowerShell needs Add-Type or direct "
                       "Win32 calls, and constrained mode removes both.",
        reader=check_ps_constrained_lang,
        read_only_reason="Setting __PSLockdownPolicy on its own is not a "
                         "security boundary and Microsoft does not support it "
                         "as one: without AppLocker or WDAC deciding which "
                         "code is trusted, any script can start a new "
                         "PowerShell in full language mode. It follows from "
                         "an application-control policy rather than being a "
                         "switch of its own.",
    ),

    SecurityControl(
        id="sam_hive_permissions",
        title="SAM hive permissions (HiveNightmare, CVE-2021-36934)",
        category=Category.ACCOUNTS,
        description="Whether ordinary users can read the SAM, SYSTEM and "
                    "SECURITY hives out of a shadow copy.",
        why_it_matters="Readable hives hand every local password hash to any "
                       "user on the machine, with no elevation at all.",
        reader=check_sam_hive_permissions,
        read_only_reason="The fix is two steps and the second is destructive: "
                         "reset the ACLs (icacls %windir%\\system32\\config"
                         "\\*.* /inheritance:e) and then DELETE every existing "
                         "volume shadow copy, because the old permissions live "
                         "on inside them. Deleting a machine's restore points "
                         "is not something to do from a toggle.",
    ),

    SecurityControl(
        id="windows_hello",
        title="Windows Hello",
        category=Category.ACCOUNTS,
        description="PIN, fingerprint or face unlock, backed by the TPM.",
        why_it_matters="A Hello PIN never leaves the machine and cannot be "
                       "replayed anywhere else, unlike a password.",
        reader=check_windows_hello,
        read_only_reason="Enrolment is per user and interactive -- the user "
                         "has to present the PIN or biometric themselves. The "
                         "policy value only makes it available; it cannot "
                         "enrol anyone.",
    ),

    SecurityControl(
        id="screensaver_lock",
        title="Lock on screen saver resume",
        category=Category.ACCOUNTS,
        description="Whether returning from the screen saver asks for "
                    "credentials.",
        why_it_matters="It is the control that makes an unattended machine "
                       "lock itself.",
        reader=check_screensaver_secure,
        read_only_reason="ScreenSaverIsSecure lives in HKCU, and the elevated "
                         "helper would write the administrator's hive rather "
                         "than the signed-in user's. Settings > Accounts > "
                         "Sign-in options, per user.",
    ),

    SecurityControl(
        id="screensaver_configured",
        title="Screen saver configured",
        category=Category.ACCOUNTS,
        description="Whether a screen saver is set at all, which is what "
                    "gives the lock-on-resume setting something to trigger.",
        why_it_matters="Lock on resume does nothing if nothing ever resumes.",
        reader=check_screensaver_active,
        read_only_reason="Also an HKCU value, and choosing a screen saver and "
                         "its timeout is the user's call. Same hive problem "
                         "as the lock setting above.",
    ),

    SecurityControl(
        id="audit_policy",
        title="Advanced audit policy",
        category=Category.ACCOUNTS,
        description="How many audit subcategories are recording success or "
                    "failure events.",
        why_it_matters="Auditing is what makes an intrusion reconstructable "
                       "afterwards. Nothing is audited by default.",
        reader=check_audit_policy,
        read_only_reason="auditpol sets one subcategory at a time -- there "
                         "are 60 -- and which ones to enable depends on what "
                         "collects the events. A single switch here would "
                         "either do too little to matter or flood the "
                         "Security log. It also needs administrator rights "
                         "even to READ, which is why this card is blank "
                         "unelevated.",
    ),
)
