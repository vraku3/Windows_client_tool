"""The Game Mode journal: what was changed and what it was before. Qt-free.

Written BEFORE each change (the entry with `done: False`) and again after it
(`done: True`), so a crash, a closed app or a reboot in the middle still
leaves the way back on disk. Turning Game Mode off reads only this file --
never the app's memory -- and deletes it once everything is put back; the
report of that last restore is kept beside it as `last_restore.json`.
"""
from __future__ import annotations

import json
import logging
import os
import time
import tempfile
from typing import List, Optional

from .session_history import record_session_end

logger = logging.getLogger(__name__)

VERSION = 1


def default_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WindowsTweaker", "game_mode")


class Journal:
    def __init__(self, folder: Optional[str] = None) -> None:
        self.folder = folder or default_dir()
        self.path = os.path.join(self.folder, "session.json")
        self.data: dict = {}

    # ---- lifecycle -----------------------------------------------------------------------------
    def load(self) -> bool:
        """True when a session is on disk (Game Mode is ON)."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
                raise ValueError("journal must contain an actions list")
        except FileNotFoundError:
            self.data = {}
            return False
        except (OSError, ValueError) as error:
            # A journal we cannot read is still a session that is ON: say so
            # rather than pretend nothing was changed.
            logger.error("game mode: journal %s unreadable: %s", self.path, error)
            self.data = {"version": VERSION, "started": 0, "actions": [], "corrupt": str(error)}
            return True
        self.data = data
        return True

    def begin(self, **facts) -> None:
        self.data = {"version": VERSION, "started": time.time(), "actions": [], **facts}
        self.save()

    def record(self, entry: dict) -> dict:
        entry = dict(entry, done=False)
        self.data.setdefault("actions", []).append(entry)
        self.save()
        return entry

    def mark_done(self, entry: dict) -> None:
        entry["done"] = True
        self.save()

    def finish(self, report: List[dict], *, restored: int = 0) -> Optional[str]:
        """Everything is back: drop the session, keep the report."""
        os.makedirs(self.folder, exist_ok=True)
        last = {"finished": time.time(), "started": self.data.get("started"), "report": report}
        self._write(os.path.join(self.folder, "last_restore.json"), last)
        history_error = None
        try:
            record_session_end(None, report, self.data.get("handles_after"),
                               self.data.get("processes_after"), started=self.data.get("started"),
                               restored=restored, folder=self.folder)
        except (OSError, ValueError, TypeError) as error:
            logger.warning("game mode: could not save restore history: %s", error)
            history_error = f"Restore history could not be saved: {error}"
        try:
            os.remove(self.path)
        except FileNotFoundError:
            logger.info("game mode: journal was already gone")
        self.data = {}
        return history_error

    # ---- access --------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self.data)

    @property
    def actions(self) -> List[dict]:
        return list(self.data.get("actions", []))

    def save(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        self._write(self.path, self.data)

    @staticmethod
    def _write(path: str, payload: dict) -> None:
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                             prefix=os.path.basename(path) + ".", suffix=".tmp",
                                             delete=False) as fh:
                tmp = fh.name
                json.dump(payload, fh, indent=1)
            os.replace(tmp, path)
        finally:
            if tmp is not None and os.path.exists(tmp):
                os.remove(tmp)
