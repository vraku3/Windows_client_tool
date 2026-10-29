from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

# Event name constants
LOG_ERRORS_FOUND = "log.errors_found"
AI_RECOMMENDATION_READY = "ai.recommendation_ready"
AI_RECOMMENDATION_APPLIED = "ai.recommendation_applied"
CONFIG_CHANGED = "config.changed"
MODULE_ERROR = "module.error"
NOTIFY_BALLOON = "notify.balloon"
NAV_REQUEST_MODULE = "nav.request_module"
#: data: the error text. Published (async, so it lands on the GUI thread)
#: whenever ConfigManager.save() could not write the settings.
CONFIG_SAVE_FAILED = "config.save_failed"
DEBLOAT_ITEMS_REMOVED = "debloat.items_removed"

# Typed payloads


@dataclass
class LogErrorsFoundData:
    source: str
    errors: List[dict]
    timestamp: datetime


@dataclass
class RecommendationReadyData:
    module: str
    summary: str
    details: dict


@dataclass
class ConfigChangedData:
    key: str
    old_value: Any
    new_value: Any


@dataclass
class BalloonNotifyData:
    title: str
    message: str
    icon_type: Optional[str] = None  # "info" | "warning" | "error"


@dataclass
class NavRequestData:
    module_name: str
    #: Optional: a module that understands `open_path(path)` (TreeSize) jumps
    #: straight to it instead of landing on whatever it last showed.
    path: Optional[str] = None
    #: Optional module-specific context for `receive_navigation(payload)`,
    #: e.g. Updates routing a driver offer as {"filter": "Intel Corporation"}.
    payload: Optional[dict] = None
