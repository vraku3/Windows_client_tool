r"""Qt-free hosts-file I/O: where the file really is, how it is encoded, and a
save that can always be undone.

Measured on this machine (2026-10-09, unelevated):

* **The hosts file may simply not exist.** `C:\Windows\System32\drivers\etc`
  here holds `lmhosts.sam`, `networks`, `protocol` and `services` and no
  `hosts` at all; name resolution works normally. Windows treats an absent
  file as "no overrides" (Network Health already reads it that way). The
  editor did not: `backup_hosts` is `shutil.copy2` of a file that is not
  there, so EVERY Save failed with "Nothing was written, because the backup
  failed", and Restore failed the same way -- on such a machine the editor
  could never create a single entry. Here an absent file is an empty one,
  there is nothing to back up, and a failed first write is rolled back by
  deleting the file it created.
* **Which file the DNS client reads is a registry value, not a constant.**
  `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\DataBasePath` is
  REG_EXPAND_SZ `%SystemRoot%\System32\drivers\etc` here (readable
  unelevated). Repointing it is an old malware trick: the System32 file stays
  clean for anyone who looks while the resolver reads another one. The
  editor used the hard-coded System32 path, so on such a machine it would
  have edited a file Windows never reads. `hosts_location()` follows the
  value and says when it is not the default.
* **The old save was lossless for the LINES but not for the BYTES.** It read
  with `utf-8, errors="replace"` and wrote UTF-8, so an ANSI (cp1252) comment
  such as `# café` came back as `# caf\ufffd` -- three bytes of U+FFFD on
  disk -- and a UTF-8 file with a BOM lost its BOM, and a UTF-16 file (what
  Notepad's "Unicode" writes) parsed as zero entries and would have been
  rewritten as garbage. Now the file is decoded strictly, written back in
  the encoding it came in, and a file that cannot be decoded faithfully is
  opened read-only with the reason. Rollback copies the backup's BYTES back
  instead of re-decoding it.

A refusal is never an empty file: `HostsText.error` carries it.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from modules.hosts_editor import hosts_analysis as ha

logger = logging.getLogger(__name__)

TCPIP_PARAMS = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
DEFAULT_DATABASE_PATH = r"%SystemRoot%\System32\drivers\etc"


# ----------------------------------------------------------------- location

@dataclass
class HostsLocation:
    path: str                 # the hosts file the DNS client reads
    configured: Optional[str]  # DataBasePath as stored (unexpanded); None = absent/unread
    relocated: bool           # DataBasePath points somewhere other than the default
    error: str = ""           # why DataBasePath could not be read ("" = it was)


def _expand(path: str) -> str:
    return os.path.normpath(os.path.expandvars(path))


def default_hosts_path() -> str:
    return os.path.join(_expand(DEFAULT_DATABASE_PATH), "hosts")


def _read_database_path() -> Optional[str]:
    """Raw DataBasePath, or None when the value is absent. Raises OSError on refusal."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TCPIP_PARAMS) as key:
            value, _kind = winreg.QueryValueEx(key, "DataBasePath")
    except FileNotFoundError:
        return None
    return str(value)


def hosts_location(read_value: Callable[[], Optional[str]] = _read_database_path) -> HostsLocation:
    """Resolve the hosts file the resolver actually uses."""
    default = default_hosts_path()
    try:
        raw = read_value()
    except OSError as exc:
        logger.warning("Cannot read Tcpip DataBasePath: %s", exc)
        return HostsLocation(default, None, False, "DataBasePath could not be read: %s" % exc)
    if not raw or not raw.strip():
        return HostsLocation(default, raw, False)
    path = os.path.join(_expand(raw.strip()), "hosts")
    relocated = os.path.normcase(path) != os.path.normcase(default)
    return HostsLocation(path, raw, relocated)


def location_findings(relocated: bool, configured: Optional[str], path: str,
                      error: str) -> List[ha.Finding]:
    """Findings for what hosts_location() found."""
    if error:
        return [ha.Finding(ha.SEV_LOW, "location_unknown", "Could not confirm which hosts file Windows reads",
                           error + " -- showing the default location.")]
    if relocated:
        return [ha.Finding(ha.SEV_HIGH, "relocated",
                           "Windows reads its hosts file from a non-default folder",
                           "Tcpip\\Parameters\\DataBasePath is %r, so the resolver reads %s. "
                           "Moving it away from %%SystemRoot%%\\System32\\drivers\\etc is a "
                           "known malware technique; confirm it was deliberate." % (configured, path))]
    return []


# ----------------------------------------------------------------- encoding

_BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


@dataclass
class HostsText:
    text: str
    exists: bool
    encoding: str = "utf-8"     # codec to write back with (the BOM is separate)
    newline: str = "\r\n"
    bom: bytes = b""
    error: str = ""             # could not read at all -- never shown as "empty"
    read_only_reason: str = ""  # readable, but writing back would not be faithful

    @property
    def editable(self) -> bool:
        return not self.error and not self.read_only_reason

    def describe(self) -> str:
        if not self.exists:
            return "absent"
        return self.encoding.upper() + (" with BOM" if self.bom else "")


