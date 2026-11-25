import logging
import os
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core import phosphor
from core.semantic_colors import (
    CHROME_PALETTES,
    PANE_BACKGROUND,
    SEMANTIC_PALETTES,
    set_theme as _set_semantic_theme,
)
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeManager(QObject):
    """Manages dark/light theme switching via QSS stylesheets."""

    #: Emitted with the new theme name once a theme has actually been applied.
    #: A stylesheet reaches everything that paints through the style and
    #: nothing else, so custom painters -- PerfMon's charts, TreeSize's
    #: proportion-bar delegate -- need telling. Not emitted when the sheet
    #: could not be loaded: nothing changed, and a listener that repainted
    #: would be acting on a theme that is not in force.
    theme_changed = pyqtSignal(str)

    THEMES = ("dark", "light") + tuple(phosphor.PHOSPHOR_THEMES)

    def __init__(self, styles_dir: str):
        super().__init__()
        self._styles_dir = styles_dir
        self._current_theme: str = "dark"

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def apply_theme(self, theme: str) -> None:
        if theme not in self.THEMES:
            logger.warning("Unknown theme '%s', falling back to dark", theme)
            theme = "dark"
        derived = theme in phosphor.PHOSPHOR_THEMES
        # A phosphor theme is dark.qss recoloured, so it can never fall out of
        # step with the dark theme's rules.
        qss_path = os.path.join(self._styles_dir, "dark.qss" if derived else f"{theme}.qss")
        stylesheet = self._load_qss(qss_path)
        if stylesheet is not None and derived:
            stylesheet = phosphor.recolour_qss(stylesheet, theme)
        if stylesheet is not None:
            # Colours applied from Python cannot come from the sheet; keep the
            # semantic palette in step before anyone repaints with it.
            _set_semantic_theme(theme)
            app = QApplication.instance()
            if app:
                app.setPalette(self._build_palette(theme))
                app.setStyleSheet(stylesheet)
            self._current_theme = theme
            logger.info("Applied theme: %s", theme)
            self.theme_changed.emit(theme)
        else:
            logger.error("Failed to load theme '%s' from %s", theme, qss_path)

    @staticmethod
    def _build_palette(theme: str) -> QPalette:
        """Build an application QPalette matching the active theme so widgets/HTML
        falling back to QPalette never inherit Windows light-mode black text on dark."""
        bg = PANE_BACKGROUND.get(theme, PANE_BACKGROUND["dark"])
        chr_p = CHROME_PALETTES.get(theme, CHROME_PALETTES["dark"])
        sem_p = SEMANTIC_PALETTES.get(theme, SEMANTIC_PALETTES["dark"])

        pal = QPalette()
        is_light = theme == "light"
        btn_bg = "#e8e8e8" if is_light else "#21262d"
        highlight = "#0063b1" if is_light else "#1f6feb"
        disabled_text = "#7a7a7a" if is_light else "#6e7681"

        roles = (
            (QPalette.ColorRole.Window, bg),
            (QPalette.ColorRole.WindowText, chr_p["text"]),
            (QPalette.ColorRole.Base, bg),
            (QPalette.ColorRole.AlternateBase, chr_p["surface"]),
            (QPalette.ColorRole.Text, chr_p["text"]),
            (QPalette.ColorRole.Button, btn_bg),
            (QPalette.ColorRole.ButtonText, chr_p["text"]),
            (QPalette.ColorRole.BrightText, "#ffffff" if not is_light else "#000000"),
            (QPalette.ColorRole.Highlight, highlight),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
            (QPalette.ColorRole.ToolTipBase, chr_p["surface"]),
            (QPalette.ColorRole.ToolTipText, chr_p["text"]),
            (QPalette.ColorRole.PlaceholderText, chr_p["text_muted"]),
            (QPalette.ColorRole.Link, sem_p["info"]),
            (QPalette.ColorRole.LinkVisited, sem_p["match"]),
        )
        for role, hex_col in roles:
            pal.setColor(role, QColor(hex_col))

        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(disabled_text))
        return pal

    def _load_qss(self, path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def toggle(self) -> str:
        new_theme = "light" if self._current_theme == "dark" else "dark"
        self.apply_theme(new_theme)
        return new_theme
