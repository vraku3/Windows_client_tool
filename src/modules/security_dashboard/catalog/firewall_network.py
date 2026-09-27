"""Firewall and network-exposure controls.

Registry-first, deliberately: BackupService can restore a registry value
exactly, and cannot revert a command. Where the only real writer is a service
state or a cmdlet, that is what the steps use, and the reason is in the entry.

Two things recur here and are stated once:

* `enabled` in these readers means **the feature is on**, not "the hardening
  is applied". So LLMNR, mDNS, NetBIOS, RDP, WinRM, Remote Registry, Telnet,
  SMBv1 and admin shares all carry `desired=False` -- the safe state is the
  thing being OFF.
* An HKCU value cannot be written by the elevated helper. It would land in the
  administrator's hive, not the signed-in user's, and the verify pass would
  then read the user's hive and correctly report that nothing changed. WPAD is
  read-only here for exactly that reason.
"""
from typing import Any, Dict, Tuple

from ..security_reader import (
    check_admin_shares, check_firewall, check_firewall_outbound, check_firewall_stealth,
    check_llmnr, check_mdns, check_netbios_tcpip, check_network_profile, check_rdp,
    check_rdp_nla, check_remote_registry, check_smb_signing, check_smbv1,
    check_telnet, check_winrm, check_wpad,
)
from .model import Category, Risk, SecurityControl

_DNSCLIENT_POLICY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"
_DNSCACHE = r"HKLM\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters"
_NETBT = r"HKLM\SYSTEM\CurrentControlSet\Services\NetBT\Parameters"
_LANMAN_WORKSTATION = (r"HKLM\SYSTEM\CurrentControlSet\Services"
                       r"\LanmanWorkstation\Parameters")
_LANMAN_SERVER = (r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer"
                  r"\Parameters")
_TERMINAL_SERVER = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
_RDP_TCP = _TERMINAL_SERVER + r"\WinStations\RDP-Tcp"

_PS = "powershell -NoProfile -Command "


def _dword(key: str, value: str, data: int) -> Dict[str, Any]:
    return {"type": "registry", "key": key, "value": value, "data": data,
            "kind": "DWORD"}


def _service(name: str, start_type: str) -> Dict[str, Any]:
    return {"type": "service", "name": name, "start_type": start_type}


#: Set-NetFirewallProfile takes the profile state by name.
_REVERT_TRUE_FALSE = {"True": "True", "False": "False"}
#: `firewall_inbound_blocked` reads True when nothing is explicitly allowed,
#: so reverting means putting the profiles back to NotConfigured -- which is
#: what they were, and which Windows treats as block.
_REVERT_INBOUND = {"True": "NotConfigured", "False": "Allow"}


def _profile_step(assignment: str, revert_assignment: str,
                  revert_values: Dict[str, str]) -> Dict[str, Any]:
    """One Set-NetFirewallProfile script step, with its way back."""
    return {"type": "script",
            "command": _PS + "Set-NetFirewallProfile -All " + assignment,
            "revert_template": _PS + "Set-NetFirewallProfile -All "
                                     + revert_assignment,
            "revert_values": revert_values}


