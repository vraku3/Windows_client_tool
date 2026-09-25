import logging
import os
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core import phosphor
from core.semantic_colors import set_theme as _set_semantic_theme
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
            app = QApplication.instance()
            if app:
                app.setStyleSheet(stylesheet)
            self._current_theme = theme
            # Colours applied from Python cannot come from the sheet; keep the
            # semantic palette in step before anyone repaints with it.
            _set_semantic_theme(theme)
            logger.info("Applied theme: %s", theme)
            self.theme_changed.emit(theme)
        else:
            logger.error("Failed to load theme '%s' from %s", theme, qss_path)

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
