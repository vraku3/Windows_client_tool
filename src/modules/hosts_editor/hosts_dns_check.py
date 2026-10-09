"""Qt-free: what would DNS answer for each hosts override, if the line were gone?

The first thing an admin checks about a hosts entry is whether it still
means anything: a line pinning `api.example.com` to the address it had three
years ago is the classic "works on every machine but this one" ticket, and
nothing in the file itself can tell you. The answer is to ask the resolver
with the hosts file taken out of the picture.

How, measured on this machine (2026-10-09, unelevated, Windows 11 26300):

* `DnsQuery_W` with `DNS_QUERY_NO_HOSTS_FILE | DNS_QUERY_BYPASS_CACHE`
  (0x40 | 0x08) is the call behind `Resolve-DnsName -NoHostsFile`. It goes
  through the Windows DNS client itself, so it honours the same servers,
  NRPT rules and DoH configuration the rest of the machine uses -- unlike a
  raw UDP query to port 53, which would answer a different question. It
  needs no elevation and costs ~3-6 ms a name here (github.com A in 4.8 ms
  with the cache bypassed), against ~200 ms for a `Resolve-DnsName`
  process launch.
* Status codes seen: 0 with records; **9003** (DNS_ERROR_RCODE_NAME_ERROR)
  for a name that does not exist; **9501** (DNS_INFO_NO_RECORDS) when the
  name exists but has no record of that family -- github.com has no AAAA
  here, so an IPv6 pin for it is "no such record", not "differs"; **123**
  (ERROR_INVALID_NAME) for a single-label name such as `wpad` with no search
  suffix to append. Anything else is a failure to find out, reported as
  `unknown` with the code, never as "fine".
* Answers for CDN names change between two queries (github.com answered
  140.82.121.3 and then 140.82.121.4 a few minutes apart), so "differs" is
  worded as "DNS now answers X", not "this entry is wrong".
* CNAME records (type 5) precede the address records in the list
  (www.microsoft.com: CNAME, CNAME, A); only records of the asked family
  are compared.

Sink addresses (0.0.0.0, 127.x, ::, ::1) and `localhost` are skipped: a block
list entry is meant to differ from DNS, and asking about it is noise.
"""
from __future__ import annotations

import ctypes
import ipaddress
import logging
import socket
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Sequence, Tuple

from modules.hosts_editor import hosts_analysis as ha

logger = logging.getLogger(__name__)

DNS_TYPE_A, DNS_TYPE_AAAA = 1, 28
DNS_QUERY_BYPASS_CACHE = 0x08
DNS_QUERY_NO_HOSTS_FILE = 0x40
DNS_ERROR_RCODE_NAME_ERROR = 9003
DNS_INFO_NO_RECORDS = 9501
ERROR_INVALID_NAME = 123
_DNS_FREE_RECORD_LIST = 1
_DATA_OFFSET = 32   # DNS_RECORDW: pNext, pName, wType, wDataLength, Flags, dwTtl, dwReserved

AGREES, DIFFERS, NXDOMAIN, NO_RECORDS, UNKNOWN = "agrees", "differs", "nxdomain", "no_records", "unknown"

#: (status, answers, error) -- what `query` returns for one name/family.
QueryResult = Tuple[int, List[str], str]


@dataclass
class DnsCheck:
    host: str
    pinned: str
    status: str
    answers: List[str] = field(default_factory=list)
    detail: str = ""