CONTROLS: Tuple[SecurityControl, ...] = (

    # -- the firewall itself -----------------------------------------------
    SecurityControl(
        id="firewall_enabled",
        title="Windows Firewall",
        category=Category.FIREWALL_NETWORK,
        description="The firewall, on all three profiles: Domain, Private "
                    "and Public.",
        why_it_matters="It is the only thing between a listening service on "
                       "this machine and everyone else on the network.",
        reader=check_firewall,
        # The reader answers per profile and has no single `enabled`; all
        # three must be on for this to count as applied.
        read_value=lambda d: (all(d["profiles"].values())
                              if d.get("profiles") else None),
        on_steps=(_profile_step("-Enabled True", "-Enabled ${old}",
                                _REVERT_TRUE_FALSE),),
        off_steps=(_profile_step("-Enabled False", "-Enabled ${old}",
                                 _REVERT_TRUE_FALSE),),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="firewall_inbound_blocked",
        title="Default inbound action: block",
        category=Category.FIREWALL_NETWORK,
        description="What the firewall does with inbound traffic that matches "
                    "no rule.",
        why_it_matters="Blocking by default is what makes a new listening "
                       "service invisible until someone deliberately opens a "
                       "hole for it.",
        reader=check_firewall_stealth,
        on_steps=(_profile_step("-DefaultInboundAction Block",
                                "-DefaultInboundAction ${old}",
                                _REVERT_INBOUND),),
        off_steps=(_profile_step("-DefaultInboundAction NotConfigured",
                                 "-DefaultInboundAction ${old}",
                                 _REVERT_INBOUND),),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="firewall_outbound_blocked",
        title="Default outbound action: block",
        category=Category.FIREWALL_NETWORK,
        description="What the firewall does with outbound traffic that matches "
                    "no rule.",
        why_it_matters="Windows allows all outbound traffic by default -- even "
                       "when this reads 'Not Configured' -- so malware phoning "
                       "home or exfiltrating data needs no rule of its own to "
                       "succeed. Blocking by default is real hardening most "
                       "environments skip, because every legitimate program "
                       "then needs its own allow rule.",
        reader=check_firewall_outbound,
        on_steps=(_profile_step("-DefaultOutboundAction Block",
                                "-DefaultOutboundAction ${old}",
                                _REVERT_INBOUND),),
        off_steps=(_profile_step("-DefaultOutboundAction NotConfigured",
                                 "-DefaultOutboundAction ${old}",
                                 _REVERT_INBOUND),),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    # -- name resolution: the three protocols Responder answers -------------
    SecurityControl(
        id="llmnr",
        title="LLMNR (Link-Local Multicast Name Resolution)",
        category=Category.FIREWALL_NETWORK,
        description="Legacy name resolution used when DNS has no answer.",
        why_it_matters="Anyone on the same network can answer an LLMNR query "
                       "and receive this machine's NTLMv2 hash. It is the "
                       "first thing Responder does, and almost nothing needs "
                       "it.",
        reader=check_llmnr,
        on_steps=(_dword(_DNSCLIENT_POLICY, "EnableMulticast", 1),),
        off_steps=(_dword(_DNSCLIENT_POLICY, "EnableMulticast", 0),),
        desired=False,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="mdns",
        title="mDNS (multicast DNS)",
        category=Category.FIREWALL_NETWORK,
        description="Multicast name resolution for .local names.",
        why_it_matters="The same spoofing risk as LLMNR, over a different "
                       "protocol. Turning it off also stops local discovery "
                       "of printers and cast devices, which is what you give "
                       "up.",
        reader=check_mdns,
        on_steps=(_dword(_DNSCACHE, "EnableMDNS", 1),),
        off_steps=(_dword(_DNSCACHE, "EnableMDNS", 0),),
        desired=False,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="netbios_over_tcpip",
        title="NetBIOS over TCP/IP",
        category=Category.FIREWALL_NETWORK,
        description="NodeType 2 (P-node) stops NetBIOS name broadcasts "
                    "entirely; the default H-node broadcasts.",
        why_it_matters="NBT-NS is the third protocol in the Responder set, "
                       "and it leaks the same credentials LLMNR does.",
        reader=check_netbios_tcpip,
        read_value=lambda d: d.get("enabled"),
        on_steps=(_dword(_NETBT, "NodeType", 8),),
        off_steps=(_dword(_NETBT, "NodeType", 2),),
        desired=False,
        risk=Risk.MEDIUM,
        requires_reboot=True,
    ),

    # -- remote access ------------------------------------------------------
    SecurityControl(
        id="rdp",
        title="Remote Desktop",
        category=Category.FIREWALL_NETWORK,
        description="Whether this machine accepts Remote Desktop connections.",
        why_it_matters="RDP exposed to a network is the single most commonly "
                       "brute-forced service on Windows.",
        reader=check_rdp,
        on_steps=(_dword(_TERMINAL_SERVER, "fDenyTSConnections", 0),),
        off_steps=(_dword(_TERMINAL_SERVER, "fDenyTSConnections", 1),),
        desired=False,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="rdp_nla",
        title="Remote Desktop Network Level Authentication",
        category=Category.FIREWALL_NETWORK,
        description="Requires the client to authenticate before a session is "
                    "created.",
        why_it_matters="Without NLA, an unauthenticated attacker reaches the "
                       "session stack itself -- which is how BlueKeep worked.",
        reader=check_rdp_nla,
        on_steps=(_dword(_RDP_TCP, "UserAuthentication", 1),),
        off_steps=(_dword(_RDP_TCP, "UserAuthentication", 0),),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="winrm_service",
        title="WinRM (Windows Remote Management)",
        category=Category.FIREWALL_NETWORK,
        description="The service behind PowerShell Remoting.",
        why_it_matters="A running WinRM is a remote shell waiting for valid "
                       "credentials, and it is the standard lateral-movement "
                       "path once a hash is stolen.",
        reader=check_winrm,
        on_steps=(_service("WinRM", "automatic"),),
        off_steps=(_service("WinRM", "disabled"),),
        desired=False,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="remote_registry_service",
        title="Remote Registry",
        category=Category.FIREWALL_NETWORK,
        description="Lets another machine read and write this one's registry.",
        why_it_matters="It is a remote read of everything on this page, and "
                       "nothing on a workstation needs it.",
        reader=check_remote_registry,
        on_steps=(_service("RemoteRegistry", "manual"),),
        off_steps=(_service("RemoteRegistry", "disabled"),),
        desired=False,
        risk=Risk.LOW,
    ),

    # -- SMB ----------------------------------------------------------------
    SecurityControl(
        id="smb_signing",
        title="SMB signing required",
        category=Category.FIREWALL_NETWORK,
        description="Requires every SMB session to be signed.",
        why_it_matters="Signing is what defeats an SMB relay: a stolen "
                       "authentication cannot be replayed against another "
                       "machine.",
        reader=check_smb_signing,
        on_steps=(_dword(_LANMAN_WORKSTATION, "RequireSecuritySignature", 1),),
        off_steps=(_dword(_LANMAN_WORKSTATION, "RequireSecuritySignature", 0),),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="smbv1",
        title="SMBv1 protocol",
        category=Category.FIREWALL_NETWORK,
        description="The 1990s file-sharing protocol, kept only for very old "
                    "devices.",
        why_it_matters="EternalBlue and WannaCry travelled over SMBv1. It has "
                       "no signing worth the name and Microsoft has been "
                       "removing it for a decade.",
        reader=check_smbv1,
        on_steps=({"type": "command",
                   "cmd": "dism /online /enable-feature "
                          "/featurename:SMB1Protocol /norestart"},),
        off_steps=({"type": "command",
                    "cmd": "dism /online /disable-feature "
                           "/featurename:SMB1Protocol /norestart"},),
        desired=False,
        risk=Risk.MEDIUM,
        requires_reboot=True,
    ),

    SecurityControl(
        id="admin_shares",
        title="Administrative shares (C$, ADMIN$)",
        category=Category.FIREWALL_NETWORK,
        description="The hidden shares that expose every drive root to a "
                    "remote administrator.",
        why_it_matters="They are the destination of most lateral movement: "
                       "one stolen local-admin hash reaches every drive on "
                       "the machine. Turning them off also breaks remote "
                       "management tools that rely on them.",
        reader=check_admin_shares,
        on_steps=(_dword(_LANMAN_SERVER, "AutoShareWks", 1),),
        off_steps=(_dword(_LANMAN_SERVER, "AutoShareWks", 0),),
        desired=False,
        risk=Risk.MEDIUM,
        requires_reboot=True,
    ),

    # -- other exposure -----------------------------------------------------
    SecurityControl(
        id="telnet_client",
        title="Telnet client",
        category=Category.FIREWALL_NETWORK,
        description="The optional Windows feature that provides telnet.exe.",
        why_it_matters="A cleartext protocol with no authentication worth the "
                       "name, and a convenient living-off-the-land tool once "
                       "someone else is on the machine.",
        reader=check_telnet,
        on_steps=({"type": "command",
                   "cmd": "dism /online /enable-feature "
                          "/featurename:TelnetClient /norestart"},),
        off_steps=({"type": "command",
                    "cmd": "dism /online /disable-feature "
                           "/featurename:TelnetClient /norestart"},),
        desired=False,
        risk=Risk.LOW,
    ),

    # -- read-only, each with the reason -----------------------------------
    SecurityControl(
        id="wpad_auto_detect",
        title="WPAD proxy auto-detection",
        category=Category.FIREWALL_NETWORK,
        description="Whether this machine asks the network where its proxy "
                    "configuration lives.",
        why_it_matters="Whoever answers first becomes this machine's proxy "
                       "and sees, or rewrites, its web traffic. This is ON "
                       "here.",
        reader=check_wpad,
        read_only_reason="Auto-detect is a per-user Internet Settings value "
                         "in HKCU. The elevated helper that applies changes "
                         "runs as the administrator, so it would write the "
                         "wrong user's hive and the verify pass would "
                         "correctly report that nothing changed. Turn it off "
                         "in Settings > Network & internet > Proxy, per user.",
    ),

    SecurityControl(
        id="network_profile",
        read_value=lambda d: d.get("status"),
        title="Network profile (Public / Private)",
        category=Category.FIREWALL_NETWORK,
        description="Which firewall profile the current network uses. Public "
                    "is the restrictive one.",
        why_it_matters="On Private, file and printer sharing and network "
                       "discovery are allowed by rules that Public blocks.",
        reader=check_network_profile,
        read_only_reason="The category belongs to a network, not to the "
                         "machine, and this reader sees only the first "
                         "adapter. A control that set every adapter at once "
                         "would move a domain connection out of its profile. "
                         "Change it per network in Settings.",
    ),
)
