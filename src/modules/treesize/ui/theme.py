"""Theme sheets for the TreeSize pane (spec 5.3).

Two sheets applied by the module to its own root widget. Because a widget's
stylesheet applies to that widget and its descendants, this scopes to the pane
automatically: ThemeManager needs no change and the host app's sheets continue
to govern every other module.

Sheet selection follows the Windows `AppsUseLightTheme` setting.

Spec 5.3 says colours are sampled from the running product rather than guessed.
These are taken from the dark screenshots referenced in spec 1.1. Where a value
could not be read off a screenshot it follows the neighbouring sampled tone
rather than a Qt default, and is marked below.
"""
import winreg

DARK = {
    "bg": "#1F1F1F",
    "panel": "#252526",
    "ribbon": "#2D2D30",
    "border": "#3F3F46",
    "text": "#F1F1F1",
    "muted": "#9D9D9D",
    "accent": "#3D8BD4",
    "accent_hover": "#4A9BE8",
    "selection": "#094771",
    "bar_track": "#2A2D2E",
    "warning": "#E0A030",
    # A wash of the warning hue, not a slab of it: the banner is an offer
    # sitting above the pane, and it must not outshout the scan itself.
    "banner_bg": "#33291A",
}

LIGHT = {
    "bg": "#FFFFFF",
    "panel": "#F5F5F5",
    "ribbon": "#F0F0F0",
    "border": "#D4D4D4",
    "text": "#1E1E1E",
    "muted": "#606060",
    "accent": "#2A72B5",
    "accent_hover": "#3D8BD4",
    "selection": "#CCE4F7",
    "bar_track": "#E6E6E6",
    "warning": "#A66300",
    "banner_bg": "#FFF4E0",
}

