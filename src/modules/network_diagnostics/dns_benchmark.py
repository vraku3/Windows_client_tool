"""DNS Benchmark Engine for Windows Client Tool.

Benchmarks DNS resolvers (System DNS, Cloudflare, Google, Quad9, OpenDNS)
against top target domains using pure Python UDP DNS client queries.
Qt-free and SAC-safe.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from modules.network_diagnostics import dns_client

logger = logging.getLogger(__name__)

BENCHMARK_DOMAINS = [
    "google.com",
    "cloudflare.com",
    "microsoft.com",
    "amazon.com",
    "wikipedia.org",
]

KNOWN_RESOLVERS = [
    ("Cloudflare Primary", "1.1.1.1"),
    ("Cloudflare Secondary", "1.0.0.1"),
    ("Google Primary", "8.8.8.8"),
    ("Google Secondary", "8.8.4.4"),
    ("Quad9 (Malware Blocking)", "9.9.9.9"),
    ("OpenDNS Home", "208.67.222.222"),
]


@dataclass
class BenchmarkResult:
    name: str
    server: str
    is_system: bool = False
    samples_sent: int = 0
    samples_ok: int = 0
    rtts: List[float] = field(default_factory=list)
    avg_rtt_ms: Optional[float] = None
    min_rtt_ms: Optional[float] = None
    max_rtt_ms: Optional[float] = None
    is_fastest: bool = False
    sample_answer: str = ""
    error_summary: str = ""

    @property
    def success_rate_pct(self) -> float:
        if self.samples_sent == 0:
            return 0.0
        return (self.samples_ok / self.samples_sent) * 100.0


def get_system_dns_servers() -> List[str]:
    """Parse configured IPv4 DNS servers from netsh."""
    servers: List[str] = []
    seen = set()
    try:
        proc = subprocess.run(
            ["netsh.exe", "interface", "ipv4", "show", "dnsservers"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                line = line.strip()
                # Match IP address at end or stand-alone
                ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
                for ip in ips:
                    if ip not in seen and not ip.startswith("127.") and ip != "0.0.0.0":
                        seen.add(ip)
                        servers.append(ip)
    except Exception as e:
        logger.debug("Failed to query system DNS servers via netsh: %s", e)
    return servers


def run_dns_benchmark(
    custom_servers: Optional[List[Tuple[str, str]]] = None,
    domains: Optional[List[str]] = None,
    timeout: float = 2.0,
    is_cancelled=lambda: False,
) -> List[BenchmarkResult]:
    """Run DNS latency benchmark against target resolvers.

    Returns a list of BenchmarkResult sorted by avg_rtt_ms ascending (fastest first).
    """
    target_domains = domains or BENCHMARK_DOMAINS
    resolvers: List[Tuple[str, str, bool]] = []

    # Add system DNS servers
    sys_dns = get_system_dns_servers()
    for idx, s in enumerate(sys_dns, 1):
        resolvers.append((f"System DNS {idx}", s, True))

    # Add standard public resolvers
    if custom_servers is not None:
        for name, ip in custom_servers:
            if not any(r[1] == ip for r in resolvers):
                resolvers.append((name, ip, False))
    else:
        for name, ip in KNOWN_RESOLVERS:
            if not any(r[1] == ip for r in resolvers):
                resolvers.append((name, ip, False))

    results: List[BenchmarkResult] = []

    for name, server, is_sys in resolvers:
        if is_cancelled():
            break

        b_res = BenchmarkResult(name=name, server=server, is_system=is_sys)
        errors = []

        for domain in target_domains:
            if is_cancelled():
                break

            b_res.samples_sent += 1
            reply = dns_client.query(server, domain, qtype="A", timeout=timeout)
            if reply.ok and reply.resolved and reply.rtt_ms is not None:
                b_res.samples_ok += 1
                b_res.rtts.append(reply.rtt_ms)
                if not b_res.sample_answer and reply.answers:
                    b_res.sample_answer = f"{domain} -> {reply.answers[0]}"
            else:
                err = reply.error or reply.rcode or "Unknown error"
                errors.append(f"{domain}: {err}")

        if b_res.rtts:
            b_res.avg_rtt_ms = sum(b_res.rtts) / len(b_res.rtts)
            b_res.min_rtt_ms = min(b_res.rtts)
            b_res.max_rtt_ms = max(b_res.rtts)

        if errors:
            b_res.error_summary = "; ".join(errors[:2])

        results.append(b_res)

    # Sort results: successful ones with lowest avg_rtt_ms first, then failures
    results.sort(key=lambda r: (r.avg_rtt_ms is None, r.avg_rtt_ms or 99999.0))

    if results and results[0].avg_rtt_ms is not None:
        results[0].is_fastest = True

    return results
