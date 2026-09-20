"""Windows' own Disk Cleanup (cleanmgr.exe), automated via its documented
/sageset + /sagerun switches.

`/sageset:n` opens cleanmgr's real checkbox dialog once, lets the user pick
categories (System Restore & Shadow Copies, old Windows Update files,
Delivery Optimization, per-user temp files, etc. -- categories this app's
own scanners cannot reach, since some require the servicing stack or a
volume cache handler this app doesn't reimplement), and writes the choice
to the registry under a numbered profile. `/sagerun:n` replays exactly that
choice silently. Neither switch's scope is limited to what this app's own
scanners cover -- that is the point: cleanmgr's cache handlers see categories
(e.g. "System error memory dump files", "DirectX Shader Cache") this app has
no other access to.

No PyQt6 here, matching the scan/store split TreeSize and Monitor Control
keep -- this stays testable without a display.
"""
import logging
import winreg
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

#: This app's own profile number, distinct from the "65" many online
#: cleanmgr guides use by convention -- picked so a user who has already
#: hand-configured their own /sageset:65 profile for something else is
#: never silently overwritten by this app's own Configure button.
PROFILE_NUMBER = 42

_VOLUME_CACHES_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"


@dataclass(frozen=True)
class ConfiguredCategory:
    handler_key: str  # the VolumeCaches subkey name, e.g. "Temporary Files"


def configured_categories() -> Optional[List[ConfiguredCategory]]:
    """Which cleanmgr categories are checked in PROFILE_NUMBER's profile,
    or None when the registry itself could not be read -- a refusal is
    never collapsed into "nothing configured" (see CLAUDE.md's Tweak
    System / Security Dashboard sections for why that distinction matters
    everywhere else in this app)."""
    value_name = f"StateFlags{PROFILE_NUMBER:04d}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VOLUME_CACHES_KEY) as root:
            subkey_count = winreg.QueryInfoKey(root)[0]
            checked = []
            for i in range(subkey_count):
                name = winreg.EnumKey(root, i)
                try:
                    with winreg.OpenKey(root, name) as handler:
                        flag, _kind = winreg.QueryValueEx(handler, value_name)
                        if flag:
                            checked.append(ConfiguredCategory(handler_key=name))
                except FileNotFoundError:
                    # Ordinary: this handler has never been touched by this
                    # profile, not a refusal worth surfacing to the caller.
                    logger.debug("No %s under VolumeCaches\\%s", value_name, name)
                    continue
            return checked
    except OSError:
        logger.warning("Could not read %s\\%s -- reporting as unreadable, "
                       "not as \"nothing configured\"",
                       _VOLUME_CACHES_KEY, value_name, exc_info=True)
        return None


def sageset_command() -> List[str]:
    """Opens cleanmgr's real checkbox dialog for PROFILE_NUMBER. Blocking
    and interactive -- run on a background Worker so it does not freeze
    the Qt event loop, never with a timeout (the user may leave the
    dialog open)."""
    return ["cleanmgr.exe", f"/sageset:{PROFILE_NUMBER}"]


def sagerun_command() -> List[str]:
    """Silently cleans exactly whatever categories PROFILE_NUMBER has
    checked. Non-interactive -- safe to run with a timeout."""
    return ["cleanmgr.exe", f"/sagerun:{PROFILE_NUMBER}"]