_TEMPLATE = """
QWidget {{ background: {bg}; color: {text}; }}

#ribbonTabBar {{ background: {ribbon}; }}
#ribbonTabBar::tab {{
    background: transparent; color: {text};
    padding: 5px 14px; border: none; margin-right: 2px;
}}
#ribbonTabBar::tab:selected {{
    background: {panel}; border-top: 2px solid {accent};
}}
#ribbonTabBar::tab:hover:!selected {{ background: {border}; }}

#ribbonPages {{ background: {panel}; border-bottom: 1px solid {border}; }}
#ribbonGroup {{ border: none; }}
#ribbonGroupCaption {{ color: {muted}; font-size: 10px; }}
#ribbonSeparator {{ color: {border}; max-width: 1px; margin: 2px 4px; }}
#ribbonLargeButton {{
    padding: 4px 8px; border: 1px solid transparent; border-radius: 3px;
    min-width: 56px;
}}
#ribbonSmallButton {{
    padding: 2px 6px; border: 1px solid transparent; border-radius: 3px;
    text-align: left;
}}
#ribbonLargeButton:hover, #ribbonSmallButton:hover {{
    background: {selection}; border: 1px solid {accent};
}}
#ribbonLargeButton:checked, #ribbonSmallButton:checked {{
    background: {selection}; border: 1px solid {accent};
}}
#ribbonLargeButton:disabled, #ribbonSmallButton:disabled {{ color: {muted}; }}

#navButton {{
    background: {panel}; border: 1px solid {border};
    border-radius: 2px; padding: 2px;
}}
#navButton:hover {{ background: {selection}; }}
#scanState {{ color: {accent}; padding-left: 8px; }}
#scanOverviewField {{ color: {muted}; }}
#statusNotice {{ color: {warning}; }}

QComboBox {{
    background: {bg}; border: 1px solid {border};
    border-radius: 2px; padding: 3px 6px; color: {text};
}}
QComboBox:focus {{ border: 1px solid {accent}; }}
QComboBox QAbstractItemView {{
    background: {panel}; color: {text}; border: 1px solid {border};
    selection-background-color: {selection};
}}

QLineEdit {{
    background: {bg}; color: {text}; border: 1px solid {border};
    border-radius: 2px; padding: 3px 6px;
}}
QLineEdit:focus {{ border: 1px solid {accent}; }}

QSpinBox {{
    background: {bg}; color: {text}; border: 1px solid {border};
    border-radius: 2px; padding: 2px 4px; color: {text};
}}
QSpinBox:focus {{ border: 1px solid {accent}; }}

QTreeView, QTreeWidget, QTableView, QTableWidget {{
    background: {bg}; alternate-background-color: {panel};
    border: 1px solid {border}; outline: none; color: {text};
}}
QTreeView::item, QTreeWidget::item, QTableView::item, QTableWidget::item {{
    padding: 2px; border: none;
}}
QTreeView::item:selected, QTreeWidget::item:selected, QTableView::item:selected, QTableWidget::item:selected {{
    background: {selection}; color: {text};
}}
QTreeView::item:hover, QTreeWidget::item:hover, QTableView::item:hover, QTableWidget::item:hover {{
    background: {bar_track};
}}

QHeaderView::section {{
    background: {panel}; color: {muted};
    padding: 4px 6px; border: none; border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}

QTabWidget::pane {{ border: 1px solid {border}; }}
QTabBar::tab {{
    background: {panel}; color: {muted};
    padding: 4px 12px; border: 1px solid {border}; border-bottom: none;
}}
QTabBar::tab:selected {{ background: {bg}; color: {text}; }}

QSplitter::handle {{ background: {border}; }}

QScrollBar:vertical {{ background: {bg}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {border}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; width: 0; }}

QScrollBar:horizontal {{ background: {bg}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {border}; border-radius: 5px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {muted}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ height: 0; width: 0; }}

QCheckBox, QRadioButton {{ color: {text}; spacing: 6px; }}
QGroupBox {{
    color: {text}; border: 1px solid {border}; border-radius: 4px;
    margin-top: 8px; padding-top: 10px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 0 4px; left: 8px;
}}

QPushButton {{
    background: {panel}; color: {text}; border: 1px solid {border};
    border-radius: 3px; padding: 4px 12px;
}}
QPushButton:hover {{ background: {selection}; border-color: {accent}; }}
QPushButton:disabled {{ color: {muted}; border-color: {border}; }}

QMenu {{
    background: {panel}; color: {text}; border: 1px solid {border};
    padding: 4px;
}}
QMenu::item {{ padding: 4px 20px 4px 24px; border-radius: 2px; color: {text}; }}
QMenu::item:selected {{ background: {selection}; color: {text}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}

QToolTip {{
    background: {panel}; color: {text}; border: 1px solid {border};
    padding: 4px 8px; border-radius: 2px;
}}

#titleRow {{ background: {ribbon}; border-bottom: 1px solid {border}; }}
#paneTitle {{ color: {text}; font-weight: 600; }}
#qatButton {{
    color: {muted}; padding: 1px 6px; border: 1px solid transparent;
    border-radius: 2px; font-size: 13px;
}}
#qatButton:hover {{ background: {selection}; color: {text}; }}
#findOption {{
    background: {bg}; color: {text}; border: 1px solid {border};
    border-radius: 2px; padding: 2px 6px;
}}
#findOption:focus {{ border: 1px solid {accent}; }}
#findResults {{
    background: {panel}; border: 1px solid {accent}; color: {text};
}}
#findResults::item {{ padding: 3px 8px; }}
#findResults::item:selected {{ background: {selection}; }}

#backstage {{ background: {bg}; }}
#backstageRail {{ background: {ribbon}; border-right: 1px solid {border}; }}
#backstageBack, #backstageEntry {{
    background: transparent; color: {text}; border: none;
    text-align: left; padding: 7px 10px; border-radius: 2px;
}}
#backstageBack:hover, #backstageEntry:hover {{ background: {selection}; }}
#backstageHeading {{ color: {text}; font-size: 17px; font-weight: 600; }}
#recentList {{ background: {bg}; border: none; color: {text}; }}
#recentList::item {{ padding: 5px 4px; }}
#recentList::item:selected {{ background: {selection}; }}
#chartModeButton {{
    color: {muted}; padding: 2px 10px; border: 1px solid transparent;
    border-radius: 2px;
}}
#chartModeButton:checked {{
    background: {selection}; color: {text}; border: 1px solid {accent};
}}
#chartModeButton:hover {{ color: {text}; }}
#optionsNote {{ color: {muted}; }}
#breadcrumb {{
    background: transparent; color: {accent}; border: none; padding: 1px 4px;
}}
#breadcrumb:hover {{ text-decoration: underline; }}
/* The elevation offer. Warning-tinted rather than error-red: nothing is
   broken, the module works unelevated -- it is only slower. */
#elevationBanner {{
    background: {banner_bg}; border-bottom: 1px solid {warning};
}}
#elevationBannerText {{ color: {warning}; padding-left: 2px; }}
#elevationBannerButton {{
    background: {panel}; color: {text}; border: 1px solid {warning};
    padding: 2px 10px; border-radius: 2px;
}}
#elevationBannerButton:hover {{ background: {selection}; }}
#elevationBannerClose {{
    background: transparent; color: {muted}; border: none; font-size: 14px;
}}
#elevationBannerClose:hover {{ color: {text}; }}
"""


