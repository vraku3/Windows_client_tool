"""Discover installed games from common launchers (Qt-free, read-only)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

_PATH = re.compile(r'"path"\s+"([^"]+)"', re.I)


@dataclass(frozen=True)
class GameEntry:
    name: str
    exe: str
    source: str

    def key(self) -> str:
        return os.path.normcase(os.path.abspath(self.exe))


def _first_exe(folder: Path) -> Optional[str]:
    try:
        for child in sorted(folder.iterdir()):
            if child.is_file() and child.suffix.lower() == ".exe":
                return str(child)
        for child in sorted(folder.iterdir()):
            if child.is_dir():
                for nested in sorted(child.iterdir()):
                    if nested.is_file() and nested.suffix.lower() == ".exe":
                        return str(nested)
    except OSError:
        return None
    return None


def steam_libraries(steam_root: Path) -> List[Path]:
    libraries = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    for match in _PATH.finditer(text):
        path = Path(match.group(1))
        if path not in libraries:
            libraries.append(path)
    return libraries


def steam_games(steam_root: Path) -> List[GameEntry]:
    games = []
    for library in steam_libraries(steam_root):
        common = library / "steamapps" / "common"
        try:
            folders = sorted(child for child in common.iterdir() if child.is_dir())
        except OSError:
            continue
        for folder in folders:
            exe = _first_exe(folder)
            if exe:
                games.append(GameEntry(folder.name, exe, "Steam"))
    return games


def epic_games(manifests: Path) -> List[GameEntry]:
    games = []
    try:
        files = sorted(manifests.glob("*.item"))
    except OSError:
        return games
    for item in files:
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
            name = str(data.get("DisplayName") or "").strip()
            install = str(data.get("InstallLocation") or "").strip()
            relative = str(data.get("LaunchExecutable") or "").strip()
        except (OSError, ValueError, TypeError):
            continue
        if not name or not install:
            continue
        exe = Path(install) / relative if relative else None
        if exe is None or not exe.exists():
            exe = _first_exe(Path(install))
        if exe:
            games.append(GameEntry(name, str(exe), "Epic"))
    return games


def scan_games(steam_root: Optional[str] = None,
               epic_manifests: Optional[str] = None,
               extra_paths: Iterable[str] = ()) -> List[GameEntry]:
    games: List[GameEntry] = []
    if steam_root:
        games.extend(steam_games(Path(steam_root)))
    if epic_manifests:
        games.extend(epic_games(Path(epic_manifests)))
    for raw in extra_paths:
        path = Path(raw)
        exe = str(path) if path.is_file() else _first_exe(path) if path.is_dir() else None
        if exe:
            games.append(GameEntry(path.stem, exe, "User"))
    seen = set()
    unique = []
    for game in games:
        key = game.key().casefold()
        if key not in seen:
            seen.add(key)
            unique.append(game)
    return sorted(unique, key=lambda g: g.name.casefold())


def running_game(binding: str, processes) -> bool:
    """True when a process matches the bound exe path or file name."""
    if not binding:
        return False
    wanted_path = os.path.normcase(os.path.abspath(binding)) if os.path.sep in binding else ""
    wanted_name = os.path.basename(binding).casefold()
    for proc in processes or []:
        name = str(getattr(proc, "name", "") or "").casefold()
        path = str(getattr(proc, "exe", "") or "")
        if wanted_name and name == wanted_name:
            return True
        if wanted_path and path and os.path.normcase(os.path.abspath(path)) == wanted_path:
            return True
    return False
