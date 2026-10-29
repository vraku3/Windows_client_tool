"""One save-as-CSV dialog used by the update surfaces."""
from PyQt6.QtWidgets import QFileDialog, QMessageBox


def save_csv(parent, title: str, default_name: str, text: str) -> str:
    """Ask for a path and write `text` as UTF-8 with BOM; '' on cancel/error."""
    path, _selected = QFileDialog.getSaveFileName(
        parent, title, default_name, "CSV files (*.csv)")
    if not path:
        return ""
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)
    except OSError as exc:
        QMessageBox.warning(parent, "Export failed", f"Could not write {path}:\n{exc}")
        return ""
    return path
