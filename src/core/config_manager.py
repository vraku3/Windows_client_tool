import copy
import json
import logging
import os
import shutil
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

CURRENT_VERSION = 1


class ConfigManager:
    AUTOSAVE_DELAY_MS = 2000

    def __init__(self, config_dir: str, defaults: dict, event_bus=None):
        self._config_dir = config_dir
        self._defaults = defaults
        self._data: dict = {}
        self._event_bus = event_bus
        self._config_path = os.path.join(config_dir, "config.json")
        self._backup_path = os.path.join(config_dir, "config.json.bak")
        #: Where a save whose final rename kept being refused leaves the
        #: settings it could not put in place -- the newest copy there is,
        #: read by load() when config.json and its backup are both missing.
        self._unsaved_path = os.path.join(config_dir, "config.json.unsaved")
        #: The last save failure, or "" -- the window shows it.
        self.last_save_error = ""
        self._migrations: List[Tuple[int, Callable[[dict], dict]]] = []
        self._autosave_timer = None
        # set() runs on worker threads too (Game Mode, Thermal); without one
        # lock a save could serialise a dict being changed, and two saves
        # shared one .tmp file -- renaming it while the other still had it
        # open is "Access is denied" (2026-10-19).
        self._lock = threading.RLock()

    def _get_defaults(self) -> dict:
        if callable(self._defaults):
            return copy.deepcopy(self._defaults())
        return copy.deepcopy(self._defaults)

    def _ensure_autosave_timer(self):
        if self._autosave_timer is None:
            try:
                from PyQt6.QtCore import QTimer
                self._autosave_timer = QTimer()
                self._autosave_timer.setSingleShot(True)
                self._autosave_timer.setInterval(self.AUTOSAVE_DELAY_MS)
                self._autosave_timer.timeout.connect(self.save)
            except ImportError:
                logger.debug("Ignored ImportError", exc_info=True)

    def register_migration(self, from_version: int, fn: Callable[[dict], dict]) -> None:
        self._migrations.append((from_version, fn))
        self._migrations.sort(key=lambda x: x[0])

    def load(self) -> None:
        os.makedirs(self._config_dir, exist_ok=True)
        loaded = self._try_load(self._config_path)
        if loaded is None:
            logger.warning("Config file corrupt or missing, trying backup")
            loaded = self._try_load(self._backup_path)
        if loaded is None:
            loaded = self._try_load(self._unsaved_path)
            if loaded is not None:
                logger.warning("config.json and its backup are missing; loaded the last settings "
                               "that could not be saved, from %s", self._unsaved_path)
        if loaded is None:
            logger.warning("No valid config found, using defaults")
            loaded = self._get_defaults()
        self._data = self._run_migrations(loaded)

    def _run_migrations(self, data: dict) -> dict:
        version = data.get("version", 1)
        for from_ver, migrate_fn in self._migrations:
            if version == from_ver:
                logger.info("Migrating config from v%d to v%d", from_ver, from_ver + 1)
                data = migrate_fn(data)
                data["version"] = from_ver + 1
                version = from_ver + 1
        return data

    def _try_load(self, path: str) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def get(self, key: str, default=None) -> Any:
        keys = key.split(".")
        node = self._data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def set(self, key: str, value: Any) -> None:
        keys = key.split(".")
        with self._lock:
            node = self._data
            for k in keys[:-1]:
                if k not in node or not isinstance(node[k], dict):
                    node[k] = {}
                node = node[k]
            old_value = node.get(keys[-1])
            node[keys[-1]] = value
        if self._event_bus and old_value != value:
            from core.events import CONFIG_CHANGED, ConfigChangedData
            self._event_bus.publish(CONFIG_CHANGED, ConfigChangedData(key=key, old_value=old_value, new_value=value))
        self._ensure_autosave_timer()
        if self._autosave_timer is not None:
            self._start_autosave()

    def _start_autosave(self) -> None:
        """A QTimer can only be started from its own thread; a set() from a
        worker queues the start onto it instead."""
        try:
            from PyQt6.QtCore import QMetaObject, QThread, Qt
            if QThread.currentThread() is self._autosave_timer.thread():
                self._autosave_timer.start()
            else:
                QMetaObject.invokeMethod(self._autosave_timer, "start",
                                         Qt.ConnectionType.QueuedConnection)
        except (ImportError, RuntimeError) as e:
            logger.warning("autosave timer not started (changes are saved at exit): %s", e)

    def get_module_config(self, module_name: str) -> dict:
        return self.get(f"modules.{module_name}", {})

    def save(self) -> bool:
        """Write the config atomically. False (logged, never raised) when it
        could not be written: losing one autosave must not crash the app."""
        with self._lock:
            try:
                os.makedirs(self._config_dir, exist_ok=True)
                if os.path.exists(self._config_path):
                    shutil.copy2(self._config_path, self._backup_path)
                # A temp name of its own per save, never a shared .tmp.
                tmp_path = (f"{self._config_path}.{os.getpid()}."
                            f"{threading.get_ident()}.tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                self._replace_with_retry(tmp_path, self._config_path, self._unsaved_path)
                if self.last_save_error:
                    logger.info("config saved again after an earlier failure")
                self.last_save_error = ""
                return True
            except (OSError, TypeError, ValueError) as e:
                logger.error("config could not be saved to %s: %s", self._config_path, e)
                self.last_save_error = str(e)
                if self._event_bus is not None:
                    from core.events import CONFIG_SAVE_FAILED
                    self._event_bus.publish_async(CONFIG_SAVE_FAILED, str(e))
                return False

    @staticmethod
    def _replace_with_retry(src: str, dst: str, unsaved: str) -> None:
        """os.replace, retried briefly: an antivirus or indexer scanning the
        just-written file holds it for a moment and the rename is refused.
        When it never succeeds, the written settings are KEPT as `unsaved`
        (the newest copy there is) rather than deleted, and the error raised."""
        for attempt in range(5):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == 4:
                    try:
                        os.replace(src, unsaved)
                        logger.error("config.json could not be replaced; the settings are kept in %s",
                                     unsaved)
                    except OSError as e:
                        logger.error("config.json could not be replaced and the settings could not "
                                     "be kept in %s either (%s); they are in %s", unsaved, e, src)
                    raise
                time.sleep(0.1 * (attempt + 1))

    def reset_to_defaults(self) -> None:
        self._data = self._get_defaults()
        self.save()
