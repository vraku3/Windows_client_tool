"""Centralized logging service for Windows Client Tool."""

import datetime
import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

_D = "[0-9]"


def session_log_glob(computer_name: str) -> str:
    """Exactly `{COMPUTERNAME}_YYYY-MM-DD_HH-MM-SS.log`, nothing broader.
    The name is already sanitised to letters, digits, - and _, so it holds
    no glob metacharacters."""
    date = f"{_D * 4}-{_D * 2}-{_D * 2}"
    time_ = f"{_D * 2}-{_D * 2}-{_D * 2}"
    return f"{computer_name}_{date}_{time_}.log"


class ApplicationLogger(logging.Logger):
    """Custom logger with application context."""

    def warning(self, msg, *args, **kwargs):
        """Log WARNING level message."""
        if args:
            try:
                msg = msg % args
            except TypeError:
                msg = str(msg)
        self.log(logging.WARNING, msg, stacklevel=2)

    def error(self, msg, *args, **kwargs):
        """Log ERROR level message."""
        if args:
            try:
                msg = msg % args
            except TypeError:
                msg = str(msg)
        self.log(logging.ERROR, msg, stacklevel=2)

    def exception(self, msg, *args, exc_info: Optional[bool] = None, **kwargs):
        """Log exception traceback if exc_info is not None."""
        if exc_info is None:
            exc_info = kwargs.pop("exc_info", True)
        if args:
            try:
                msg = msg % args
            except TypeError:
                msg = str(msg)
        self.log(logging.ERROR, msg, exc_info=exc_info, stacklevel=2)

    def info(self, msg, *args, **kwargs):
        """Log INFO level message."""
        if args:
            try:
                msg = msg % args
            except TypeError:
                msg = str(msg)
        self.log(logging.INFO, msg, stacklevel=2)

    def debug(self, msg, *args, **kwargs):
        """Log DEBUG level message if DEBUG enabled."""
        if args:
            try:
                msg = msg % args
            except TypeError:
                msg = str(msg)
        self.log(logging.DEBUG, msg, stacklevel=2)



