import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
_lock = threading.RLock()
MAX_ENTRIES = 2000


@contextmanager
def _file_lock(path):
    import msvcrt

    with _lock, open(str(path) + ".lock", "a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def record_activity(app_data_dir, entry_dict):
    from core.admin_utils import is_admin

    path = Path(app_data_dir) / "updates" / "activity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now().isoformat(timespec="seconds"),
             "source": "winget", "version_before": None, "version_offered": None,
             "version_after": None, "detail": "", "context": "manual",
             "elevated": is_admin(), **entry_dict}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _file_lock(path):
        if path.exists() and path.stat().st_size:
            with path.open("rb") as previous:
                previous.seek(-1, os.SEEK_END)
                if previous.read(1) != b"\n":
                    line = "\n" + line
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
        _trim_if_needed(path)


def _trim_if_needed(path, max_entries=MAX_ENTRIES):
    path = Path(path)
    with path.open(encoding="utf-8", errors="replace") as stream:
        lines = stream.readlines()
    if len(lines) <= max_entries + 100:
        return
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.writelines(lines[-max_entries:])
            stream.flush()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_activity(app_data_dir, package_id=None, limit=100) -> list[dict]:
    if limit is not None and limit <= 0:
        return []
    path = Path(app_data_dir) / "updates" / "activity.jsonl"
    try:
        with _file_lock(path), path.open(encoding="utf-8", errors="replace") as stream:
            lines = stream.readlines()
    except FileNotFoundError:
        return []
    entries = []
    corrupt = 0
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            corrupt += 1
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            corrupt += 1
            continue
        if package_id is not None and entry["id"].casefold() != package_id.casefold():
            continue
        entries.append(entry)
        if limit is not None and len(entries) >= limit:
            break
    if corrupt:
        logger.warning("Skipped %s corrupt activity ledger records", corrupt)
    return entries


def package_history(app_data_dir, package_id) -> list[dict]:
    return load_activity(app_data_dir, package_id, limit=None)


def recent_activity(app_data_dir, days=30) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    entries = []
    for entry in load_activity(app_data_dir, limit=None):
        try:
            timestamp = datetime.fromisoformat(entry["ts"]).astimezone()
        except (ValueError, TypeError, KeyError, OverflowError):
            logger.warning("Invalid timestamp in activity ledger: %r", entry.get("ts"))
            continue
        if timestamp >= cutoff:
            entries.append(entry)
    return entries


def _version(value):
    value = (value or "").strip()
    return value if value and value.casefold() not in ("unknown", "n/a", "—") and not value.startswith(("<", ">")) else None


def _save(app, entry, log):
    try:
        record_activity(app.app_data_dir, entry)
    except (OSError, AttributeError) as exc:
        logger.warning("Could not save package activity", exc_info=True)
        log(f"Warning: could not save package activity: {exc}")


def record_winget_activity(app, update, outcome, context, log):
    states = {"UPDATED?": "UPDATED_UNVERIFIED", "UNCONFIRMED": "UPDATED_UNVERIFIED",
              "STILL RUNNING": "UPDATED_UNVERIFIED", "BROKEN": "FAILED",
              "NOT APPLICABLE": "SKIPPED", "NEEDS USER": "NEEDS_USER",
              "NEEDS INPUT": "NEEDS_USER", "DIFF TECH": "DIFF_TECH"}
    _save(app, {"source": "store" if (update.source or "").strip().lower() == "msstore" else "winget",
                "id": update.winget_id, "name": update.name,
                "version_before": _version(update.installed_version),
                "version_offered": _version(update.available_version),
                "version_after": _version(getattr(outcome, "version_after", None)),
                "outcome": states.get(outcome.state, outcome.state), "detail": outcome.detail,
                "context": context}, log)


def record_wu_activity(app, result, context, log):
    state = "UPDATED" if result.success else "UPDATED_UNVERIFIED" if result.pending_restart else "FAILED"
    _save(app, {"source": "wu", "id": result.kb or result.title, "name": result.title,
                "outcome": state, "detail": result.message, "context": context}, log)


def record_wu_uninstall_activity(app, result, context, log):
    _save(app, {"source": "wu", "id": result.kb or result.title, "name": result.title,
                "outcome": "UNINSTALLED" if result.success else "FAILED",
                "detail": result.message, "context": context}, log)
