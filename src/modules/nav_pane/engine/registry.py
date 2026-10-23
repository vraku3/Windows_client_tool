"""HKCU-only backend. Missing raises FileNotFoundError; refusal propagates."""
import copy
import ctypes
import logging
import subprocess
import winreg
from pathlib import Path

logger = logging.getLogger(__name__)


def system_exe(name):
    buffer = ctypes.create_unicode_buffer(32768)
    count = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise OSError("Could not locate System32")
    return str(Path(buffer.value) / name)


def unsigned(value):
    data, kind = value
    return (data & 0xFFFFFFFF if kind == 4 else data, kind)


class Registry:
    def create_key(self, key):
        logger.info("nav pane: create HKCU\\%s", key)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_WRITE):
            logger.debug("Key opened for creation: %s", key)
        self.values(key)  # A refused read-back fails the operation.

    def read(self, key, name=""):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
            return unsigned(winreg.QueryValueEx(handle, name))

    def children(self, key):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
            count = winreg.QueryInfoKey(handle)[0]
            return [winreg.EnumKey(handle, i) for i in range(count)]

    def values(self, key):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
            count = winreg.QueryInfoKey(handle)[1]
            rows = [winreg.EnumValue(handle, i) for i in range(count)]
            return {name: unsigned((data, kind)) for name, data, kind in rows}

    def write(self, key, name, data, kind):
        logger.info("nav pane: write HKCU\\%s [%s]", key, name)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_WRITE) as handle:
            winreg.SetValueEx(handle, name, 0, kind, data)
        if self.read(key, name) != unsigned((data, kind)):
            raise OSError(f"Read-back mismatch: HKCU\\{key} [{name}]")

    def delete_value(self, key, name):
        logger.info("nav pane: delete HKCU\\%s [%s]", key, name)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            logger.debug("Value already absent: %s [%s]", key, name)
        if optional(self, key, name) is not None:
            raise OSError(f"Value still present: {key} [{name}]")

    def delete_tree(self, key):
        logger.info("nav pane: delete tree HKCU\\%s", key)
        try:
            for child in self.children(key):
                self.delete_tree(key + "\\" + child)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            logger.debug("Key already absent: %s", key)
        if snapshot(self, key) is not None:
            raise OSError(f"Key still present: {key}")

    def export(self, key, destination):
        result = subprocess.run([system_exe("reg.exe"), "export", "HKCU\\" + key,
                                 str(destination), "/y"], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        if result.returncode or not Path(destination).is_file() or Path(destination).stat().st_size == 0:
            raise OSError(f"Backup refused for HKCU\\{key}: {result.stderr!r}")


def optional(backend, key, name=""):
    try:
        return backend.read(key, name)
    except FileNotFoundError:
        return None


def snapshot(backend, key):
    try:
        return {"values": backend.values(key),
                "children": {c: snapshot(backend, key + "\\" + c) for c in backend.children(key)}}
    except FileNotFoundError:
        return None


class MemoryRegistry(Registry):
    """Case-insensitive fake. Never accesses winreg or runs a process."""
    def __init__(self):
        self.keys = {}
        self.refused = set()
        self.exports = []

    def _key(self, key):
        key = key.casefold()
        if any(key == p.casefold() or key.startswith(p.casefold() + "\\") for p in self.refused):
            raise PermissionError(f"Read refused: {key}")
        return key

    def read(self, key, name=""):
        try:
            return self.keys[self._key(key)][name.casefold()]
        except KeyError as error:
            raise FileNotFoundError(key) from error

    def children(self, key):
        key = self._key(key)
        if key not in self.keys:
            raise FileNotFoundError(key)
        prefix = key + "\\"
        return sorted({k[len(prefix):].split("\\")[0] for k in self.keys if k.startswith(prefix)})

    def values(self, key):
        key = self._key(key)
        if key not in self.keys:
            raise FileNotFoundError(key)
        return copy.deepcopy(self.keys[key])

    def write(self, key, name, data, kind):
        self.create_key(key)
        self.keys[self._key(key)][name.casefold()] = unsigned((data, kind))

    def create_key(self, key):
        key = self._key(key)
        parts = key.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})

    def delete_value(self, key, name):
        self.keys.get(self._key(key), {}).pop(name.casefold(), None)

    def delete_tree(self, key):
        key = self._key(key)
        for existing in list(self.keys):
            if existing == key or existing.startswith(key + "\\"):
                del self.keys[existing]

    def export(self, key, destination):
        self.exports.append(key)
        Path(destination).write_text(repr(snapshot(self, key)), encoding="utf-8")
