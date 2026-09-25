"""CPU topology: which logical processors share a core, which cores are
performance vs efficiency, and which NUMA node each belongs to. No Qt.

Read from `GetLogicalProcessorInformationEx`, which is a chain of variable-
length records; every offset below is from winnt.h and each record is bounds-
checked, because a mis-sized step on this chain reads garbage rather than
failing. A machine with one kind of core and one NUMA node (most desktops,
including a Ryzen 9950X3D) is a normal answer, and is reported as such.
"""
import ctypes
import logging
import struct
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

RELATION_PROCESSOR_CORE = 0
RELATION_NUMA_NODE = 1
RELATION_ALL = 0xFFFF
_GROUP_AFFINITY = struct.Struct("<QH3H")      # Mask, Group, Reserved[3]


@dataclass
class Core:
    index: int
    threads: List[int]
    efficiency_class: int      # higher = more performant
    numa_node: int = 0


@dataclass
class Topology:
    cores: List[Core] = field(default_factory=list)
    numa_nodes: Dict[int, List[int]] = field(default_factory=dict)

    @property
    def classes(self) -> List[int]:
        return sorted({c.efficiency_class for c in self.cores})

    @property
    def is_hybrid(self) -> bool:
        return len(self.classes) > 1

    def kind_of(self, logical: int) -> str:
        """'P', 'E' or '' (when every core is the same, nothing to distinguish)."""
        if not self.is_hybrid:
            return ""
        for core in self.cores:
            if logical in core.threads:
                return "P" if core.efficiency_class == self.classes[-1] else "E"
        return ""

    def summary(self) -> str:
        n_threads = sum(len(c.threads) for c in self.cores)
        text = f"{len(self.cores)} cores, {n_threads} logical processors"
        if self.is_hybrid:
            perf = sum(1 for c in self.cores if c.efficiency_class == self.classes[-1])
            text += f"   ({perf} performance + {len(self.cores) - perf} efficiency)"
        else:
            text += "   (all cores identical)"
        nodes = len(self.numa_nodes)
        text += f"   ·   {nodes} NUMA node" + ("s" if nodes != 1 else "")
        return text


def _threads_from_masks(buffer: bytes, offset: int, count: int) -> List[int]:
    threads = []
    for i in range(count):
        at = offset + i * _GROUP_AFFINITY.size
        if at + _GROUP_AFFINITY.size > len(buffer):
            break
        mask, group, *_ = _GROUP_AFFINITY.unpack_from(buffer, at)
        threads += [group * 64 + bit for bit in range(64) if mask >> bit & 1]
    return threads


def parse(buffer: bytes) -> Topology:
    """Walk the record chain. Split from the API call so it tests on bytes."""
    topo = Topology()
    pos = 0
    while pos + 8 <= len(buffer):
        relationship, size = struct.unpack_from("<II", buffer, pos)
        if size < 8 or pos + size > len(buffer):
            logger.warning("topology record at %d has size %d; stopping", pos, size)
            break
        body = pos + 8
        if relationship == RELATION_PROCESSOR_CORE and body + 24 <= len(buffer):
            efficiency = buffer[body + 1]
            (group_count,) = struct.unpack_from("<H", buffer, body + 22)
            topo.cores.append(Core(index=len(topo.cores),
                                   threads=_threads_from_masks(buffer, body + 24, group_count),
                                   efficiency_class=efficiency))
        elif relationship == RELATION_NUMA_NODE and body + 24 <= len(buffer):
            (node,) = struct.unpack_from("<I", buffer, body)
            (group_count,) = struct.unpack_from("<H", buffer, body + 22)
            topo.numa_nodes[node] = _threads_from_masks(buffer, body + 24, max(1, group_count))
        pos += size
    for node, threads in topo.numa_nodes.items():
        for core in topo.cores:
            if core.threads and core.threads[0] in threads:
                core.numa_node = node
    return topo


def read_topology() -> Optional[Topology]:
    """The machine's topology, or None if Windows would not say."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetLogicalProcessorInformationEx.argtypes = [
            wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetLogicalProcessorInformationEx.restype = wintypes.BOOL
        size = wintypes.DWORD(0)
        kernel32.GetLogicalProcessorInformationEx(RELATION_ALL, None, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not kernel32.GetLogicalProcessorInformationEx(RELATION_ALL, buf, ctypes.byref(size)):
            logger.warning("GetLogicalProcessorInformationEx failed: %s", ctypes.get_last_error())
            return None
        return parse(buf.raw[:size.value])
    except (OSError, AttributeError) as e:
        logger.warning("topology unavailable: %s", e)
        return None
