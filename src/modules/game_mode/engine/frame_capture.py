"""Optional PresentMon capture and frame-time summary (Qt-free)."""
from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def find_presentmon(extra_paths=()) -> Optional[str]:
    """A usable PresentMon executable, or None (never installed by REA)."""
    env = os.environ.get("REA_PRESENTMON") or os.environ.get("PRESENTMON")
    if env and Path(env).is_file():
        return env
    found = shutil.which("PresentMon.exe") or shutil.which("presentmon.exe")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PresentMon",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "PresentMon",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet",
    ]
    for extra in extra_paths:
        candidates.append(Path(extra))
    for root in candidates:
        try:
            if root.is_file() and root.suffix.lower() == ".exe":
                return str(root)
            if root.is_dir():
                for path in root.rglob("PresentMon*.exe"):
                    return str(path)
        except OSError as exc:
            logger.debug("Accessing candidate root %s: %s", root, exc)
            continue
    return None


def capture(presentmon: str, process_name: str, seconds: int,
            output_path: Optional[str] = None,
            runner: Callable = subprocess.run,
            timeout_s: Optional[int] = None) -> str:
    """Run PresentMon for `seconds`; returns the CSV path."""
    output_path = output_path or str(Path(tempfile.gettempdir()) / "wct-presentmon.csv")
    cmd = [presentmon, "--process_name", process_name,
           "--output_file", output_path,
           "--terminate_after_timed", str(max(1, int(seconds)))]
    result = runner(cmd, capture_output=True, text=True,
                    timeout=timeout_s or max(30, int(seconds) + 20),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if getattr(result, "returncode", 0) != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        raise RuntimeError(detail[:500] or f"PresentMon exited {result.returncode}")
    if not Path(output_path).is_file():
        raise RuntimeError("PresentMon did not write its CSV output.")
    return output_path


def _header_index(header, names) -> Optional[int]:
    lowered = [str(cell).strip().casefold() for cell in header]
    for name in names:
        if name.casefold() in lowered:
            return lowered.index(name.casefold())
    return None


def summarize(text: str) -> dict:
    """Avg FPS and 1%/0.1% lows from PresentMon's MsBetweenPresents column."""
    reader = csv.reader(io.StringIO(text or ""))
    rows = list(reader)
    if not rows:
        raise ValueError("PresentMon output is empty.")
    header = rows[0]
    index = _header_index(header, ("MsBetweenPresents", "msBetweenPresents",
                                   "msBetweenPresents", "msBetweenDisplayChange"))
    if index is None:
        raise ValueError("PresentMon output has no frame-time column.")
    frame_ms = []
    for row in rows[1:]:
        if index >= len(row):
            continue
        try:
            value = float(row[index])
        except (TypeError, ValueError) as exc:
            logger.debug("Parsing frame value: %s", exc)
            continue
        if 0 < value < 10_000:
            frame_ms.append(value)
    if not frame_ms:
        raise ValueError("PresentMon reported no frames.")

    def low(percent: float) -> float:
        count = max(1, int(len(frame_ms) * percent))
        slowest = sorted(frame_ms, reverse=True)[:count]
        return 1000.0 / (sum(slowest) / len(slowest))

    average = 1000.0 / (sum(frame_ms) / len(frame_ms))
    return {"frames": len(frame_ms), "avg_fps": round(average, 1),
            "one_percent_low_fps": round(low(0.01), 1),
            "point_one_percent_low_fps": round(low(0.001), 1),
            "max_frame_ms": round(max(frame_ms), 2)}
