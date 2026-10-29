"""Decisions around an active Game Mode session (Qt-free, no Windows calls)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

ABOVE_NORMAL = 0x8000
HIGH = 0x80


@dataclass
class PauseState:
    until: Optional[float] = None

    def paused(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return self.until is not None and now < self.until

    def remaining(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        return max(0, int((self.until or 0) - now))


def should_auto_off(*, started: Optional[float], now: Optional[float],
                    minutes: int, game_alive: Optional[bool] = None,
                    last_alive: Optional[float] = None,
                    grace_s: int = 300) -> tuple[bool, str]:
    """(off?, reason) for the enforced session.

    Two independent limits: the user's total-length limit, and (when a game is
    bound) a grace period after the game last ran. Both are opt-in.
    """
    now = time.time() if now is None else now
    if started is None:
        return False, ""
    if minutes and now - started >= max(1, int(minutes)) * 60:
        return True, f"session limit of {int(minutes)} minute(s) reached"
    if game_alive is False and last_alive is not None and now - last_alive >= max(0, grace_s):
        return True, "the bound game has exited"
    return False, ""


def schedule_for(now: Optional[datetime], schedules: Iterable[dict]) -> Optional[dict]:
    """The active weekly window at `now`, or None. Monday = 0."""
    now = now or datetime.now()
    weekday = now.weekday()
    clock = now.strftime("%H:%M")
    for schedule in schedules or []:
        if weekday in schedule.get("days", []) and schedule.get("start", "99:99") <= clock < schedule.get("end", ""):
            return schedule
    return None


def priority_value(mode: str) -> Optional[int]:
    return {"above": ABOVE_NORMAL, "high": HIGH}.get((mode or "").lower())
