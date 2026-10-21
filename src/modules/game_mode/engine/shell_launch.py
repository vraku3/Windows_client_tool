"""Re-export of core.shell_launch (moved there 2026-10-21 so Cleanup can open files
unelevated without importing Game Mode: cleanup <-> game_mode was an import cycle)."""
from core.shell_launch import *  # noqa: F401,F403
from core.shell_launch import (  # noqa: F401 -- names used by system.py and the tests
    SHOW_MIN_NO_ACTIVE, SHOW_NORMAL, TIMEOUT_S, launch, launch_packaged, package_installed,
)
