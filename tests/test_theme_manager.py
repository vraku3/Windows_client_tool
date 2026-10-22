from core.theme_manager import ThemeManager


import pytest

# App-wide restyles must not repolish windows earlier modules left (conftest).
pytestmark = pytest.mark.usefixtures("fresh_widgets")

def test_apply_valid_theme(tmp_path):
    styles_dir = str(tmp_path)
    (tmp_path / "dark.qss").write_text("QMainWindow { background: #1e1e1e; }")
    (tmp_path / "light.qss").write_text("QMainWindow { background: #f5f5f5; }")
    tm = ThemeManager(styles_dir=styles_dir)
    tm.apply_theme("dark")
    assert tm.current_theme == "dark"
    tm.apply_theme("light")
    assert tm.current_theme == "light"


def test_apply_invalid_theme_falls_back_to_dark(tmp_path):
    styles_dir = str(tmp_path)
    (tmp_path / "dark.qss").write_text("QMainWindow { background: #1e1e1e; }")
    tm = ThemeManager(styles_dir=styles_dir)
    tm.apply_theme("neon")
    assert tm.current_theme == "dark"


def test_toggle(tmp_path):
    styles_dir = str(tmp_path)
    (tmp_path / "dark.qss").write_text("body {}")
    (tmp_path / "light.qss").write_text("body {}")
    tm = ThemeManager(styles_dir=styles_dir)
    assert tm.current_theme == "dark"
    result = tm.toggle()
    assert result == "light"
    assert tm.current_theme == "light"
    result = tm.toggle()
    assert result == "dark"


def test_missing_qss_file_does_not_crash(tmp_path):
    styles_dir = str(tmp_path)
    tm = ThemeManager(styles_dir=styles_dir)
    tm.apply_theme("dark")  # File does not exist — should not crash
    # Theme stays at default since load failed
    assert tm.current_theme == "dark"
