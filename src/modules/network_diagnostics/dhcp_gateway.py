"""DHCP Lease Expiration & Gateway Inspector.

Parses active adapter DHCP leases, gateway bindings, and expiration timers from ipconfig /all.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AdapterDhcpInfo:
    adapter_name: str
    description: str
    is_connected: bool = True
    dhcp_enabled: bool = False
    ip_address: str = ""
    subnet_mask: str = ""
    default_gateway: str = ""
    dhcp_server: str = ""
    lease_obtained_str: str = ""
    lease_expires_str: str = ""
    lease_remaining_hours: Optional[float] = None
    dns_servers: List[str] = field(default_factory=list)
    status_summary: str = "Active"


def parse_ipconfig_all(text: str) -> List[AdapterDhcpInfo]:
    """Parse ipconfig /all into structured AdapterDhcpInfo records."""
    adapters: List[AdapterDhcpInfo] = []
    current_adapter: Optional[str] = None
    fields: Dict[str, Any] = {}

    def flush():
        if current_adapter and fields.get("description"):
            ip = fields.get("ipv4 address", "").split("(")[0].strip()
            conn = "media disconnected" not in fields.get("media state", "").lower()
            dhcp_on = fields.get("dhcp enabled", "").lower() == "yes"

            obtained = fields.get("lease obtained", "")
            expires = fields.get("lease expires", "")

            rem_hours = None
            if expires:
                # Format: Friday, November 20, 2026 7:29:22 PM
                # Try parsing date
                for fmt in ("%A, %B %d, %Y %I:%M:%S %p", "%B %d, %Y %I:%M:%S %p"):
                    try:
                        exp_dt = datetime.strptime(expires.strip(), fmt)
                        delta = exp_dt - datetime.now()
                        rem_hours = max(0.0, delta.total_seconds() / 3600.0)
                        break
                    except Exception as e:
                        logger.debug("Failed parsing lease expiry with format %s: %s", fmt, e)

            dns_raw = fields.get("dns servers", [])
            dns_list = dns_raw if isinstance(dns_raw, list) else [dns_raw]

            adapters.append(AdapterDhcpInfo(
                adapter_name=current_adapter,
                description=fields.get("description", ""),
                is_connected=conn,
                dhcp_enabled=dhcp_on,
                ip_address=ip,
                subnet_mask=fields.get("subnet mask", ""),
                default_gateway=fields.get("default gateway", ""),
                dhcp_server=fields.get("dhcp server", ""),
                lease_obtained_str=obtained,
                lease_expires_str=expires,
                lease_remaining_hours=rem_hours,
                dns_servers=[d for d in dns_list if d],
                status_summary=f"IP: {ip} | Gateway: {fields.get('default gateway', 'None')} | Lease: {expires or 'Static'}",
            ))

    for line in text.splitlines():
        # Match adapter section header e.g. "Ethernet adapter Ethernet 2:"
        header_m = re.match(r"^([A-Za-z0-9\s\-]+ adapter\s+[^:]+):$", line.strip())
        if header_m:
            flush()
            fields = {}
            current_adapter = header_m.group(1).strip()
            continue

        if ":" in line:
            parts = line.split(":", 1)
            k = parts[0].replace(".", "").strip().lower()
            v = parts[1].strip()
            if k == "dns servers":
                fields.setdefault("dns servers", []).append(v)
            else:
                fields[k] = v
        elif line.strip() and "dns servers" in fields:
            # Multi-line DNS entries
            extra_dns = line.strip()
            if re.match(r"^[\d.]+$", extra_dns):
                fields["dns servers"].append(extra_dns)

    flush()
    return adapters


def get_dhcp_adapters_info() -> List[AdapterDhcpInfo]:
    """Execute ipconfig /all and return parsed adapter list."""
    try:
        proc = subprocess.run(
            ["ipconfig.exe", "/all"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout:
            return parse_ipconfig_all(proc.stdout)
    except Exception as exc:
        logger.warning("Error running ipconfig /all: %s", exc)
    return []


def renew_dhcp_lease(adapter_name: str = "") -> Tuple[bool, str]:
    """Trigger ipconfig /renew to re-negotiate DHCP lease."""
    cmd = ["ipconfig.exe", "/renew"]
    if adapter_name:
        cmd.append(adapter_name)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except Exception as exc:
        return False, f"Failed to renew DHCP lease: {exc}"
