"""Windows Wait Chain Traversal (WCT) & Deadlock Detective.

Uses native advapi32.dll OpenThreadWaitChainSession and GetThreadWaitChain to:
1. Traverse synchronization primitives (Mutex, CriticalSection, ALPC, COM, Thread, Process).
2. Trace what process/thread a hanging or blocked thread is waiting on.
3. Detect synchronization deadlocks.

Pure Python (ctypes), zero external dependencies, SAC-safe.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

advapi32 = ctypes.windll.advapi32

WCT_MAX_NODE_COUNT = 16
WCT_ASYNC_OPEN_FLAG = 0
WCT_FLAGS = 0


class _LOCK_OBJECT(ctypes.Structure):
    _fields_ = [
        ("ObjectName", wintypes.WCHAR * 128),
        ("Timeout", wintypes.LARGE_INTEGER),
        ("Alertable", wintypes.BOOL),
    ]


class _THREAD_OBJECT(ctypes.Structure):
    _fields_ = [
        ("ProcessId", wintypes.DWORD),
        ("ThreadId", wintypes.DWORD),
        ("WaitTime", wintypes.DWORD),
        ("ContextSwitches", wintypes.DWORD),
    ]


class _UNION(ctypes.Union):
    _fields_ = [
        ("LockObject", _LOCK_OBJECT),
        ("ThreadObject", _THREAD_OBJECT),
    ]


class WAITCHAIN_NODE_INFO(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_ = [
        ("ObjectType", wintypes.DWORD),
        ("ObjectStatus", wintypes.DWORD),
        ("_u", _UNION),
    ]


# Function prototypes
advapi32.OpenThreadWaitChainSession.restype = ctypes.c_void_p
advapi32.OpenThreadWaitChainSession.argtypes = [wintypes.DWORD, ctypes.c_void_p]

advapi32.CloseThreadWaitChainSession.restype = None
advapi32.CloseThreadWaitChainSession.argtypes = [ctypes.c_void_p]

advapi32.GetThreadWaitChain.restype = wintypes.BOOL
advapi32.GetThreadWaitChain.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(WAITCHAIN_NODE_INFO),
    ctypes.POINTER(wintypes.BOOL),
]

OBJECT_TYPES = {
    1: "CriticalSection",
    2: "SendMessage",
    3: "Mutex",
    4: "ALPC",
    5: "COM",
    6: "ThreadWait",
    7: "ProcessWait",
    8: "Thread",
    9: "ComActivation",
    10: "Unknown",
}

OBJECT_STATUSES = {
    1: "NoStatus",
    2: "Ready",
    3: "Waiting",
    4: "BlockedOnChainEntry",
}


@dataclass
class WaitChainNode:
    type_name: str
    status_name: str
    process_id: Optional[int] = None
    thread_id: Optional[int] = None
    wait_time_ms: int = 0
    lock_object_name: str = ""


@dataclass
class ThreadWaitReport:
    thread_id: int
    is_deadlocked: bool = False
    chain_length: int = 0
    nodes: List[WaitChainNode] = field(default_factory=list)
    summary: str = "Running / Not Blocked"


def inspect_thread_wait_chain(thread_id: int) -> ThreadWaitReport:
    """Query wait chain traversal for a specific thread ID."""
    session = advapi32.OpenThreadWaitChainSession(WCT_ASYNC_OPEN_FLAG, None)
    if not session:
        return ThreadWaitReport(thread_id=thread_id, summary="Could not open WCT session")

    try:
        nodes_arr = (WAITCHAIN_NODE_INFO * WCT_MAX_NODE_COUNT)()
        node_count = wintypes.DWORD(WCT_MAX_NODE_COUNT)
        is_cycle = wintypes.BOOL(False)

        success = advapi32.GetThreadWaitChain(
            session,
            None,
            WCT_FLAGS,
            wintypes.DWORD(thread_id),
            ctypes.byref(node_count),
            nodes_arr,
            ctypes.byref(is_cycle),
        )

        if not success:
            return ThreadWaitReport(thread_id=thread_id, summary="Thread not accessible or exited")

        count = node_count.value
        deadlocked = bool(is_cycle.value)
        parsed_nodes: List[WaitChainNode] = []

        for i in range(count):
            node = nodes_arr[i]
            t_name = OBJECT_TYPES.get(node.ObjectType, f"Type_{node.ObjectType}")
            s_name = OBJECT_STATUSES.get(node.ObjectStatus, f"Status_{node.ObjectStatus}")

            if node.ObjectType in (1, 3):  # Lock
                parsed_nodes.append(WaitChainNode(
                    type_name=t_name,
                    status_name=s_name,
                    lock_object_name=node.LockObject.ObjectName,
                ))
            else:  # Thread
                parsed_nodes.append(WaitChainNode(
                    type_name=t_name,
                    status_name=s_name,
                    process_id=node.ThreadObject.ProcessId,
                    thread_id=node.ThreadObject.ThreadId,
                    wait_time_ms=node.ThreadObject.WaitTime,
                ))

        if deadlocked:
            summary = f"DEADLOCK DETECTED! Thread {thread_id} is in a cyclical wait chain ({count} nodes)."
        elif count > 1:
            summary = f"Thread {thread_id} is waiting in a {count}-node synchronization chain."
        else:
            summary = "Thread is active / ready."

        return ThreadWaitReport(
            thread_id=thread_id,
            is_deadlocked=deadlocked,
            chain_length=count,
            nodes=parsed_nodes,
            summary=summary,
        )
    finally:
        advapi32.CloseThreadWaitChainSession(session)