class _SafeRotatingFileHandler(RotatingFileHandler):
    """Rotation that cannot take logging down. Two app processes (the
    launcher and its elevated copy) can hold app.log open at once, and on
    Windows renaming an open file fails with a sharing violation -- after
    which the stock handler would retry the rename on every record. A failed
    rollover keeps appending to the current file and stops rotating for the
    rest of this process."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError as exc:
            self.maxBytes = 0                      # 0 = do not rotate again
            if self.stream is None:
                self.stream = self._open()
            # Said once, in the log itself: with maxBytes 0 this record does
            # not re-enter doRollover (the handler lock is re-entrant).
            logging.getLogger(__name__).warning(
                "app.log could not be rotated (%s); rotation is off until the app restarts",
                exc)

class LoggingService:
    """Configure Python logging for Windows Client Tool application.

    Provides:
    - Rotating file logs in %APPDATA%/WindowsTweaker/logs/ (persistent across runs)
    - Per-session log next to the exe named {COMPUTERNAME}_{date}_{time}.log
    - Console output when stdout is available
    """

    LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    MAX_BYTES = 5 * 1024 * 1024  # 5MB rotation
    BACKUP_COUNT = 5

    def __init__(
        self,
        log_dir: Optional[str] = None,
        log_level: str = "INFO",
        console_level: str = "WARNING",
        max_bytes: Optional[int] = None,
        backup_count: int = 5,
    ):
        self._log_dir = log_dir or ""
        self._log_level = getattr(logging, log_level.upper(), logging.INFO)
        self._console_level = getattr(logging, console_level.upper(), logging.WARNING)
        # None = the class default. It used to default to -1, which
        # RotatingFileHandler reads as "never rotate": app.log grew forever.
        self._max_bytes = self.MAX_BYTES if max_bytes is None else max_bytes
        self._backup_count = backup_count
        self._handlers: list[logging.Handler] = []
        self.session_log_dir: str = ""
        self.session_log_glob: str = ""

    def setup(self) -> None:
        """Configure logging with rotation and dual output."""
        # Root logger must pass everything — individual handlers filter by level.
        logging.getLogger().setLevel(logging.DEBUG)

        formatter = logging.Formatter(self.LOG_FORMAT, datefmt=self.DATE_FORMAT)

        if self._log_dir:
            try:
                os.makedirs(self._log_dir, exist_ok=True)
                log_file = os.path.join(self._log_dir, "app.log")
                file_handler = _SafeRotatingFileHandler(
                    log_file,
                    maxBytes=self._max_bytes,
                    backupCount=self._backup_count,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(self._log_level)
                logging.getLogger().addHandler(file_handler)
                self._handlers.append(file_handler)
            except Exception as e:
                print(f"Could not create log file handler: {e}", file=sys.stderr)

        # Console handler — skip when stdout is unavailable (frozen windowed mode)
        if hasattr(sys, "stdout") and sys.stdout is not None and hasattr(sys.stdout, "write"):
            try:
                # A default Windows console is cp1252, and these messages
                # carry em-dashes and arrows. Observed live:
                #     Module 'Tweaks' requires admin ? disabled
                # main.py's _s() already works around this for its own
                # prints; the handler needs it too. errors="replace" rather
                # than strict, because losing a whole log line to a
                # UnicodeEncodeError is worse than one replacement glyph.
                try:
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                except (AttributeError, ValueError, OSError):
                    # Not a real text stream (pytest capture, a pipe wrapper,
                    # a frozen build's stub). Nothing to reconfigure, and
                    # nothing the user can do about it.
                    logging.getLogger(__name__).debug(
                        "stdout could not be reconfigured to UTF-8; console "
                        "output may mangle non-ASCII", exc_info=True)
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(formatter)
                console_handler.setLevel(self._console_level)
                logging.getLogger().addHandler(console_handler)
                self._handlers.append(console_handler)
            except Exception as e:
                print(f"Could not create console handler: {e}", file=sys.stderr)

    def _session_fallback_dir(self) -> str:
        """App-data logs directory, used when the exe dir is unwritable or
        when running from source (never drop logs into the project root)."""
        if self._log_dir:
            return self._log_dir
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        directory = os.path.join(base, "WindowsTweaker", "logs")
        os.makedirs(directory, exist_ok=True)
        return directory

    def setup_session_log(self) -> None:
        """Add a per-session log file.

        The file is named {COMPUTERNAME}_{YYYY-MM-DD}_{HH-MM-SS}.log and placed
        next to the running exe so logs from several machines can be collected
        from one shared folder. When that folder is not writable (Program
        Files), or when running from source, it goes to the app-data logs dir
        instead — never into the working directory.
        """
        if getattr(sys, "frozen", False):
            out_dir = os.path.dirname(os.path.abspath(sys.executable))
            probe = os.path.join(out_dir, f".wct_write_probe_{os.getpid()}")
            try:
                with open(probe, "w", encoding="utf-8"):
                    pass
                os.remove(probe)
            except OSError:
                out_dir = self._session_fallback_dir()
        else:
            out_dir = self._session_fallback_dir()
        self.session_log_dir = out_dir

        raw_name = os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown"
        computer_name = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in raw_name
        )
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = os.path.join(out_dir, f"{computer_name}_{timestamp}.log")
        # Retention must match ONLY these files: the folder is wherever the
        # portable exe sits (a shared Apps folder), and "*.log" there deleted
        # other programs' logs.
        self.session_log_glob = session_log_glob(computer_name)

        formatter = logging.Formatter(self.LOG_FORMAT, datefmt=self.DATE_FORMAT)
        try:
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(formatter)
            handler.setLevel(self._log_level)
            logging.getLogger().addHandler(handler)
            self._handlers.append(handler)
            logging.getLogger(__name__).info(
                "Session log started — computer: %s, file: %s", computer_name, log_path
            )
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Could not create session log at %s: %s", log_path, e
            )

    def set_level(self, level: str) -> None:
        """Update logging level for all file handlers."""
        level_int = getattr(logging, level.upper(), logging.INFO)
        for handler in self._handlers:
            if isinstance(handler, (RotatingFileHandler, logging.FileHandler)):
                handler.setLevel(level_int)

    def shutdown(self) -> None:
        """Flush and close all handlers before application shutdown."""
        for handler in self._handlers:
            try:
                handler.flush()
                handler.close()
                logging.getLogger().removeHandler(handler)
            except Exception:
                logger = logging.getLogger(__name__)
                logger.warning("Error shutting down logging handler %s", handler, exc_info=True)
        self._handlers.clear()
        self._log_dir = ""
