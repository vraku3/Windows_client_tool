import subprocess
import threading
from dataclasses import dataclass
from typing import List, Callable, Optional

from core import winget_list
from core.blocklist import is_blocked


@dataclass
class AppUpdate:
    name: str
    winget_id: str
    installed_version: str
    available_version: str
    source: str


def _run_winget(
    args: List[str],
    output_cb: Optional[Callable[[str], None]] = None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> str:
    """Run winget, stream output via output_cb, return full output.

    `worker.is_cancelled` was only ever checked BETWEEN winget calls (see
    `updates_module.py`'s per-package loops) -- a single stuck call (a
    network stall, an install silently waiting on an interactive prompt)
    left "Update All"'s Cancel button inert until that one call resolved on
    its own. A side thread polls `is_cancelled` and kills the process tree
    if it fires, the same pattern `windows_features/features_module.py`'s
    `_run_dism_action` uses for the identical shape of bug.

    **`--disable-interactivity` is forced onto EVERY call here**, so no
    subcommand can ever block on a prompt. Measured 2026-08-15: an
    unattended run of `winget upgrade --id Blizzard.BattleNet` printed
    "Specify the install root:" and died with `0x8a150042 : Error reading
    input in prompt`, because that package needs an install location and
    winget asked a question no caller in a headless run can answer. With
    the flag, winget refuses cleanly with a message `winget_list.refusal`
    can read and report, instead of hanging or half-installing.
    """
    args = list(args)
    if "--disable-interactivity" not in args:
        args.append("--disable-interactivity")
    try:
        proc = subprocess.Popen(
            ["winget"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stop_watching = threading.Event()

        def _watch_for_cancel() -> None:
            while not stop_watching.wait(0.5):
                if is_cancelled():
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    return

        watcher = threading.Thread(target=_watch_for_cancel, daemon=True)
        watcher.start()
        lines = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                lines.append(line)
                if output_cb:
                    output_cb(line)
            proc.wait()
        finally:
            stop_watching.set()
        return "\n".join(lines)
    except FileNotFoundError:
        msg = "winget not found. Please install App Installer from the Microsoft Store."
        if output_cb:
            output_cb(msg)
        return msg
    except Exception as e:
        msg = f"Error running winget: {e}"
        if output_cb:
            output_cb(msg)
        return msg


def fetch_updates(patterns: Optional[List[str]] = None) -> List[AppUpdate]:
    """Run winget upgrade --include-unknown, parse and return list of updates.

    patterns: optional blocklist patterns (id/name/source-agnostic) — matching
    packages are excluded from the result.
    """
    output = _run_winget(["upgrade", "--include-unknown", "--accept-source-agreements"])
    updates = _parse_upgrade_output(output)
    if patterns:
        updates = [u for u in updates if not is_blocked(u.name, u.winget_id, patterns)]
    return updates


def _parse_upgrade_output(output: str) -> List[AppUpdate]:
    """Parse winget upgrade output table into AppUpdate list."""
    updates = []
    lines = output.splitlines()

    # Find the header line (contains "Name" and "Id" and "Version" etc.)
    header_idx = -1
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line and "Version" in line:
            header_idx = i
            break

    if header_idx < 0:
        return updates

    # Find separator line (dashes)
    sep_idx = -1
    for i in range(header_idx + 1, min(header_idx + 3, len(lines))):
        if lines[i].strip().startswith("-"):
            sep_idx = i
            break

    if sep_idx < 0:
        return updates

    # Determine column positions from header
    header = lines[header_idx]
    # Typical columns: Name, Id, Version, Available, Source
    col_name = header.index("Name") if "Name" in header else 0
    col_id = header.index("Id") if "Id" in header else 30
    col_ver = header.index("Version") if "Version" in header else 60
    col_avail = header.index("Available") if "Available" in header else 80
    col_source = header.index("Source") if "Source" in header else 100

    for line in lines[sep_idx + 1:]:
        if not line.strip() or line.strip().startswith("-"):
            continue
        # Skip summary lines
        if "upgrades available" in line.lower():
            continue

        def _extract(start: int, end: int) -> str:
            if start >= len(line):
                return ""
            return line[start:end].strip() if end <= len(line) else line[start:].strip()

        name = _extract(col_name, col_id)
        winget_id = _extract(col_id, col_ver)
        installed = _extract(col_ver, col_avail)
        available = _extract(col_avail, col_source)
        source = _extract(col_source, len(line))

        if winget_id and available and available != "Unknown":
            updates.append(AppUpdate(
                name=name, winget_id=winget_id,
                installed_version=installed, available_version=available,
                source=source,
            ))

    return updates


@dataclass
class UpdateOutcome:
    """What ONE package's update did, decided by reading its version back.

    `state` is one of core.winget_list's UPDATED / UNCONFIRMED / NOT_APPLICABLE /
    NEEDS_USER / NEEDS_INPUT / FAILED; `detail` is the sentence the table and the
    report show.
    Truthy only when the read-back proved the update.
    """
    winget_id: str
    state: str
    detail: str

    def __bool__(self) -> bool:
        return self.state == winget_list.UPDATED


def install_update(
    winget_id: str,
    output_cb: Callable[[str], None],
    is_cancelled: Callable[[], bool] = lambda: False,
    target_version: str = "",
    before_version: str = "",
    verify: bool = True,
) -> UpdateOutcome:
    """Upgrade one package, then read `winget list` back to decide what happened.

    The upgrade's own words are not evidence: winget prints "Successfully
    installed" while nothing changed and "No applicable upgrade found" for a
    package it still lists as upgradable.
    """
    output = _run_winget(
        ["upgrade", "--id", winget_id, "--exact", "--silent", "--include-unknown",
         "--accept-source-agreements", "--accept-package-agreements"],
        output_cb=output_cb, is_cancelled=is_cancelled,
    )
    if is_cancelled():
        return UpdateOutcome(winget_id, winget_list.UNCONFIRMED,
                             "Cancelled before the installed version could be read back.")
    refused = winget_list.refusal(output)
    if refused:
        return UpdateOutcome(winget_id, *refused)
    if not verify:
        return UpdateOutcome(winget_id, winget_list.UNCONFIRMED,
                             "Not checked: verification after update is turned off in Settings.")
    listed = _run_winget(["list", "--id", winget_id, "--exact",
                          "--accept-source-agreements", "--disable-interactivity"],
                         is_cancelled=is_cancelled)
    if is_cancelled():
        return UpdateOutcome(winget_id, winget_list.UNCONFIRMED,
                             "Cancelled before the installed version could be read back.")
    state, detail = winget_list.verdict(0, listed, winget_id, target_version,
                                        before=before_version, upgrade_output=output)
    return UpdateOutcome(winget_id, state, detail)


def install_all_updates(
    output_cb: Callable[[str], None],
    is_cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Run winget upgrade --all."""
    _run_winget(
        ["upgrade", "--all", "--silent", "--accept-source-agreements",
         "--accept-package-agreements", "--include-unknown"],
        output_cb=output_cb, is_cancelled=is_cancelled,
    )


def show_package_details(winget_id: str, output_cb: Callable[[str], None]) -> None:
    """Run `winget show --id <id> --exact`, streaming output via output_cb."""
    _run_winget(
        ["show", "--id", winget_id, "--exact", "--accept-source-agreements"],
        output_cb=output_cb,
    )
