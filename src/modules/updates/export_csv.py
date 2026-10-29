"""CSV export of the update offers an operator is looking at (Qt-free)."""
from __future__ import annotations

import csv
import io
from typing import Iterable

from modules.updates import wu_categories

APP_HEADERS = ["Name", "ID", "Installed", "Available", "Source"]
WU_HEADERS = ["KB", "Title", "Classification", "Category", "Severity", "Restart",
              "Released", "MaxDownloadMB", "MinDownloadMB", "Downloaded", "Driver",
              "SecurityBulletins", "CveIds", "SupportURL"]


def app_offers_to_csv(updates: Iterable) -> str:
    """Every app offer with its installed and available version."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(APP_HEADERS)
    for update in updates:
        writer.writerow([
            getattr(update, "name", ""),
            getattr(update, "winget_id", ""),
            getattr(update, "installed_version", ""),
            getattr(update, "available_version", ""),
            getattr(update, "source", ""),
        ])
    return output.getvalue()


def wu_offers_to_csv(updates: Iterable) -> str:
    """Every Windows Update offer, including held/driver detail and identifiers."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(WU_HEADERS)
    for update in updates:
        driver = bool(getattr(update, "driver", False)
                      or wu_categories.category_of(update) == wu_categories.DRIVER)
        writer.writerow([
            getattr(update, "kb", ""),
            getattr(update, "title", ""),
            getattr(update, "classification", ""),
            wu_categories.category_of(update),
            getattr(update, "severity", ""),
            getattr(update, "reboot", ""),
            getattr(update, "release_date", ""),
            _number(getattr(update, "size_mb", 0)),
            _number(getattr(update, "min_size_mb", 0)),
            bool(getattr(update, "downloaded", False)),
            driver,
            "; ".join(str(v) for v in (getattr(update, "security_bulletins", None) or [])),
            "; ".join(str(v) for v in (getattr(update, "cve_ids", None) or [])),
            getattr(update, "support_url", ""),
        ])
    return output.getvalue()


def _number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return "" if number <= 0 else f"{number:.1f}"