def _api():
    api = ctypes.WinDLL("dnsapi")
    api.DnsQuery_W.argtypes = [wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    api.DnsQuery_W.restype = ctypes.c_long
    api.DnsRecordListFree.argtypes = [ctypes.c_void_p, ctypes.c_int]
    api.DnsRecordListFree.restype = None
    return api


def _walk(head: int, rtype: int) -> List[str]:
    family, size = (socket.AF_INET, 4) if rtype == DNS_TYPE_A else (socket.AF_INET6, 16)
    out, cur = [], head
    while cur:
        if ctypes.c_uint16.from_address(cur + 16).value == rtype:
            out.append(socket.inet_ntop(family, ctypes.string_at(cur + _DATA_OFFSET, size)))
        cur = ctypes.c_void_p.from_address(cur).value
    return out


def query_without_hosts(name: str, rtype: int) -> QueryResult:
    """Ask the Windows DNS client, skipping the hosts file and the cache."""
    try:
        api = _api()
    except OSError as exc:
        logger.warning("dnsapi unavailable: %s", exc)
        return -1, [], "dnsapi.dll could not be loaded: %s" % exc
    head = ctypes.c_void_p()
    rc = api.DnsQuery_W(name, rtype, DNS_QUERY_NO_HOSTS_FILE | DNS_QUERY_BYPASS_CACHE,
                        None, ctypes.byref(head), None)
    try:
        return rc, (_walk(head.value, rtype) if rc == 0 and head.value else []), ""
    finally:
        if head.value:
            api.DnsRecordListFree(head, _DNS_FREE_RECORD_LIST)


def is_sink(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.strip("[]").split("%")[0])
    except ValueError:
        return False
    return a.is_unspecified or a.is_loopback


def classify(host: str, pinned: str, result: QueryResult) -> DnsCheck:
    rc, answers, err = result
    if err:
        return DnsCheck(host, pinned, UNKNOWN, detail=err)
    if rc == 0:
        norm = {str(ipaddress.ip_address(a)) for a in answers}
        mine = str(ipaddress.ip_address(pinned.strip("[]").split("%")[0]))
        if not norm:
            return DnsCheck(host, pinned, NO_RECORDS, detail="DNS answered with no address of this family")
        return DnsCheck(host, pinned, AGREES if mine in norm else DIFFERS, sorted(norm))
    if rc == DNS_ERROR_RCODE_NAME_ERROR:
        return DnsCheck(host, pinned, NXDOMAIN, detail="DNS says this name does not exist")
    if rc == DNS_INFO_NO_RECORDS:
        return DnsCheck(host, pinned, NO_RECORDS, detail="The name exists but has no record of this family")
    if rc == ERROR_INVALID_NAME:
        return DnsCheck(host, pinned, NXDOMAIN,
                        detail="Single-label or invalid name: DNS cannot resolve it (code 123)")
    return DnsCheck(host, pinned, UNKNOWN, detail="DNS query failed (code %d)" % rc)


def checkable(lines: Sequence[ha.HostLine]) -> List[Tuple[str, str]]:
    """Unique (host, ip) pairs worth asking about, in file order."""
    seen, out = set(), []
    for ln in lines:
        if not (ln.is_entry and ln.enabled and ha.valid_ip(ln.ip)) or is_sink(ln.ip):
            continue
        for h in ln.hosts:
            key = (h.lower(), ln.ip.lower())
            if h.lower() != "localhost" and ha.valid_hostname(h) and key not in seen:
                seen.add(key)
                out.append((h, ln.ip))
    return out


def check_entries(lines: Sequence[ha.HostLine],
                  query: Callable[[str, int], QueryResult] = query_without_hosts,
                  is_cancelled: Callable[[], bool] = lambda: False) -> List[DnsCheck]:
    out = []
    for host, ip in checkable(lines):
        if is_cancelled():
            break
        rtype = DNS_TYPE_AAAA if ":" in ip else DNS_TYPE_A
        out.append(classify(host, ip, query(host, rtype)))
    return out


_FINDING = {
    DIFFERS: (ha.SEV_MEDIUM, "%d override(s) pin an address DNS no longer gives",
              "Without the hosts line DNS would answer differently. Deliberate for a "
              "test or intranet override; otherwise a stale pin (CDN names also rotate "
              "addresses, so check before deleting)."),
    AGREES: (ha.SEV_INFO, "%d override(s) only repeat what DNS already answers",
             "Redundant today, and it will go stale silently the day DNS moves."),
    NXDOMAIN: (ha.SEV_INFO, "%d name(s) exist only in this hosts file",
               "DNS does not know them; the hosts line is the only thing resolving them."),
    NO_RECORDS: (ha.SEV_INFO, "%d name(s) have no DNS record of the pinned family",
                 "e.g. an IPv6 pin for a name with no AAAA record."),
    UNKNOWN: (ha.SEV_LOW, "%d name(s) could not be checked against DNS",
              "The query failed; this is not evidence the entry is fine."),
}


def _line_indexes(lines: Sequence[ha.HostLine]) -> Dict[Tuple[str, str], List[int]]:
    idx: Dict[Tuple[str, str], List[int]] = {}
    for ln in lines:
        if ln.is_entry and ln.enabled:
            for h in ln.hosts:
                idx.setdefault((h.lower(), ln.ip.lower()), []).append(ln.index)
    return idx


def dns_findings(checks: Sequence[DnsCheck], lines: Sequence[ha.HostLine]) -> List[ha.Finding]:
    """One grouped finding per outcome, pointing at the CURRENT lines.

    A check whose (host, ip) no longer appears among the enabled lines (the
    user edited or removed it since) is dropped rather than pointed at the
    wrong row."""
    idx = _line_indexes(lines)
    groups: Dict[str, List[DnsCheck]] = {}
    for c in checks:
        if (c.host.lower(), c.pinned.lower()) in idx:
            groups.setdefault(c.status, []).append(c)
    out = []
    for status in (DIFFERS, UNKNOWN, NXDOMAIN, NO_RECORDS, AGREES):
        items = groups.get(status)
        if not items:
            continue
        sev, title, why = _FINDING[status]
        rows = sorted({i for c in items for i in idx[(c.host.lower(), c.pinned.lower())]})
        out.append(ha.Finding(sev, "dns_" + status, title % len(items),
                              why + "\n" + "\n".join(_describe(c) for c in items[:15]), rows))
    return out


def _describe(c: DnsCheck) -> str:
    if c.status in (DIFFERS, AGREES):
        return "%s: pinned %s, DNS answers %s" % (c.host, c.pinned, ", ".join(c.answers))
    return "%s (%s): %s" % (c.host, c.pinned, c.detail)


def summary(checks: Sequence[DnsCheck]) -> str:
    if not checks:
        return "No overrides to check (block-list and localhost lines are skipped)."
    counts: Dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    order = (DIFFERS, AGREES, NXDOMAIN, NO_RECORDS, UNKNOWN)
    return "Checked %d override(s) against DNS: %s." % (
        len(checks), ", ".join("%d %s" % (counts[s], s.replace("_", " ")) for s in order if s in counts))

