# -*- coding: utf-8 -*-
"""
Trash Manager for Cleanup Module

Handles:
- 30-day retention
- File restoration
- Auto-empty
- Quarantine deleted items
- Restore from trash
"""

import os
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

from modules.cleanup.cleanup_config import CleanupConfig
from modules.cleanup.cleanup_scanner.disk_size import on_disk_size
import logging
logger = logging.getLogger(__name__)


@dataclass
class TrashItem:
    """Represents a file in trash."""

    original_path: str
    target_path: str
    original_name: str
    deleted_date: datetime
    size_bytes: int


class TrashManager:
    """Manages trash/undo functionality for cleanup operations."""

    TRASH_DIR: Path = None

    def __init__(self, config_manager: CleanupConfig):
        self._config = config_manager
        self._trash_dir: Path = None
        self._setup_trash_dir()

    def _setup_trash_dir(self):
        """Set up trash directory."""
        if TrashManager.TRASH_DIR is None:
            self._trash_dir = Path(
                self._config.get_config()
                .get("user_cleanup", {})
                .get("temp_files", "%TEMP%")
            )
            Path(self._trash_dir).parent.mkdir(parents=True, exist_ok=True)

    def delete_to_trash(
        self, items: List[str], source_folder: Optional[str] = None
    ) -> List[str]:
        """Move files to trash instead of deleting.

        Returns:
            List of paths in trash (for restoration).
        """
        trash_paths = []
        for item_path in items:
            target_path = self._trash_dir / Path(item_path).name
            try:
                if os.path.exists(item_path):
                    if os.path.isdir(item_path):
                        shutil.move(item_path, str(target_path))
                    else:
                        shutil.move(item_path, str(target_path))
                    trash_paths.append(str(target_path))
            except Exception:
                logger.warning("could not move %s to the trash", item_path, exc_info=True)
                # Move to permanent delete if trash failed
                try:
                    if os.path.exists(item_path):
                        os.unlink(item_path)
                except Exception:
                    logger.warning("Ignored Exception", exc_info=True)
        return [str(p) for p in trash_paths]

    def get_trash_items(self) -> List[TrashItem]:
        """Get list of all items in trash."""
        items = []
        retention_days = self._config.get_trash_retention_days()
        cutoff = datetime.now() - timedelta(days=retention_days)

        for trash_item_path in self._trash_dir.rglob("*"):
            if trash_item_path.is_file():
                try:
                    stat = trash_item_path.stat()
                    modified = datetime.fromtimestamp(stat.st_mtime)

                    if modified < cutoff:
                        continue

                    stat = trash_item_path.stat()
                    items.append(
                        TrashItem(
                            original_path=str(trash_item_path),
                            target_path=str(trash_item_path),
                            original_name=trash_item_path.name,
                            deleted_date=modified,
                            size_bytes=on_disk_size(str(trash_item_path), stat),
                        )
                    )
                except Exception:
                    logger.warning("could not read the trash item %s", trash_item_path, exc_info=True)

        return sorted(items, key=lambda x: x.deleted_date)

    def restore(self, item_path: str, original_path: str = None) -> bool:
        """Restore item from trash to original location."""
        try:
            if not os.path.exists(item_path):
                return False

            dest_dir = (
                Path(original_path).parent if original_path else Path(item_path).parent
            )
            dest_path = dest_dir / Path(item_path).name

            if dest_path.exists():
                if self._config.get_config().get("overwrite_existing"):
                    shutil.rmtree(dest_path)
                return False

            shutil.move(str(item_path), str(dest_path))
            return True
        except Exception:
            logger.warning("could not restore %s from the trash", item_path, exc_info=True)
            return False

    def restore_all(self, original_location: str) -> int:
        """Restore all items to single location."""
        restored = 0
        for item in self.get_trash_items():
            if self.restore(str(item), original_location):
                restored += 1
        return restored

    def empty_trash(self) -> tuple:
        """Empty trash within retention period.

        Returns:
            Tuple of (restored_count: int, error_count: int)
        """
        items = self.get_trash_items()
        restored = 0

        for item in items:
            try:
                os.remove(item.target_path)
                restored += 1
            except Exception:
                logger.warning("could not delete the trash item", exc_info=True)

        return (restored, 0)
