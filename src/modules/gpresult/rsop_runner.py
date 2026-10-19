"""Run `gpresult` and say honestly what came back.

The trap this file exists to avoid: **unelevated, `gpresult /x` succeeds and
returns half the report.** It exits 0, writes a well-formed XML file, and that
file contains only `<UserResults>` -- the computer half is refused with no
error anywhere. Asking for it explicitly is what makes the refusal visible::

    > gpresult /x out.xml /f /scope computer
    ERROR: Access Denied.
    (exit 1, no file written)

So a missing `<ComputerResults>` is never reported as "no computer policy".
It is reported as "not collected, and here is why".
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import uuid
from typing import Optional, Tuple

from core.admin_utils import is_admin

from modules.gpresult.rsop_parser import RsopResult, parse_rsop_xml

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 90

_NEEDS_ELEVATION = (
    "Computer policy was not collected: reading it requires an elevated run. "
    "Restart this tool as administrator to see Computer Configuration."
)

_NO_COMPUTER_SECTION = (
    "gpresult returned no Computer Configuration section, even though this "
    "tool is running elevated. The report itself is incomplete."
)

_NO_USER_SECTION = (
    "gpresult returned no User Configuration section for the signed-in user."
)


def _no_window() -> int:
    """CREATE_NO_WINDOW, or 0 off Windows so tests can run anywhere."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_gpresult_xml(scope: Optional[str] = None,
                     timeout: int = DEFAULT_TIMEOUT) -> Tuple[bool, bytes, str]:
    """Run `gpresult /x` and return (ok, xml bytes, message).

    The exit code is not the success signal -- gpresult writes its complaints
    to stdout and can leave no file behind while still exiting 0 under some
    failures. The file existing and being non-empty is the signal.
    """
    path = os.path.join(tempfile.gettempdir(),
                        "wct_gpresult_%s.xml" % uuid.uuid4().hex)
    cmd = ["gpresult", "/x", path, "/f"]
    if scope:
        cmd += ["/scope", scope]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              creationflags=_no_window(), timeout=timeout)
    except FileNotFoundError:
        return False, b"", "gpresult.exe was not found on this system."
    except subprocess.TimeoutExpired:
        return False, b"", "gpresult did not finish within %ds." % timeout
    finally:
        pass

    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            # gpresult puts the real reason on stdout, not stderr.
            complaint = (proc.stdout or "").strip() or (proc.stderr or "").strip()
            return False, b"", complaint or "gpresult produced no report."
        with open(path, "rb") as handle:
            return True, handle.read(), ""
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            logger.debug("Could not remove %s", path, exc_info=True)


def collect_rsop(timeout: int = DEFAULT_TIMEOUT,
                 elevated: Optional[bool] = None) -> RsopResult:
    """The full resultant set of policy, with each missing half explained.

    `elevated` is injectable so the unelevated path can be tested on an
    elevated machine and the other way round.
    """
    if elevated is None:
        elevated = is_admin()

    ok, xml, message = run_gpresult_xml(timeout=timeout)
    if not ok:
        result = RsopResult()
        result.error = message
        result.computer.unavailable_reason = message
        result.user.unavailable_reason = message
        return result

    result = parse_rsop_xml(xml)
    if result.error:
        result.computer.unavailable_reason = result.error
        result.user.unavailable_reason = result.error
        return result

    if not result.computer.available:
        result.computer.unavailable_reason = (
            _NO_COMPUTER_SECTION if elevated else _NEEDS_ELEVATION)
    if not result.user.available:
        result.user.unavailable_reason = _NO_USER_SECTION
    return result


def export_html_report(path: str, timeout: int = DEFAULT_TIMEOUT) -> Tuple[bool, str]:
    """Write Microsoft's own HTML RSOP report to `path`.

    gpresult writes into a fresh temp file beside `path`, which replaces
    `path` only once it exists and is non-empty: judged by the destination
    alone, a failed run over an EARLIER report announced that stale report
    as the new one."""
    folder = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(folder, ".gpresult-%d-%s.html" % (os.getpid(), os.urandom(4).hex()))
    try:
        try:
            proc = subprocess.run(["gpresult", "/h", tmp, "/f"],
                                  capture_output=True, text=True,
                                  creationflags=_no_window(), timeout=timeout)
        except FileNotFoundError:
            return False, "gpresult.exe was not found on this system."
        except subprocess.TimeoutExpired:
            return False, "gpresult did not finish within %ds." % timeout

        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            try:
                os.replace(tmp, path)
            except OSError as exc:
                return False, "The report was written but could not be saved as %s: %s" % (path, exc)
            return True, ""
        complaint = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return False, complaint or "gpresult wrote no report."
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError as exc:
                logger.warning("could not remove %s: %s", tmp, exc)


def mmc_console_path(name: str) -> Optional[str]:
    """Full path to an MMC console, or None if this edition lacks it.

    gpedit.msc and rsop.msc ship with Pro and above but not with Home, so the
    buttons that open them have to be able to find out.
    """
    candidate = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)
    return candidate if os.path.exists(candidate) else None
