"""Persisted Game Mode settings, profiles, protection and schedules (Qt-free)."""
from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional

SETTINGS_KEY = "modules.game_mode.settings"
SCHEMA_VERSION = 2
PROFILES_KEY = "modules.game_mode.profiles"
PROTECTED_KEY = "modules.game_mode.protected_patterns"
BINDINGS_KEY = "modules.game_mode.bindings"
SCHEDULES_KEY = "modules.game_mode.schedules"

PRIORITY_MODES = ("off", "above", "high")


@dataclass
class GameSettings:
    auto_off_minutes: int = 0             # 0 = never
    auto_off_grace_s: int = 300           # game-exit grace
    auto_on_armed: bool = False
    restore_point: bool = False
    close_grace_s: float = 8.0
    force_close: bool = True
    priority_boost: str = "off"           # off / above / high
    ac_guard: bool = True
    notifications: bool = True
    retention: int = 100
    bound_game: str = ""                  # exe path or name for auto-off/priority
    affinity_mode: str = "off"            # off / ccd0 / ccd1
    disable_power_throttling: bool = False
    priority_separation: int = 0          # 0 = leave alone, else 26/38
    system_responsiveness: int = 0        # 0 = leave alone, else 10/20
    handle_target: int = 0                 # 0 = no target
    game_io_priority: str = "high"        # off / normal / high
    lower_apps_io_priority: bool = True   # demote background apps to Very Low disk I/O
    high_resolution_timer: bool = True    # 0.5ms timer resolution
    low_latency_network: bool = True      # TcpAckFrequency=1 & TCPNoDelay=1
    max_mmcss: bool = True                # Max GPU & MMCSS Scheduling Category High + SFIO Priority High
    trim_background_memory: bool = True   # Flush inactive background working sets to maximize game RAM
    suppress_game_dvr: bool = True        # Suppress Xbox Game DVR background recording & enable DirectFlip
    prefer_discrete_gpu: bool = True      # Force High-Performance Discrete GPU for bound game

    def normalized(self) -> "GameSettings":
        data = asdict(self)
        data["auto_off_minutes"] = max(0, min(int(self.auto_off_minutes or 0), 24 * 60))
        data["auto_off_grace_s"] = max(0, min(int(self.auto_off_grace_s or 0), 3600))
        data["close_grace_s"] = max(0.0, min(float(self.close_grace_s or 0), 120.0))
        data["retention"] = max(1, min(int(self.retention or 100), 1000))
        if data["priority_boost"] not in PRIORITY_MODES:
            data["priority_boost"] = "off"
        if data["affinity_mode"] not in ("off", "ccd0", "ccd1"):
            data["affinity_mode"] = "off"
        if data["priority_separation"] not in (0, 26, 38):
            data["priority_separation"] = 0
        if data["system_responsiveness"] not in (0, 10, 20):
            data["system_responsiveness"] = 0
        data["handle_target"] = max(0, min(int(self.handle_target or 0), 1000000))
        if data.get("game_io_priority") not in ("off", "normal", "high"):
            data["game_io_priority"] = "high"
        data["lower_apps_io_priority"] = bool(data.get("lower_apps_io_priority", True))
        data["high_resolution_timer"] = bool(data.get("high_resolution_timer", True))
        data["low_latency_network"] = bool(data.get("low_latency_network", True))
        data["max_mmcss"] = bool(data.get("max_mmcss", True))
        data["trim_background_memory"] = bool(data.get("trim_background_memory", True))
        data["suppress_game_dvr"] = bool(data.get("suppress_game_dvr", True))
        data["prefer_discrete_gpu"] = bool(data.get("prefer_discrete_gpu", True))
        return GameSettings(**data)


def load_settings(config) -> GameSettings:
    raw = config.get(SETTINGS_KEY, {}) if config is not None else {}
    if not isinstance(raw, dict):
        return GameSettings()
    known = {f.name for f in fields(GameSettings)}
    try:
        settings = GameSettings(**{key: value for key, value in raw.items()
                                   if key in known}).normalized()
    except (TypeError, ValueError):
        return GameSettings()
    if int(raw.get("schema", 0) or 0) < SCHEMA_VERSION:
        # The first Session-tab build wrote unchecked control defaults before
        # loading the saved values. Restore the intended defaults once.
        settings.force_close = True
        settings.ac_guard = True
        settings.retention = 100
    return settings


def save_settings(config, settings: GameSettings) -> None:
    if config is not None:
        data = asdict(settings.normalized())
        data["schema"] = SCHEMA_VERSION
        config.set(SETTINGS_KEY, data)


def normalize_protected(values) -> List[str]:
    patterns, seen = [], set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        patterns.append(text)
    return patterns


