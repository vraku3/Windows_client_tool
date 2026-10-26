import logging
import os
from abc import ABC, abstractmethod
from typing import List, Callable, Optional

from core.types import LogEntry

logger = logging.getLogger(__name__)


class LogParserBase(ABC):
    """Abstract base for parsing text-based log files (CBS, DISM, WU)."""

    def __init__(self, file_path: str):
        self._file_path = file_path

    @property
    def file_path(self) -> str:
        return self._file_path

    def file_exists(self) -> bool:
        return os.path.isfile(self._file_path)

    def file_size(self) -> int:
        try:
            return os.path.getsize(self._file_path)
        except OSError:
            return 0

    def _detect_encoding(self) -> str:
        """The file's encoding, honouring either BOM.

        "utf-8-sig", not "utf-8". Every log under C:\\Windows\\Logs is UTF-8
        WITH a BOM, and plain "utf-8" leaves it in the text as a leading
        U+FEFF -- invisible, not matched by ``\\s``, and enough to make the
        first line fail every timestamp regex in this codebase. The first
        line of every CBS, DISM and Windows Update log was being dropped for
        that reason alone. "utf-8-sig" also reads a file without a BOM
        correctly, so it is safe as the default.
        """
        try:
            with open(self._file_path, "rb") as f:
                bom = f.read(2)
            if bom in (b"\xff\xfe", b"\xfe\xff"):
                return "utf-16"
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
        return "utf-8-sig"

    def parse(self, progress_callback: Optional[Callable[[int], None]] = None) -> List[LogEntry]:
        """Parse the log file and return entries. Runs on worker thread."""
        self.read_problems = []
        if not self.file_exists():
            logger.warning("Log file not found: %s", self._file_path)
            self.read_problems.append(f"Log file not found: {self._file_path}")
            return []

        encoding = self._detect_encoding()
        entries = []
        try:
            with open(self._file_path, "r", encoding=encoding, errors="replace") as f:
                lines = f.readlines()

            total = len(lines)
            for i, line in enumerate(lines):
                entry = self.parse_line(line.rstrip("\n\r"))
                if entry is not None:
                    entries.append(entry)
                if progress_callback and total > 0 and i % 1000 == 0:
                    progress_callback(int((i / total) * 100))

            if progress_callback:
                progress_callback(100)

        except OSError as e:
            logger.error("Failed to read %s: %s", self._file_path, e)
            self.read_problems.append(f"Could not read {self._file_path}: {e}")

        return entries

    def parse_result(self, progress_callback=None):
        """Preserve read failure separately from a successfully parsed empty file."""
        from core.types import LogReadResult
        entries = self.parse(progress_callback=progress_callback)
        return LogReadResult(entries, self.read_problems)

    @abstractmethod
    def parse_line(self, line: str) -> Optional[LogEntry]:
        """Parse a single line. Return LogEntry or None if line should be skipped."""
        ...