def ansi_codec() -> str:
    try:
        return "cp%d" % ctypes.windll.kernel32.GetACP()
    except (AttributeError, OSError) as exc:
        logger.warning("GetACP unavailable, assuming cp1252: %s", exc)
        return "cp1252"


def _newline(body: str) -> str:
    return "\n" if "\n" in body and "\r\n" not in body else "\r\n"


def decode_hosts(data: bytes, ansi: Optional[str] = None) -> HostsText:
    """Decode faithfully; pick the codec that writes the same bytes back."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            try:
                body = data[len(bom):].decode(codec)
            except UnicodeDecodeError as exc:
                return _unfaithful(data, "it starts with a %s mark but does not decode (%s)" % (codec, exc))
            return HostsText(body, True, codec, _newline(body), bom)
    if b"\x00" in data:
        return _unfaithful(data, "it contains NUL bytes (UTF-16 without a byte-order mark?)")
    for codec in ("utf-8", ansi or ansi_codec()):
        try:
            body = data.decode(codec)
        except UnicodeDecodeError as exc:
            logger.debug("hosts file is not %s: %s", codec, exc)
            continue
        return HostsText(body, True, codec, _newline(body))
    return _unfaithful(data, "it is neither valid UTF-8 nor the ANSI code page")


def _unfaithful(data: bytes, why: str) -> HostsText:
    body = data.decode("utf-8", errors="replace").replace("\x00", "")
    return HostsText(body, True, "utf-8", _newline(body),
                     read_only_reason="Opened read-only: %s. Saving would rewrite bytes "
                                      "this editor cannot reproduce." % why)


def read_hosts_text(path: str) -> HostsText:
    """Absent file = empty, existing = faithfully decoded, refused = error."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return HostsText("", False)
    except PermissionError as exc:
        logger.warning("Hosts file refused: %s", exc)
        return HostsText("", True, error="Permission denied reading %s" % path)
    except OSError as exc:
        logger.warning("Cannot read hosts file: %s", exc)
        return HostsText("", True, error="Could not read %s: %s" % (path, exc))
    return decode_hosts(data)


def encode_hosts(text: str, encoding: str, newline: str = "\r\n", bom: bytes = b"") -> bytes:
    body = text.replace("\r\n", "\n")
    if newline != "\n":
        body = body.replace("\n", newline)
    return bom + body.encode(encoding)


# ----------------------------------------------------------------- save

@dataclass
class SaveOutcome:
    ok: bool
    backup: Optional[str] = None  # None with ok=True means there was no file to back up
    created: bool = False         # the file did not exist before this save
    error: str = ""
    rollback_error: str = ""      # the undo ALSO failed; the user must act


def backup_file(path: str, backup_dir: str) -> str:
    """Timestamped byte copy into backup_dir. Raises OSError."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, "hosts_%s.txt" % stamp)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(backup_dir, "hosts_%s_%d.txt" % (stamp, n))
        n += 1
    shutil.copy2(path, dest)
    if os.path.getsize(dest) != os.path.getsize(path):
        raise OSError("backup size differs from the original")
    return dest


def write_bytes_verified(path: str, data: bytes) -> Optional[str]:
    """Write then read back. None on success, else the reason."""
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        with open(path, "rb") as fh:
            back = fh.read()
    except OSError as exc:
        return str(exc)
    return None if back == data else "the file read back differs from what was written"


def _undo(path: str, backup: Optional[str], created: bool) -> str:
    """Put the file back as it was. Returns "" or why that failed."""
    try:
        if backup:
            shutil.copyfile(backup, path)
        elif created and os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.error("Hosts rollback failed for %s: %s", path, exc)
        return str(exc)
    return ""


def replace_verified(path: str, data: bytes, backup_dir: str) -> SaveOutcome:
    """Back up (if there is anything), write, read back, undo on mismatch."""
    created = not os.path.exists(path)
    backup = None
    if not created:
        try:
            backup = backup_file(path, backup_dir)
        except OSError as exc:
            logger.warning("Hosts backup failed: %s", exc)
            return SaveOutcome(False, error="Nothing was written, because the backup failed: %s" % exc)
    err = write_bytes_verified(path, data)
    if err:
        logger.warning("Hosts write failed (%s); rolling back", err)
        return SaveOutcome(False, backup, created, err, _undo(path, backup, created))
    return SaveOutcome(True, backup, created)


def save_hosts(path: str, text: str, read: HostsText, backup_dir: str) -> SaveOutcome:
    """Save `text` in the encoding and line endings the file was read with."""
    if not read.editable:
        return SaveOutcome(False, error=read.error or read.read_only_reason)
    try:
        data = encode_hosts(text, read.encoding, read.newline, read.bom)
    except UnicodeEncodeError as exc:
        return SaveOutcome(False, error="An edit contains a character the file's encoding "
                                        "(%s) cannot hold: %s" % (read.encoding, exc))
    return replace_verified(path, data, backup_dir)


def restore_from(backup_path: str, path: str, backup_dir: str) -> Tuple[SaveOutcome, bytes]:
    """Byte-for-byte restore of a backup over `path` (current file backed up first)."""
    try:
        with open(backup_path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        logger.warning("Cannot read hosts backup %s: %s", backup_path, exc)
        return SaveOutcome(False, error="Could not read the backup: %s" % exc), b""
    return replace_verified(path, data, backup_dir), data
