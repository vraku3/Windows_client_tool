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
