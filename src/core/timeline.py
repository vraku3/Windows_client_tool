"""Best-effort cross-module maintenance history, independent of Qt."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_COUNTS = {}
_MAX_ENTRIES = 5000


@contextmanager
def _locked(path):
    with _LOCK:
        with open(str(path) + ".lock", "a+b") as stream:
            if os.name == "nt":
                import msvcrt
                if not stream.tell():
                    stream.write(b"0")
                    stream.flush()
                for attempt in range(20):
                    try:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if attempt == 19:
                            raise
                        time.sleep(0.01)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def _parse_time(value):
    if isinstance(value, datetime):
        return value.timestamp()
    return datetime.fromisoformat(value).timestamp()


def _read(path):
    events = []
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                    if (not isinstance(item, dict)
                            or not all(isinstance(item.get(k), str) for k in ("ts", "source", "event", "summary"))
                            or not isinstance(item.get("detail"), dict)
                            or item.get("severity") not in ("info", "warning", "error")):
                        continue
                    _parse_time(item["ts"])
                    events.append(item)
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
    except FileNotFoundError:
        return []
    return events


def record(app_data_dir, source, event, summary, detail=None, severity="info"):
    try:
        _append(app_data_dir, source, event, summary, detail, severity)
    except Exception:
        logger.warning("Could not append timeline event", exc_info=True)


def _append(app_data_dir, source, event, summary, detail, severity, ts=None):
    path = Path(app_data_dir) / "timeline.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(detail or {}, ensure_ascii=False, default=str)
    detail = json.loads(payload) if len(payload.encode("utf-8")) <= 4096 else {"truncated": True}
    if not isinstance(detail, dict):
        detail = {}
    item = dict(ts=ts or datetime.now().astimezone().isoformat(timespec="microseconds"),
                source=str(source)[:64], event=str(event)[:64], summary=str(summary)[:1000],
                detail=detail, severity=severity if severity in ("info", "warning", "error") else "info")
    line = json.dumps(item, ensure_ascii=False) + "\n"
    with _locked(path):
        size = path.stat().st_size if path.exists() else 0
        cached = _COUNTS.get(str(path))
        count = cached[1] if cached and cached[0] == size else None
        prefix = ""
        if size:
            with open(path, "rb") as stream:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    prefix = "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(prefix + line)
            stream.flush()
        if count is None:
            with open(path, "rb") as stream:
                count = sum(1 for _ in stream)
        else:
            count += 1
        if count > _MAX_ENTRIES + 500:
            _trim(path, _MAX_ENTRIES)
            count = _MAX_ENTRIES
        _COUNTS[str(path)] = (path.stat().st_size, count)


def _trim(path, max_entries=5000):
    path = Path(path)
    temporary = None
    try:
        with open(path, "rb") as stream:
            lines = stream.readlines()
        if len(lines) <= max_entries + 500:
            return
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.writelines(lines[-max_entries:])
            stream.flush()
        os.replace(temporary, path)
    except Exception:
        logger.warning("Could not trim timeline", exc_info=True)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_timeline(app_data_dir, limit=200, source=None, since=None) -> list[dict]:
    try:
        if limit <= 0:
            return []
        cutoff = _parse_time(since) if since is not None else None
        events = [e for e in _read(Path(app_data_dir) / "timeline.jsonl")
                  if (source is None or e["source"] == source)
                  and (cutoff is None or _parse_time(e["ts"]) >= cutoff)]
        return sorted(reversed(events), key=lambda e: _parse_time(e["ts"]), reverse=True)[:limit]
    except Exception:
        logger.warning("Could not read timeline", exc_info=True)
        return []


def search_timeline(app_data_dir, query, limit=50) -> list[dict]:
    if limit <= 0:
        return []
    return [e for e in load_timeline(app_data_dir, limit=_MAX_ENTRIES + 500)
            if query.casefold() in e["summary"].casefold()][:limit]


def detect_reboot(app_data_dir, boot_time=None):
    try:
        if boot_time is None:
            import psutil
            boot_time = psutil.boot_time()
        events = load_timeline(app_data_dir, limit=_MAX_ENTRIES + 500, source="system")
        stop = next((e for e in events if e["event"] == "app_stop"), None)
        if stop is None or not _parse_time(stop["ts"]) < boot_time <= time.time():
            return
        if any(e["event"] == "reboot_detected" and _parse_time(e["ts"]) >= boot_time for e in events):
            return
        _append(app_data_dir, "system", "reboot_detected", "Windows reboot detected", {}, "info",
                datetime.fromtimestamp(boot_time).astimezone().isoformat(timespec="microseconds"))
    except Exception:
        logger.warning("Could not detect reboot for timeline", exc_info=True)
