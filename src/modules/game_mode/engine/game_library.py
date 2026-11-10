"""Discover installed games from common launchers (Qt-free, read-only)."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

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
        except OSError as exc:
            logger.debug("Iterating library common %s: %s", common, exc)
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
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Reading Epic item %s: %s", item, exc)
            continue
        if not name or not install:
            continue
        exe = Path(install) / relative if relative else None
        if exe is None or not exe.exists():
            exe = _first_exe(Path(install))
        if exe:
            games.append(GameEntry(name, str(exe), "Epic"))
    return games


def battlenet_games(bnet_product_db: Optional[Path] = None) -> List[GameEntry]:
    """Scan Battle.net installed games (World of Warcraft, Diablo, etc.)."""
    games: List[GameEntry] = []
    candidates: List[Path] = []
    if bnet_product_db is None:
        progdata = os.environ.get("ProgramData")
        if progdata:
            bnet_product_db = Path(progdata) / "Battle.net" / "Agent" / "product.db"
    if bnet_product_db and bnet_product_db.exists():
        try:
            raw = bnet_product_db.read_bytes()
            found = re.findall(rb"[A-Za-z]:/[^\x00\r\n\t\"]+", raw)
            for item in found:
                try:
                    p = Path(item.decode("utf-8", errors="ignore"))
                    if p.exists() and p.is_dir():
                        candidates.append(p)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Battle.net path parsing failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Reading Battle.net product.db failed: %s", exc)
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "World of Warcraft")

    wow_flavors = (
        ("_retail_", "World of Warcraft (Retail)", "Wow.exe"),
        ("_classic_", "World of Warcraft Classic", "WowClassic.exe"),
        ("_classic_era_", "World of Warcraft Classic Era", "Wow.exe"),
        ("_forever_", "World of Warcraft Classic Era", "Wow.exe"),
        ("_ptr_", "World of Warcraft (PTR)", "WowT.exe"),
        ("_beta_", "World of Warcraft (Beta)", "WowB.exe"),
    )
    seen_dirs = set()
    for cand in candidates:
        norm = os.path.normcase(os.path.abspath(cand))
        if norm in seen_dirs or not cand.exists():
            continue
        seen_dirs.add(norm)
        has_flavor = False
        for sub, label, exe_name in wow_flavors:
            exe_path = cand / sub / exe_name
            if exe_path.exists():
                games.append(GameEntry(label, str(exe_path), "Battle.net"))
                has_flavor = True
        if not has_flavor:
            try:
                for exe in sorted(cand.glob("*.exe")):
                    ename = exe.name.casefold()
                    if not any(skip in ename for skip in ("launcher", "setup", "error", "update")):
                        games.append(GameEntry(f"{cand.name} ({exe.stem})", str(exe), "Battle.net"))
            except OSError as exc:
                logger.debug("Scanning candidate %s failed: %s", cand, exc)
    return games


def scan_games(steam_root: Optional[str] = None,
               epic_manifests: Optional[str] = None,
               extra_paths: Iterable[str] = (),
               scan_battlenet: bool = True) -> List[GameEntry]:
    games: List[GameEntry] = []
    if steam_root:
        games.extend(steam_games(Path(steam_root)))
    if epic_manifests:
        games.extend(epic_games(Path(epic_manifests)))
    if scan_battlenet:
        games.extend(battlenet_games())
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
