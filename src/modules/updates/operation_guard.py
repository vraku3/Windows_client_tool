"""Worker-thread maintenance guards, shared by GUI and headless callers."""
from contextlib import contextmanager
from functools import wraps

from core.operation_lock import OperationBusy, lock, operation_lock
from modules.game_mode.engine.journal import Journal


class GameModeActive(OperationBusy):
    def __init__(self, owner="Game Mode ON"):
        super().__init__(owner)
        self.args = ("Game Mode is active. Turn it off before installing updates. "
                     f"Operation in progress: {owner}",)


def _check_game_mode():
    if Journal().load():
        raise GameModeActive()


@contextmanager
def maintenance_operation(owner):
    try:
        with operation_lock(owner, timeout_ms=0) as acquired:
            _check_game_mode()
            yield acquired
    except OperationBusy as exc:
        if exc.owner.startswith("Game Mode") and not isinstance(exc, GameModeActive):
            raise GameModeActive(exc.owner) from exc
        raise


def maintenance_worker(owner):
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            with maintenance_operation(owner):
                return fn(*args, **kwargs)
        return run
    return decorate


def maintenance_stage(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        if lock.held and lock.owned_by_current_thread:
            _check_game_mode()
            return fn(*args, **kwargs)
        with maintenance_operation("Updates maintenance"):
            return fn(*args, **kwargs)
    return run
