from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class LogEntry:
    timestamp: datetime
    source: str
    level: str       # Error, Warning, Info, Debug
    message: str
    raw: dict = field(default_factory=dict)


@dataclass
class LogReadResult:
    """Records and read limits travel together, including an unavailable empty read."""
    entries: List[LogEntry] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


@dataclass
class ProcessInfo:
    pid: int
    name: str
    cpu_percent: float
    memory_bytes: int


@dataclass
class Recommendation:
    id: str
    summary: str
    details: str
    confidence: float
    source_entries: List[LogEntry] = field(default_factory=list)