def windows_prefers_light() -> bool:
    """Read AppsUseLightTheme. Defaults to False (dark mode)."""
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return False


def is_light_theme(theme: str | bool | None = None, light: bool | None = None) -> bool:
    """Determine whether to use the light theme.

    Defaults to DARK (False), matching the app's default dark theme.
    If 'system', follows Windows AppsUseLightTheme.
    If 'light' or True, returns True.
    If 'dark' or False or None, returns False.
    """
    if light is not None:
        return bool(light)
    if theme is None or theme == "dark" or theme is False:
        return False
    if theme == "light" or theme is True:
        return True
    if theme == "system":
        return windows_prefers_light()
    return False


def stylesheet(theme: str | bool | None = None, light: bool | None = None) -> str:
    use_light = is_light_theme(theme, light=light)
    return _TEMPLATE.format(**(LIGHT if use_light else DARK))


def palette(theme: str | bool | None = None, light: bool | None = None) -> dict:
    use_light = is_light_theme(theme, light=light)
    return LIGHT if use_light else DARK


def apply_theme(widget, theme: str | bool | None = None, light: bool | None = None) -> None:
    colors = palette(theme, light=light)
    widget.setStyleSheet(stylesheet(theme, light=light))
    _repaint_bars(widget, colors)
    _repaint_custom_views(widget, colors)


def _repaint_bars(widget, colors: dict) -> None:
    """Push the sheet's bar colors into every proportional-bar delegate.

    A delegate paints outside the stylesheet, so a sheet alone cannot reach
    it -- which is why a light pane used to paint its bars on the dark
    theme's near-black track. Delegates are QObject children of their views,
    so findChildren reaches every one of them: the tree, Details, the tables
    and the drive list, without theme.py having to know they exist.
    """
    from PyQt6.QtGui import QColor

    from .directory_tree import ProportionBarDelegate

    fill, track = QColor(colors["accent"]), QColor(colors["bar_track"])
    for delegate in widget.findChildren(ProportionBarDelegate):
        delegate.set_bar_colors(fill, track)


def _repaint_custom_views(widget, colors: dict) -> None:
    """Notify custom painting widgets (Treemap, ChartView, Sparkline) of theme colors."""
    from PyQt6.QtWidgets import QWidget
    for child in widget.findChildren(QWidget):
        if hasattr(child, "set_theme_colors") and callable(child.set_theme_colors):
            try:
                child.set_theme_colors(colors)
            except Exception:
                pass
