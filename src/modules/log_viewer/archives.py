r"""Getting logs out of an archive so the viewer can open them.

Two kinds: a `.cab`, which is how Windows 11 rolls CBS, and a `.zip`, which
is how logs arrive from someone else's machine. The zip half refuses members
that would land outside the directory it was given -- see `extract_zip`.


Windows 11 rolls CBS into cabinet files -- four of them in
`C:\Windows\Logs\CBS` on this machine -- and the plain `CBS.log` beside them
is usually the smallest part of the story.

**Extracted with `expand.exe`, which ships with Windows**, rather than with
7-Zip as the older CBS tab does. 7-Zip happens to be installed on this
machine; it is not on most, and depending on it would make this work on the
developer's box and nowhere else.

Two things real CBS cabs do that a naive extractor gets wrong, both found by
running it against the real folder:

* **The member inside is named like the cab, extension and all** --
  `cbspersist_20260829190803.cab` -- so looking for `*.log` in the output
  finds nothing at all.
* **It is 15.8 MB of text from a 465 KB cab.** The caller has to be given
  somewhere with room, and it has to be cleaned up.

No Qt.
"""
import logging
import os
import subprocess
import zipfile
import json
import shutil
import time
import uuid

import psutil

logger = logging.getLogger(__name__)

#: What counts as a log inside a bundle. Same rule the folder
#: scan uses, kept here rather than imported so this module does
#: not depend on the merge engine.
LOG_SUFFIXES = (".log", ".lo_")

#: Windows' own cabinet extractor. Full path deliberately: a bare `expand`
#: resolves to a different, unrelated tool under a POSIX shell.
_EXPAND = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "expand.exe")

#: A cabinet starts with these four bytes. Checked before shelling out, so a
#: file that is merely NAMED .cab fails with a sentence rather than with
#: whatever expand.exe says about it.
_MAGIC = b"MSCF"
_OWNED = set()


def extraction_root() -> str:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise OSError("LOCALAPPDATA is unavailable")
    return os.path.abspath(os.path.join(local, "WindowsTweaker", "logviewer-extract"))


def _linked(path):
    return os.path.islink(path) or os.path.isjunction(path)


def _managed_folder(path):
    root = extraction_root()
    path = os.path.abspath(path)
    try:
        valid_name = str(uuid.UUID(os.path.basename(path))) == os.path.basename(path)
    except ValueError:
        return False
    return (valid_name and os.path.dirname(path) == root and not _linked(path)
            and not _linked(root) and os.path.normcase(os.path.realpath(root)) == os.path.normcase(root))


def _new_extraction() -> str:
    root = extraction_root()
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, str(uuid.uuid4()))
    if not _managed_folder(target):
        raise OSError("the extraction directory is redirected; left alone")
    # Obtain the identity first: failure must not leave an ownerless directory.
    owner = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    os.mkdir(target)
    try:
        with open(os.path.join(target, "owner.json"), "x", encoding="utf-8") as handle:
            json.dump(owner, handle)
    except OSError:
        shutil.rmtree(target)
        raise
    _OWNED.add(target)
    return target


def _remove_extraction(folder):
    if not _managed_folder(folder):
        return
    try:
        shutil.rmtree(folder)
        _OWNED.discard(folder)
    except FileNotFoundError:
        _OWNED.discard(folder)
    except OSError as error:
        logger.warning("could not remove log extraction %s: %s", folder, error)


def release_extraction(path: str, keep=()) -> None:
    """Release only a folder created by this process, never an arbitrary path."""
    for folder in tuple(_OWNED):
        if _is_inside(folder, path):
            if any(_is_inside(folder, p) for p in keep):
                return
            _remove_extraction(folder)
            return


def cleanup_stale_extractions() -> None:
    """Startup sweep confined to UUID folders in our fixed extraction directory.

    PID reuse is checked against process creation time; an unreadable owner is
    kept. Fresh ownerless folders may be another process creating its lease.
    """
    try:
        root = extraction_root()
        if not os.path.isdir(root) or _linked(root) or os.path.normcase(os.path.realpath(root)) != os.path.normcase(root):
            return
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False) or not _managed_folder(entry.path):
                    continue
                try:
                    with open(os.path.join(entry.path, "owner.json"), encoding="utf-8") as handle:
                        owner = json.load(handle)
                    pid, created = int(owner["pid"]), float(owner["created"])
                    if pid <= 0 or created <= 0:
                        continue
                    try:
                        alive = abs(psutil.Process(pid).create_time() - created) < .001
                    except psutil.NoSuchProcess:
                        alive = False
                    if not alive:
                        _remove_extraction(entry.path)
                except FileNotFoundError:
                    if time.time() - entry.stat(follow_symlinks=False).st_mtime > 3600:
                        _remove_extraction(entry.path)
                except (OSError, ValueError, KeyError, TypeError, psutil.Error) as error:
                    logger.warning("cannot verify extraction owner %s: %s", entry.path, error)
    except OSError as error:
        logger.warning("cannot check stale log extractions: %s", error)


