"""A tiny Qt-free DNS client over UDP, so a resolver can be timed and compared.

Why not ``nslookup``/``Resolve-DnsName``: both cost a process launch (~150 ms+)
that would be measured as "DNS latency", and nslookup's output is not stable to
parse.  A raw query measures the resolver.

A failure is never an answer: ``query`` returns a ``DnsReply`` whose ``error``
says why nothing came back (timeout, refused, network unreachable), which is
distinct from an empty, successful reply (NXDOMAIN / NODATA).
"""
from __future__ import annotations

import logging
import random
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

QTYPES = {"A": 1, "NS": 2, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28}
_QTYPE_NAMES = {v: k for k, v in QTYPES.items()}
RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}


@dataclass
class DnsReply:
    server: str
    name: str
    qtype: str
    rtt_ms: Optional[float] = None
    rcode: Optional[str] = None
    answers: List[str] = field(default_factory=list)
    cnames: List[str] = field(default_factory=list)
    truncated_udp: bool = False  # answered over TCP because UDP was cut
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """The resolver answered (any rcode)."""
        return self.error is None

    @property
    def resolved(self) -> bool:
        return self.error is None and self.rcode == "NOERROR" and bool(self.answers)


def build_query(name: str, qtype: str, txid: int) -> bytes:
    header = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    qname = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("idna")
        if not raw or len(raw) > 63:
            raise ValueError(f"bad DNS label in {name!r}")
        qname += bytes([len(raw)]) + raw
    return header + qname + b"\x00" + struct.pack(">HH", QTYPES[qtype], 1)


def _read_name(data: bytes, pos: int) -> tuple:
    labels: List[str] = []
    jumped = False
    end = pos
    hops = 0
    while True:
        if pos >= len(data):
            raise ValueError("name runs past packet")
        ln = data[pos]
        if ln & 0xC0 == 0xC0:
            ptr = ((ln & 0x3F) << 8) | data[pos + 1]
            if not jumped:
                end = pos + 2
            pos = ptr
            jumped = True
            hops += 1
            if hops > 30:
                raise ValueError("compression loop")
            continue
        pos += 1
        if ln == 0:
            break
        labels.append(data[pos:pos + ln].decode("ascii", "replace"))
        pos += ln
    if not jumped:
        end = pos
    return ".".join(labels), end


def _format_rdata(rtype: int, data: bytes, pos: int, length: int) -> str:
    if rtype == 1 and length == 4:
        return socket.inet_ntoa(data[pos:pos + 4])
    if rtype == 28 and length == 16:
        return socket.inet_ntop(socket.AF_INET6, data[pos:pos + 16])
    if rtype in (2, 5):
        return _read_name(data, pos)[0]
    if rtype == 15:
        pref = struct.unpack(">H", data[pos:pos + 2])[0]
        return f"{pref} {_read_name(data, pos + 2)[0]}"
    if rtype == 16:
        out, i = [], pos
        while i < pos + length:
            n = data[i]
            out.append(data[i + 1:i + 1 + n].decode("utf-8", "replace"))
            i += 1 + n
        return "".join(out)
    return data[pos:pos + length].hex()


def parse_reply(data: bytes) -> tuple:
    """Return (txid, rcode, [(rtype, text)], truncated). Raises ValueError if malformed."""
    if len(data) < 12:
        raise ValueError("short packet")
    txid, flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    rcode = RCODES.get(flags & 0xF, f"RCODE{flags & 0xF}")
    pos = 12
    for _ in range(qd):
        _n, pos = _read_name(data, pos)
        pos += 4
    answers: List[tuple] = []
    for _ in range(an):
        _n, pos = _read_name(data, pos)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        answers.append((rtype, _format_rdata(rtype, data, pos, rdlen)))
        pos += rdlen
    return txid, rcode, answers, bool(flags & 0x0200)


def _udp_exchange(server: str, packet: bytes, timeout: float) -> bytes:
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        while True:
            data, _addr = sock.recvfrom(4096)
            if data[:2] == packet[:2]:
                return data
    finally:
        sock.close()


def _tcp_exchange(server: str, packet: bytes, timeout: float) -> bytes:
    with socket.create_connection((server, 53), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(struct.pack(">H", len(packet)) + packet)
        head = b""
        while len(head) < 2:
            chunk = sock.recv(2 - len(head))
            if not chunk:
                raise OSError("connection closed")
            head += chunk
        need = struct.unpack(">H", head)[0]
        body = b""
        while len(body) < need:
            chunk = sock.recv(need - len(body))
            if not chunk:
                raise OSError("connection closed mid-reply")
            body += chunk
        return body


def query(server: str, name: str, qtype: str = "A", timeout: float = 2.0) -> DnsReply:
    reply = DnsReply(server=server, name=name, qtype=qtype)
    txid = random.randint(0, 0xFFFF)
    try:
        packet = build_query(name, qtype, txid)
    except (ValueError, UnicodeError) as e:
        reply.error = str(e)
        return reply
    try:
        start = time.perf_counter()
        data = _udp_exchange(server, packet, timeout)
        rtt = (time.perf_counter() - start) * 1000.0
        parsed = parse_reply(data)
        if parsed[3]:
            data = _tcp_exchange(server, packet, timeout)
            parsed = parse_reply(data)
            reply.truncated_udp = True
            rtt = (time.perf_counter() - start) * 1000.0
        _rid, rcode, records, _tc = parsed
        reply.rtt_ms, reply.rcode = rtt, rcode
        want = QTYPES[qtype]
        reply.answers = [t for r, t in records if r == want]
        reply.cnames = [t for r, t in records if r == 5 and want != 5]
        if rcode == "REFUSED":
            reply.error = "server refused the query"
    except socket.timeout:
        reply.error = f"no answer within {timeout:g}s"
    except (ValueError, struct.error) as e:
        reply.error = f"malformed reply: {e}"
    except OSError as e:
        reply.error = f"{type(e).__name__}: {e}"
        logger.debug("dns query %s @%s failed: %s", name, server, e)
    return reply


def compare(replies: List[DnsReply]) -> str:
    """One-line verdict on whether resolvers agree (answer SETS, not order)."""
    live = [r for r in replies if r.ok]
    if len(live) < len(replies):
        bad = [r.server for r in replies if not r.ok]
        prefix = f"{len(bad)} resolver(s) gave no usable answer ({', '.join(bad)}). "
    else:
        prefix = ""
    if not live:
        return prefix.strip() or "No resolvers queried."
    sets = {frozenset(r.answers) for r in live}
    codes = {r.rcode for r in live}
    if len(sets) == 1 and len(codes) == 1:
        return prefix + "All answering resolvers agree."
    if len(codes) > 1:
        return prefix + "Resolvers disagree on the result code: " + ", ".join(
            f"{r.server}={r.rcode}" for r in live)
    return prefix + "Resolvers return different answer sets (can be normal for CDN/geo DNS)."


def qtype_name(code: int) -> str:
    return _QTYPE_NAMES.get(code, str(code))