def load_protected(config) -> List[str]:
    return normalize_protected(config.get(PROTECTED_KEY, []) if config is not None else [])


def save_protected(config, values) -> None:
    if config is not None:
        config.set(PROTECTED_KEY, normalize_protected(values))


def _matches(pattern: str, text: str) -> bool:
    pattern = pattern.casefold()
    target = text.casefold()
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(target, pattern)
    return pattern in target


def is_protected(exe: str, title: str, patterns) -> bool:
    text = f"{exe or ''} {title or ''}"
    return any(_matches(pattern, text) for pattern in (patterns or []))


def load_profiles(config) -> Dict[str, dict]:
    raw = config.get(PROFILES_KEY, {}) if config is not None else {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): dict(value) for name, value in raw.items()
            if isinstance(name, str) and name.strip() and isinstance(value, dict)}


def save_profile(config, name: str, choices: dict) -> None:
    name = (name or "").strip()
    if not name or not isinstance(choices, dict):
        raise ValueError("A profile needs a name and a choices object.")
    profiles = load_profiles(config)
    profiles[name] = dict(choices)
    config.set(PROFILES_KEY, profiles)


def delete_profile(config, name: str) -> None:
    profiles = load_profiles(config)
    profiles.pop(name, None)
    config.set(PROFILES_KEY, profiles)


def load_bindings(config) -> Dict[str, str]:
    raw = config.get(BINDINGS_KEY, {}) if config is not None else {}
    if not isinstance(raw, dict):
        return {}
    return {str(path): str(profile) for path, profile in raw.items()
            if isinstance(path, str) and isinstance(profile, str) and path.strip()}


def save_binding(config, path: str, profile: str = "") -> None:
    bindings = load_bindings(config)
    path = (path or "").strip()
    if not path:
        return
    if profile:
        bindings[path] = profile
    else:
        bindings.pop(path, None)
    config.set(BINDINGS_KEY, bindings)


def normalize_schedule(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    days = sorted({int(day) for day in raw.get("days", []) if 0 <= int(day) <= 6})
    start, end = str(raw.get("start", "")), str(raw.get("end", ""))
    profile = str(raw.get("profile", "") or "")
    if not days or len(start) != 5 or len(end) != 5 or start >= end:
        return None
    return {"days": days, "start": start, "end": end, "profile": profile}


def load_schedules(config) -> List[dict]:
    raw = config.get(SCHEDULES_KEY, []) if config is not None else []
    if not isinstance(raw, list):
        return []
    return [schedule for schedule in (normalize_schedule(item) for item in raw)
            if schedule is not None]


def save_schedules(config, schedules) -> None:
    if config is not None:
        config.set(SCHEDULES_KEY, [s for s in (normalize_schedule(x) for x in schedules)
                                   if s is not None])


def export_settings(config) -> str:
    return json.dumps({
        "version": 1,
        "settings": asdict(load_settings(config).normalized()),
        "protected": load_protected(config),
        "protected_publishers": (config.get("modules.game_mode.protected_publishers", [])
                                 if config is not None else []),
        "profiles": load_profiles(config),
        "bindings": load_bindings(config),
        "schedules": load_schedules(config),
    }, indent=2, ensure_ascii=False)


def import_settings(config, text: str) -> dict:
    try:
        payload = json.loads(text or "")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or int(payload.get("version", 1)) != 1:
        raise ValueError("Unsupported Game Mode settings file.")
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object.")
    known = {f.name for f in fields(GameSettings)}
    cleaned = GameSettings(**{key: value for key, value in settings.items()
                              if key in known}).normalized()
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict) or any(not isinstance(v, dict) for v in profiles.values()):
        raise ValueError("profiles must map names to choice objects.")
    schedules_raw = payload.get("schedules", [])
    if not isinstance(schedules_raw, list):
        raise ValueError("schedules must be a list.")
    save_settings(config, cleaned)
    save_protected(config, payload.get("protected", []))
    config.set("modules.game_mode.protected_publishers",
               [str(p) for p in payload.get("protected_publishers", []) if str(p).strip()])
    config.set(PROFILES_KEY, {str(k): dict(v) for k, v in profiles.items() if str(k).strip()})
    bindings = payload.get("bindings", {})
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be an object.")
    config.set(BINDINGS_KEY, {str(k): str(v) for k, v in bindings.items()
                              if str(k).strip() and str(v).strip()})
    save_schedules(config, schedules_raw)
    return {"settings": cleaned, "protected": load_protected(config),
            "profiles": load_profiles(config), "bindings": load_bindings(config),
            "schedules": load_schedules(config)}
