"""Decide whether a winget upgrade really happened. Qt-free.

`winget upgrade` exits 0 and prints "Successfully installed" while nothing
changed, and prints "No applicable upgrade found" for a package it then still
lists as upgradable. The only proof is reading the installed version back with
`winget list --id X --exact` and comparing it with the version that was
offered. App Buster's update action and the Updates module's App Updates tab
and Run All stage all decide through `verdict()` so they cannot disagree.

`winget list` must be parsed by COLUMN, from the header offsets: ids carry
spaces (`ARP\\Machine\\X64\\...`) and names carry their own dots.
"""
import re
import os
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

UPDATED = "UPDATED"
UNCONFIRMED = "UNCONFIRMED"      # could not prove it either way
NOT_APPLICABLE = "NOT APPLICABLE"  # winget had nothing that applies to this PC
NEEDS_USER = "NEEDS USER"        # per-user install, refused from an elevated process
NEEDS_INPUT = "NEEDS INPUT"      # the package wants an answer only a person can give
DIFF_TECH = "DIFF TECH"          # installed via a different technology (e.g. system MSIX vs winget)
FAILED = "FAILED"                # read back: the old version is still installed
BROKEN = "BROKEN"                # portable metadata exists, program files do not
STILL_RUNNING = "STILL RUNNING"  # do not overlap a potentially running installer
SKIPPED = "SKIPPED"

PER_USER_REFUSAL = "cannot be uninstalled when running with administrator privileges"
NO_APPLICABLE = "no applicable upgrade found"
#: A package that needs an install location, or asks anything else interactively.
#: Measured on this machine (2026-10-15): `Blizzard.BattleNet` prints
#: "Specify the install root:" and `0x8a150042 : Error reading input in prompt`.
#: Every winget call now runs with `--disable-interactivity`, so this is what
#: such a package reports instead of blocking.
NEEDS_INPUT_MARKERS = (
    "requires an install location",
    "specify the install root",
    "error reading input in prompt",
    "0x8a150042",
)
LOCATION_MARKERS = ("requires an install location", "specify the install root")
#: Measured on this machine (2026-10-28): Microsoft.Edge is installed as a
#: system component and winget offers a newer version through its own source;
#: the upgrade exits 0x8A15008E (2316632206) with the message below.
DIFF_TECH_MARKERS = (
    "install technology is different",
    "0x8a15008e",
)
DIFF_TECH_EXIT = 0x8A15008E  # 2316632206 unsigned


def portable_snapshot(winget_id: str, scope: str = "") -> list:
    """Remember indexed portable folders and links BEFORE winget can remove them.

    The portable index is the Packages directory, independent of installed.db.
    Retaining the pre-install paths catches a deleted directory/link afterwards.
    """
    roots = []
    if scope != "machine" and os.environ.get("LOCALAPPDATA"):
        roots.append(("user", os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "WinGet")))
    if scope != "user" and os.environ.get("ProgramFiles"):
        roots.append(("machine", os.path.join(os.environ["ProgramFiles"], "WinGet")))
    result = []
    for kind, root in roots:
        packages = os.path.join(root, "Packages")
        if not os.path.isdir(packages):
            continue
        try:
            for name in os.listdir(packages):
                if not name.casefold().startswith(winget_id.casefold() + "_"):
                    continue
                folder = os.path.join(packages, name)
                links = []
                link_root = os.path.join(root, "Links")
                if os.path.isdir(link_root):
                    for link_name in os.listdir(link_root):
                        link = os.path.join(link_root, link_name)
                        target = os.path.normcase(os.path.realpath(link))
                        real_folder = os.path.normcase(os.path.realpath(folder))
                        try:
                            if os.path.commonpath([target, real_folder]) == real_folder:
                                links.append(link)
                        except ValueError:
                            logger.debug("Portable link %s targets another drive", link)
                result.append((kind, folder, links))
        except (OSError, ValueError):
            logger.warning("Could not read portable index %s", packages, exc_info=True)
    return result


def portable_problem(snapshot: list, winget_id: str, version: str) -> Optional[str]:
    for scope, folder, links in snapshot:
        has_exe = any(name.casefold().endswith(".exe") and os.path.isfile(os.path.join(root, name))
                      for root, _dirs, files in os.walk(folder) for name in files)
        real_folder = os.path.normcase(os.path.realpath(folder))
        links_ok = True
        for link in links:
            try:
                target = os.path.normcase(os.path.realpath(link))
                if not os.path.isfile(link) or os.path.commonpath([target, real_folder]) != real_folder:
                    links_ok = False
            except (OSError, ValueError):
                links_ok = False
        if not has_exe or not links_ok:
            return (f"winget recorded {version or 'an unknown version'} but the program files are missing "
                    f"-- reinstall: winget install --id {winget_id} --exact --scope {scope} --force")
    return None


