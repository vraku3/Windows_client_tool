"""Atomic, durable write-ahead records. Corruption is an error, never an empty journal."""
import json
import base64
import os
import tempfile
import errno
import msvcrt
import threading
import time
from contextlib import contextmanager
from pathlib import Path


def encode_value(value):
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"Unsupported journal value: {type(value).__name__}")


def decode_value(value):
    if set(value) == {"__bytes__"} and isinstance(value["__bytes__"], str):
        return base64.b64decode(value["__bytes__"], validate=True)
    return value


def loads(text):
    return json.loads(text, object_hook=decode_value)


def canonical(value):
    return loads(json.dumps(value, default=encode_value))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, default=encode_value)
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
        self._local = threading.local()

    @contextmanager
    def lock(self, timeout=2):
        """Hold one per-user OS lock across the entire read/plan/write/repair."""
        if getattr(self._local, "locked", False):
            yield
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.directory / "journal.lock", os.O_CREAT | os.O_RDWR)
        # Windows byte-range locks may extend past EOF; no shared-file write is needed.
        with os.fdopen(descriptor, "r+b") as stream:
            deadline = time.monotonic() + timeout
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Another WinClientTool window is changing navigation entries; try again shortly.") from error
                    time.sleep(0.05)
            self._local.locked = True
            try:
                yield
            finally:
                self._local.locked = False
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def read(self):
        try:
            data = loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "entries": {}, "transactions": {}}
        if (not isinstance(data, dict) or data.get("version") != 1
                or not all(isinstance(data.get(k), dict) for k in ("entries", "transactions"))):
            raise ValueError("Navigation pane journal is invalid; no changes are allowed.")
        return data

    def save(self, data):
        atomic_json(self.path, data)
