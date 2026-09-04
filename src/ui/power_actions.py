r"""Three quick, always-visible system actions: force shutdown, restart,
restart into Safe Mode.

Placed as the menu bar's corner widget — visually next to File / Tools /
View, the fastest spot to reach them without opening a module.

**Force shutdown and restart need no elevation.** Shutting down or
restarting the LOCAL machine is a standard Windows user right, not an
admin one, so both fire `shutdown.exe` directly and work in an unelevated
run of the app — matching `adv_startup_btn` in `power_boot/power_module.py`,
which does the same fire-and-forget `Popen` for the same reason.

**Safe Mode is different and IS admin-only**, because setting the boot flag
means `bcdedit`, and that needs elevation. The button is disabled rather
than clicked-and-silently-refused when the app is not elevated — the same
shape as every other admin-gated control in this app.

**Safe Mode does not clear itself.** `bcdedit /set {current} safeboot
minimal` persists across every subsequent restart until it is explicitly
removed; it is not a one-shot flag. `enable_safe_mode()` /
`disable_safe_mode()` in `power_boot/power_module.py` are the pair this
reuses rather than duplicating the bcdedit call, and the confirmation
dialog says exactly where `disable_safe_mode()` lives so nobody restarts
into Safe Mode not knowing how to get back out.

**The bcdedit write is verified before anything restarts.** `enable_safe_mode`
returns the `CompletedProcess` `bcdedit` produced; a non-zero exit there
means the flag was never actually set, and restarting anyway would silently
drop the user into a NORMAL boot while the confirmation dialog told them
Safe Mode. Refusing is the only honest response — see `restart_to_safe_mode`.
"""
from __future__ import annotations

import logging
import subprocess

from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QMessageBox, QToolButton, QWidget,
)

from core.admin_utils import is_admin
from core.confirm import confirm_destructive

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


def force_shutdown() -> None:
    """Shut down now. Every running program closes immediately, whether or
    not it has saved — `/f` is exactly that: don't wait, don't ask."""
    subprocess.Popen(["shutdown", "/s", "/f", "/t", "0"],
                     creationflags=CREATE_NO_WINDOW)


def restart() -> None:
    """Restart now. No `/f`: Windows still gives a program with unsaved
    work the normal chance to ask before closing, exactly as choosing
    Restart from the Start menu would."""
    subprocess.Popen(["shutdown", "/r", "/t", "0"],
                     creationflags=CREATE_NO_WINDOW)


def restart_to_safe_mode() -> None:
    """Set the Safe Mode boot flag, verify it took, then restart.

    Raises `RuntimeError` with `bcdedit`'s own reason if the flag could not
    be set, and restarts nothing in that case — a restart proceeding on an
    unset flag would drop the user into a normal boot while believing they
    asked for Safe Mode. Needs admin; callers must gate on `is_admin()`
    before ever offering this, since `bcdedit` itself will refuse otherwise
    and that refusal is exactly what gets raised here.
    """
    from modules.power_boot.power_module import enable_safe_mode

    result = enable_safe_mode()
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "no output from "
                 "bcdedit").strip()
        raise RuntimeError(
            f"bcdedit would not set the Safe Mode boot flag: {reason}")
    subprocess.Popen(["shutdown", "/r", "/t", "0"],
                     creationflags=CREATE_NO_WINDOW)


class PowerActionsWidget(QWidget):
    """The three pictogram buttons themselves. One instance, menu-bar corner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 8, 0)
        layout.setSpacing(2)

        self._add_button(
            layout, "⏻", "Force Shut Down",
            "Force shut down this computer now. Every running program "
            "closes immediately, whether or not it has saved its work.",
            self._do_shutdown)
        self._add_button(
            layout, "⟲", "Restart",
            "Restart this computer now. Windows still asks any program "
            "with unsaved work to close first.",
            self._do_restart)

        safe_btn = self._add_button(
            layout, "\U0001f6e1", "Restart to Safe Mode",
            "Restart into Safe Mode. Windows starts with only its core "
            "drivers and services — useful when something is stopping it "
            "starting normally. It KEEPS restarting into Safe Mode on "
            "every subsequent restart until turned off from Power & Boot "
            "→ Disable Safe Mode.",
            self._do_safe_mode)
        if not is_admin():
            safe_btn.setEnabled(False)
            safe_btn.setToolTip(
                "Restart to Safe Mode — needs administrator.\n\nUse "
                "“Restart as Admin” in the banner above the "
                "sidebar to enable this.")
            # Qt's disabled palette only recolours the TEXT PEN — a colour
            # emoji glyph is not drawn with it, so `setEnabled(False)` alone
            # leaves this button looking exactly as clickable as the other
            # two (measured: pixel-identical before and after). An opacity
            # effect dims the whole button by compositing, which works
            # regardless of the glyph's own colour.
            dim = QGraphicsOpacityEffect(safe_btn)
            dim.setOpacity(0.35)
            safe_btn.setGraphicsEffect(dim)

    def _add_button(self, layout: QHBoxLayout, glyph: str, title: str,
                    tooltip: str, handler) -> QToolButton:
        button = QToolButton(self)
        button.setText(glyph)
        button.setToolTip(f"{title}\n\n{tooltip}")
        button.setAutoRaise(True)
        font = button.font()
        font.setPointSize(font.pointSize() + 3)
        button.setFont(font)
        button.clicked.connect(handler)
        layout.addWidget(button)
        return button

    def _do_shutdown(self) -> None:
        if not confirm_destructive(
                self, "Force Shut Down",
                "Force shut down this computer now?",
                detail="Every running program closes immediately, whether "
                       "or not it has saved its work."):
            return
        logger.info("Force shutdown requested from the toolbar")
        force_shutdown()

    def _do_restart(self) -> None:
        if not confirm_destructive(
                self, "Restart",
                "Restart this computer now?",
                detail="Windows will ask any program with unsaved work to "
                       "close first."):
            return
        logger.info("Restart requested from the toolbar")
        restart()

    def _do_safe_mode(self) -> None:
        if not is_admin():
            return
        if not confirm_destructive(
                self, "Restart to Safe Mode",
                "Restart into Safe Mode now?",
                detail="Windows starts with only its core drivers and "
                       "services. It keeps restarting into Safe Mode on "
                       "every subsequent restart until you turn it off "
                       "from Power & Boot → Disable Safe Mode."):
            return
        logger.info("Restart to Safe Mode requested from the toolbar")
        try:
            restart_to_safe_mode()
        except Exception as exc:                         # noqa: BLE001
            logger.error("Could not enable Safe Mode: %s", exc)
            QMessageBox.warning(
                self, "Could not restart to Safe Mode",
                f"The Safe Mode boot flag could not be set, so nothing "
                f"was restarted:\n\n{exc}")