def needs_install_location(output: str) -> bool:
    return any(marker in output.lower() for marker in LOCATION_MARKERS)


def parse_list_row(output: str, winget_id: str) -> Optional[Tuple[str, str]]:
    """Read installed/available versions from an exact ID's table columns."""
    columns = None
    for line in output.splitlines():
        labels = list(re.finditer(r"\S+", line))
        names = [label.group() for label in labels]
        if names[:3] == ["Name", "Id", "Version"]:
            columns = {label.group(): label.start() for label in labels}
            continue
        if columns is None:
            continue
        version_start = columns["Version"]
        if line[columns["Id"]:version_start].strip() != winget_id:
            continue
        available_start = columns.get("Available", columns.get("Source", len(line)))
        installed = line[version_start:available_start].strip()
        available = (line[available_start:columns.get("Source", len(line))].strip()
                     if "Available" in columns else "")
        return (installed, available) if installed else None
    return None


def _parts(v: str):
    v = v.strip().casefold().removeprefix("v")
    if not re.fullmatch(r"\d+(\.\d+)*", v):
        return v
    nums = [int(p) for p in v.split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def same_version(a: str, b: str) -> bool:
    """"v2.0", "2.0" and "2.0.0" are one version; anything non-numeric compares as text."""
    return _parts(a) == _parts(b)


def newer_version(a: str, b: str) -> bool:
    """True only when both are numeric and `a` is strictly newer than `b` --
    a non-numeric version cannot be ordered, so it is never "newer"."""
    pa, pb = _parts(a), _parts(b)
    return isinstance(pa, tuple) and isinstance(pb, tuple) and pa > pb


def refusal(upgrade_output: str) -> Optional[Tuple[str, str]]:
    """(state, detail) when the upgrade's own output already says nothing happened."""
    text = upgrade_output.lower()
    if PER_USER_REFUSAL in text:
        return (NEEDS_USER, "Installed for your account only, and winget will not update it "
                            "from an app running as administrator. Start the app without admin to update it.")
    if any(marker in text for marker in NEEDS_INPUT_MARKERS):
        return (NEEDS_INPUT, "This package needs an answer only you can give -- an install "
                             "location, or an interactive prompt -- so winget left it as it "
                             "was. Update this one by hand (winget with --location, or its "
                             "own updater).")
    if any(marker in text for marker in DIFF_TECH_MARKERS):
        return (DIFF_TECH, "This package was installed by Windows or its own installer, not "
                           "by winget, so winget cannot upgrade it. Update it through "
                           "Settings → Apps, the app’s own updater, or Windows Update.")
    if NO_APPLICABLE in text:
        return (NOT_APPLICABLE, "winget found no update that applies to this PC.")
    return None


def _exact(version: str) -> bool:
    return bool(version) and version.casefold() != "unknown" and version[:1] not in "<>"


def verdict(list_rc: int, list_output: str, winget_id: str, target: str,
            upgrade_rc: Optional[int] = None, before: str = "",
            upgrade_output: str = "") -> Tuple[str, str]:
    """(state, detail) from `winget list --id <id> --exact` read after the upgrade.

    `before` is the version installed before the upgrade: winget installs the
    newest installer that APPLIES, which can be older than the newest one
    offered, so a version that moved off `before` is an update even when it is
    not `target`.
    """
    row = parse_list_row(list_output, winget_id) if list_rc == 0 else None
    if row is None:
        return (UNCONFIRMED, "Could not read the installed version back, so the update is unconfirmed.")
    installed, available = row
    if not _exact(installed):
        # Windows records no exact version for it, and an update does not add one.
        return (UNCONFIRMED, "Windows records no exact version for it, so the update is unconfirmed.")
    if target and same_version(installed, target):
        return (UPDATED, f"Now {installed}")
    if _exact(before) and newer_version(installed, before):
        return (UPDATED, f"Now {installed} (was {before}; {target or 'a newer version'} is offered, "
                         "but this is the newest one that applies here)")
    if any(word in upgrade_output.lower() for word in ("restart", "reboot")):
        return (UNCONFIRMED, f"{installed} is still installed; the installer asked for a restart, "
                             "so check again after restarting.")
    if not available:
        # No newer version listed is not proof: the source may simply not have answered.
        return (UNCONFIRMED, f"{installed} is installed, not {target or 'the new version'}; "
                             "the update is unconfirmed.")
    if upgrade_rc is not None and (upgrade_rc & 0xFFFFFFFF) == DIFF_TECH_EXIT:
        return (DIFF_TECH, "This package was installed by Windows or its own installer, not "
                           "by winget, so winget cannot upgrade it. Update it through "
                           "Settings → Apps, the app’s own updater, or Windows Update.")
    exit_text = f"exit {upgrade_rc}" if upgrade_rc is not None else "it finished"
    return (FAILED, f"winget finished ({exit_text}), but {installed} is still installed "
                    f"({available} available).")
