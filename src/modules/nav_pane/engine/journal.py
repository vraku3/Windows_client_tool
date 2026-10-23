"""Atomic, durable write-ahead records. Corruption is an error, never an empty journal."""
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Journal:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else Path(os.environ["APPDATA"]) / "WindowsTweaker" / "nav_pane"
        self.path = self.directory / "journal.json"

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "entries": {}, "transactions": {}}
        if (not isinstance(data, dict) or data.get("version") != 1
                or not all(isinstance(data.get(k), dict) for k in ("entries", "transactions"))):
            raise ValueError("Navigation pane journal is invalid; no changes are allowed.")
        return data

    def save(self, data):
        atomic_json(self.path, data)