def legacy_cab_output(path: str) -> bool:
    """Only old, flat CBS expand output in the exact legacy TEMP prefix."""
    if not os.path.basename(path).startswith("logviewer_cab_") or _linked(path) or not os.path.isdir(path):
        return False
    try:
        if time.time() - os.lstat(path).st_mtime < 3600:
            return False
        count = 0
        with os.scandir(path) as entries:
            for entry in entries:
                low = entry.name.lower()
                if (_linked(entry.path) or not entry.is_file(follow_symlinks=False)
                        or not (low.endswith(LOG_SUFFIXES) or (low.startswith("cbspersist_") and low.endswith(".cab")))
                        or time.time() - entry.stat(follow_symlinks=False).st_mtime < 3600):
                    return False
                with open(entry.path, "rb") as handle:
                    head = handle.read(4096)
                # Real rolled CBS members are text despite their .cab suffix.
                if not head or head.startswith(_MAGIC) or (b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff"))):
                    return False
                count += 1
        return count > 0
    except OSError as error:
        logger.warning("cannot verify legacy log extraction %s: %s", path, error)
        return False


def _extract_owned(extractor, path, into, empty):
    target = None
    success = False
    try:
        target = into or _new_extraction()
        result, problem = extractor(path, target)
        success = bool(result) and not problem
        return result, problem
    except (OSError, psutil.Error) as error:
        logger.warning("could not extract %s: %s", path, error)
        return empty, str(error)
    finally:
        if not into and target and not success:
            _remove_extraction(target)


def is_cab(path: str) -> bool:
    return bool(path) and path.lower().endswith(".cab")


def is_zip(path: str) -> bool:
    return bool(path) and path.lower().endswith(".zip")


def largest_cbs_cab(environ=None) -> str:
    r"""The biggest `CbsPersist_*.cab`, or "".

    Largest rather than newest, for the reason the log menu already learned:
    the newest CBS archive is routinely the smallest.
    """
    environ = os.environ if environ is None else environ
    root = environ.get("SystemRoot", "")
    if not root:
        return ""
    folder = os.path.join(root, "Logs", "CBS")
    if not os.path.isdir(folder):
        return ""
    biggest, biggest_size = "", -1
    try:
        names = os.listdir(folder)
    except OSError:
        return ""
    for name in names:
        lowered = name.lower()
        if not (lowered.startswith("cbspersist") and lowered.endswith(".cab")):
            continue
        path = os.path.join(folder, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            logger.debug("largest_cbs_cab: skipping an item that could not be read", exc_info=True)
            continue
        if size > biggest_size:
            biggest, biggest_size = path, size
    return biggest


def extract_cab(path: str, into: str = None):
    return _extract_owned(_extract_cab, path, into, "")


def _extract_cab(path: str, into: str):
    """`(extracted path, problem)`. One of the two is always empty.

    Returns the LARGEST file expand produced, because a CBS cab holds one
    member and looking for `*.log` would find nothing -- the member carries
    the cab's own name.
    """
    if not os.path.isfile(path):
        return "", f"{os.path.basename(path) or path} is not there"
    try:
        with open(path, "rb") as handle:
            if handle.read(4) != _MAGIC:
                return "", (f"{os.path.basename(path)} is not a cabinet file")
    except OSError as problem:
        return "", f"could not read {os.path.basename(path)}: {problem}"

    if not os.path.isfile(_EXPAND):
        return "", "expand.exe was not found on this machine"

    target = into
    try:
        done = subprocess.run(
            [_EXPAND, "-F:*", path, target],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as problem:
        return "", f"could not run expand.exe: {problem}"
    except subprocess.TimeoutExpired:
        return "", "expand.exe did not finish within two minutes"

    if done.returncode:
        detail = (done.stderr or done.stdout or "").strip()
        return "", f"expand.exe failed (exit {done.returncode}): {detail}"

    produced = []
    try:
        for name in os.listdir(target):
            candidate = os.path.join(target, name)
            if name != "owner.json" and os.path.isfile(candidate):
                produced.append((os.path.getsize(candidate), candidate))
    except OSError as problem:
        return "", f"could not read the extracted files: {problem}"

    if not produced:
        # expand exits 0 while extracting nothing when the cab is empty or
        # unreadable, so rc is not the signal -- what came out is.
        detail = (done.stdout or done.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else "nothing was extracted"
        return "", f"{os.path.basename(path)}: {reason}"

    produced.sort(reverse=True)
    return produced[0][1], ""


def _is_inside(target: str, path: str) -> bool:
    """Whether `path` really lands inside `target`.

    Compared after resolving both, because that is the only comparison that
    survives `..`, an absolute member name, and a symlinked temp directory.
    """
    target = os.path.realpath(target)
    resolved = os.path.realpath(path)
    return resolved == target or resolved.startswith(target + os.sep)


def extract_zip(path: str, into: str = None):
    return _extract_owned(_extract_zip, path, into, [])


def _extract_zip(path: str, into: str):
    """`(log paths, problem)` from a bundle of collected logs.

    This is the shape logs arrive in from someone else's machine.

    **Members that would land outside `into` are refused, not extracted.**
    `zipfile` will write `../escaped.log` or an absolute member name wherever
    it says, and a viewer that unpacks a bundle from a stranger's machine has
    to refuse that. Everything is joined to the target and then checked after
    resolution, which is what survives `..`, an absolute name, and a
    symlinked temp directory alike.
    """
    if not os.path.isfile(path):
        return [], f"{os.path.basename(path) or path} is not there"
    target = into
    os.makedirs(target, exist_ok=True)

    found = []
    refused = 0
    try:
        with zipfile.ZipFile(path) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                if not name.lower().endswith(LOG_SUFFIXES):
                    continue
                destination = os.path.join(target, name)
                if not _is_inside(target, destination):
                    refused += 1
                    logger.warning(
                        "Refused a zip member that would land outside %s: %s",
                        target, name)
                    continue
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with bundle.open(member) as source:
                    with open(destination, "wb") as handle:
                        handle.write(source.read())
                found.append(destination)
    except zipfile.BadZipFile:
        return [], f"{os.path.basename(path)} is not a zip file"
    except OSError as problem:
        return [], f"could not read {os.path.basename(path)}: {problem}"

    if not found:
        detail = (f" ({refused} member(s) refused as unsafe)" if refused
                  else "")
        return [], (f"{os.path.basename(path)} holds no logs "
                    f"(*.log, *.lo_){detail}")
    return sorted(found), ""
