"""Qt-free remote-access exposure summary: RDP, WinRM, SSH, Remote Assistance.

`gather()` reads the machine (registry, services, listening sockets, the
Remote Desktop Users group, firewall rules); `evaluate()` is pure and turns a
`RemoteState` into rows + findings. A field that could not be read is None and
becomes "unknown" -- never "off". RDP being off by `fDenyTSConnections` is a
definite answer; a service we could not query is not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SEV_HIGH, SEV_MEDIUM, SEV_LOW, SEV_INFO, SEV_OK = "high", "medium", "low", "info", "ok"
_SEV_ORDER = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2, SEV_INFO: 3, SEV_OK: 4}

JUMP_FIREWALL = "Firewall Rules"
JUMP_SERVICES = "System Management"


@dataclass
class ServiceInfo:
    exists: Optional[bool]           # None = query failed
    running: Optional[bool] = None
    start_type: str = ""


@dataclass
class RemoteState:
    rdp_enabled: Optional[bool] = None       # fDenyTSConnections == 0
    rdp_nla: Optional[bool] = None
    rdp_port: Optional[int] = None
    rdp_security_layer: Optional[int] = None  # 0 RDP, 1 negotiate, 2 TLS
    rdp_users: Optional[List[str]] = None     # Remote Desktop Users members
    rdp_listening: Optional[bool] = None
    assist_enabled: Optional[bool] = None
    assist_full_control: Optional[bool] = None
    services: Dict[str, ServiceInfo] = field(default_factory=dict)
    listening: Optional[Dict[int, bool]] = None   # port -> listening (any address)
    fw_enabled: Optional[Dict[str, int]] = None    # feature -> count of ENABLED inbound allow rules
    winrm_trusted_hosts: Optional[str] = None      # None = unreadable; "" = none configured
    winrm_listener_readable: Optional[bool] = None  # WSMAN\Service branch openable at all
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class Row:
    feature: str
    status: str            # "On" / "Off" / "Unknown"
    summary: str
    severity: str
    findings: List[str] = field(default_factory=list)
    jump: str = ""         # module name to navigate to


def _svc(state: RemoteState, name: str) -> Optional[ServiceInfo]:
    return state.services.get(name)


def _fw_line(state: RemoteState, key: str) -> str:
    if state.fw_enabled is None:
        return "firewall rules unreadable"
    n = state.fw_enabled.get(key, 0)
    return "%d enabled inbound allow rule(s)" % n if n else "no enabled inbound allow rule"


def _rdp_row(s: RemoteState) -> Row:
    if s.rdp_enabled is None:
        return Row("Remote Desktop", "Unknown", s.errors.get("rdp", "could not read the RDP setting"),
                   SEV_INFO, jump=JUMP_SERVICES)
    if not s.rdp_enabled:
        return Row("Remote Desktop", "Off", "Connections are denied (fDenyTSConnections=1).", SEV_OK)
    port = s.rdp_port if s.rdp_port is not None else "?"
    bits, finds, sev = ["port %s" % port, _fw_line(s, "rdp")], [], SEV_LOW
    if s.rdp_nla is False:
        finds.append("Network Level Authentication is OFF: the login screen is reachable before authenticating.")
        sev = SEV_HIGH
    elif s.rdp_nla is None:
        finds.append("Could not read whether NLA is required.")
    else:
        bits.append("NLA required")
    if s.rdp_security_layer == 0:
        finds.append("Security layer is legacy RDP (no TLS).")
        sev = SEV_HIGH
    if s.rdp_port not in (None, 3389):
        bits.append("non-default port")
    if s.rdp_users is not None:
        bits.append("%d user(s) in Remote Desktop Users%s" % (
            len(s.rdp_users), ": " + ", ".join(s.rdp_users[:5]) if s.rdp_users else "; only Administrators"))
        if any(u.lower().split("\\")[-1] in ("everyone", "users", "authenticated users") for u in s.rdp_users):
            finds.append("A broad group is allowed to log on over RDP.")
            sev = SEV_HIGH
    else:
        bits.append("group membership unreadable")
    if s.rdp_listening is False:
        bits.append("enabled but not listening (service stopped)")
    return Row("Remote Desktop", "On", "; ".join(bits), sev, finds, JUMP_FIREWALL)


def _svc_row(s: RemoteState, feature: str, svc: str, port: int, fwkey: str, jump: str) -> Row:
    info = _svc(s, svc)
    if info is None or info.exists is None:
        return Row(feature, "Unknown", "could not query the %s service" % svc, SEV_INFO, jump=jump)
    if not info.exists:
        return Row(feature, "Off", "%s is not installed." % svc, SEV_OK)
    listening = (s.listening or {}).get(port) if s.listening is not None else None
    if info.running:
        sev = SEV_MEDIUM if listening else SEV_LOW
        return Row(feature, "On", "service running; port %d %s; %s" % (
            port, "LISTENING" if listening else ("not listening" if listening is False else "listener unknown"),
            _fw_line(s, fwkey)), sev,
            ["Reachable if the firewall allows it."] if listening else [], jump)
    return Row(feature, "Off", "%s installed, %s (start type %s)." % (
        svc, "stopped", info.start_type or "?"), SEV_OK if info.start_type.lower() in ("disabled", "manual") else SEV_LOW)


def _winrm_row(s: RemoteState) -> Row:
    row = _svc_row(s, "WinRM / PowerShell remoting", "WinRM", 5985, "winrm", JUMP_SERVICES)
    if row.status != "On":
        return row
    bits = [row.summary]
    finds = list(row.findings)
    sev = row.severity
    if s.winrm_trusted_hosts is None:
        bits.append("TrustedHosts unreadable")
    elif s.winrm_trusted_hosts:
        bits.append("TrustedHosts: %s" % s.winrm_trusted_hosts)
        finds.append("TrustedHosts is set: any listed host can be remoted into without "
                      "Kerberos mutual authentication -- confirm every entry is expected.")
        sev = SEV_MEDIUM if _SEV_ORDER[sev] > _SEV_ORDER[SEV_MEDIUM] else sev
    else:
        bits.append("TrustedHosts empty (Kerberos/domain-joined hosts only)")
    if s.winrm_listener_readable is False:
        bits.append("listener config needs admin to read")
    return Row(row.feature, row.status, "; ".join(bits), sev, finds, row.jump)


def _assist_row(s: RemoteState) -> Row:
    if s.assist_enabled is None:
        return Row("Remote Assistance", "Unknown", "could not read", SEV_INFO)
    if not s.assist_enabled:
        return Row("Remote Assistance", "Off", "Invitations are not allowed.", SEV_OK)
    finds = []
    if s.assist_full_control:
        finds.append("A helper who is invited may take FULL CONTROL, not just view.")
    return Row("Remote Assistance", "On", "Solicited assistance allowed%s; %s" % (
        ", full control permitted" if s.assist_full_control else ", view only", _fw_line(s, "assist")),
        SEV_MEDIUM if s.assist_full_control else SEV_LOW, finds, JUMP_FIREWALL)


def evaluate(state: RemoteState) -> List[Row]:
    rows = [_rdp_row(state), _assist_row(state), _winrm_row(state),
            _svc_row(state, "OpenSSH server", "sshd", 22, "ssh", JUMP_SERVICES)]
    rows.sort(key=lambda r: _SEV_ORDER[r.severity])
    return rows


# ------------------------------------------------------------------- readers

def _dword(winreg, hive, path: str, name: str) -> Optional[int]:
    try:
        with winreg.OpenKey(hive, path) as k:
            return int(winreg.QueryValueEx(k, name)[0])
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Cannot read %s\\%s: %s", path, name, exc)
        raise


def read_registry(state: RemoteState) -> None:
    import winreg
    ts = r"SYSTEM\CurrentControlSet\Control\Terminal Server"
    tcp = ts + r"\WinStations\RDP-Tcp"
    try:
        deny = _dword(winreg, winreg.HKEY_LOCAL_MACHINE, ts, "fDenyTSConnections")
        state.rdp_enabled = None if deny is None else deny == 0
        nla = _dword(winreg, winreg.HKEY_LOCAL_MACHINE, tcp, "UserAuthentication")
        state.rdp_nla = None if nla is None else nla == 1
        state.rdp_port = _dword(winreg, winreg.HKEY_LOCAL_MACHINE, tcp, "PortNumber")
        state.rdp_security_layer = _dword(winreg, winreg.HKEY_LOCAL_MACHINE, tcp, "SecurityLayer")
        ra = r"SYSTEM\CurrentControlSet\Control\Remote Assistance"
        allow = _dword(winreg, winreg.HKEY_LOCAL_MACHINE, ra, "fAllowToGetHelp")
        state.assist_enabled = None if allow is None else allow == 1
        full = _dword(winreg, winreg.HKEY_LOCAL_MACHINE, ra, "fAllowFullControl")
        state.assist_full_control = None if full is None else full == 1
    except OSError as exc:
        state.errors["rdp"] = "registry read refused: %s" % exc


def read_services(state: RemoteState, names=("TermService", "WinRM", "sshd")) -> None:
    import psutil
    for n in names:
        try:
            svc = psutil.win_service_get(n)
            state.services[n] = ServiceInfo(True, svc.status() == "running", svc.start_type())
        except psutil.NoSuchProcess:
            state.services[n] = ServiceInfo(False)
        except Exception as exc:  # psutil raises OSError/AccessDenied variants
            logger.warning("Cannot query service %s: %s", n, exc)
            state.services[n] = ServiceInfo(None)


def read_listeners(state: RemoteState, ports=(3389, 5985, 5986, 22)) -> None:
    import psutil
    try:
        socks = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError) as exc:
        state.errors["listening"] = str(exc)
        return
    live = {c.laddr.port for c in socks if c.status == psutil.CONN_LISTEN and c.laddr}
    state.listening = {p: p in live for p in ports}
    port = state.rdp_port or 3389
    state.rdp_listening = port in live


_WSMAN_CLIENT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Client"
_WSMAN_SERVICE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Service"


def read_winrm_trusted_hosts(state: RemoteState) -> None:
    """`TrustedHosts` under the WSMAN Client registry branch, no subprocess.

    Confirmed live: this branch stays readable unelevated even on a machine
    where WinRM has never been configured (service Stopped/Manual, no
    `winrm quickconfig` ever run) -- `winrm enumerate`/`Get-Item WSMan:\\...`
    both fail here with "cannot connect"/"path does not exist" because the
    WSMan PowerShell provider talks to the WinRM SERVICE, not the registry,
    and a stopped service can't answer either. Reading the registry directly
    sidesteps that: the key exists with zero values in that state, so a
    missing value means "no hosts trusted" (the real default), not "refused".
    """
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WSMAN_CLIENT) as key:
            try:
                state.winrm_trusted_hosts = str(winreg.QueryValueEx(key, "TrustedHosts")[0])
            except FileNotFoundError:
                state.winrm_trusted_hosts = ""
    except OSError as exc:
        logger.warning("Cannot read WinRM TrustedHosts: %s", exc)
        state.errors["winrm_trustedhosts"] = str(exc)


def read_winrm_listener_access(state: RemoteState) -> None:
    """Whether the WSMAN Service registry branch -- where listener config
    (transport, port, HTTPS cert thumbprint) actually lives -- can even be
    opened. Confirmed live: this branch refuses with "Requested registry
    access is not allowed" UNELEVATED even though the sibling Client branch
    (TrustedHosts, above) is readable -- a real, narrower ACL, not a generic
    HKLM restriction. So listener detail genuinely needs admin; this only
    records that refusal for the row to explain, never guesses a value.
    """
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WSMAN_SERVICE):
            state.winrm_listener_readable = True
    except OSError as exc:
        state.winrm_listener_readable = False
        state.errors["winrm_listener"] = str(exc)


def read_rdp_users(state: RemoteState) -> None:
    try:
        import win32net
        res = win32net.NetLocalGroupGetMembers(None, "Remote Desktop Users", 1)[0]
        state.rdp_users = [m["name"] for m in res]
    except Exception as exc:
        logger.warning("Cannot list Remote Desktop Users: %s", exc)
        state.errors["rdp_users"] = str(exc)


_FW_KEYS = (("rdp", ("remote desktop",)), ("winrm", ("windows remote management",)),
            ("ssh", ("openssh",)), ("assist", ("remote assistance",)))


def count_fw_rules(rules) -> Dict[str, int]:
    """Enabled inbound allow rules per feature, matched on the rule NAME."""
    out = {k: 0 for k, _ in _FW_KEYS}
    for r in rules:
        if r.enabled == "Yes" and r.direction == "In" and r.action == "Allow":
            n = r.name.lower()
            for key, needles in _FW_KEYS:
                if any(x in n for x in needles):
                    out[key] += 1
    return out


def read_firewall(state: RemoteState, reader: Optional[Callable] = None) -> None:
    try:
        if reader is None:
            from modules.firewall_rules import firewall_audit as fa
            reader = lambda: fa.read_registry_rules(fa.resolve_indirect)[0]  # noqa: E731
        state.fw_enabled = count_fw_rules(reader())
    except OSError as exc:
        logger.warning("Cannot read firewall rules: %s", exc)
        state.errors["firewall"] = str(exc)


def gather() -> RemoteState:
    st = RemoteState()
    read_registry(st)
    read_services(st)
    read_listeners(st)
    read_rdp_users(st)
    read_firewall(st)
    read_winrm_trusted_hosts(st)
    read_winrm_listener_access(st)
    return st
