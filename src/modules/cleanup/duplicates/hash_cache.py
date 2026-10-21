"""Optional SQLite digest cache; metadata changes invalidate a path's entry."""
from __future__ import annotations

import logging
import os
import sqlite3
from threading import Lock

logger = logging.getLogger(__name__)


def default_cache_path() -> str:
    from modules.cleanup.cleanup_history import _history_path
    return os.path.join(os.path.dirname(_history_path()), "duplicates_hashes.sqlite")


class HashCache:
    def __init__(self, path: str | None = None):
        self.path = path or default_cache_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = Lock()
        self._db = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS hashes (path TEXT, size INTEGER, "
                         "mtime TEXT, dev TEXT, ino TEXT, kind TEXT, digest TEXT, "
                         "PRIMARY KEY(path,kind))")
        self.hits = 0

    @staticmethod
    def _key(entry):
        # NTFS file indices may exceed SQLite's signed 64-bit integer range.
        p, size, mtime, dev, ino = entry.cache_key
        return p, size, str(mtime), str(dev), str(ino)

    def get(self, entry, kind):
        with self._lock:
            row = self._db.execute("SELECT digest FROM hashes WHERE path=? AND size=? "
                                   "AND mtime=? AND dev=? AND ino=? AND kind=?",
                                   (*self._key(entry), kind)).fetchone()
            if row:
                self.hits += 1
                return row[0]
        return None

    def put(self, entry, kind, digest):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO hashes VALUES (?,?,?,?,?,?,?)",
                             (*self._key(entry), kind, digest))

    def clear(self):
        with self._lock:
            self._db.execute("DELETE FROM hashes")
            self._db.commit()

    def close(self):
        with self._lock:
            try:
                self._db.commit()
            finally:
                self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
