# core/utils/logger.py
from __future__ import annotations
import logging
import sys
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------- single global logger ----------
logger_uma = logging.getLogger("uma")
logger_uma.propagate = False  # don't bubble to root

# We'll create handlers lazily & idempotently inside setup_uma_logging()
_console: Optional[logging.Handler] = None
_file_handler: Optional[logging.Handler] = None
_file_handler_ts: Optional[logging.Handler] = None  # timestamped file handler
_run_file_handler: Optional[logging.Handler] = None
_run_log_path: Optional[str] = None

_FILE_FMT = (
    "%(asctime)s %(levelname)-7s %(pathname)s:%(lineno)d %(funcName)s(): %(message)s"
)


def _has_console_handler(logger: logging.Logger) -> bool:
    return any(
        isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) in (sys.stdout, sys.stderr)
        for h in logger.handlers
    )


def _remove_console_handlers(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (
            sys.stdout,
            sys.stderr,
        ):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception as e:
                print(f"Error while setting logger: {e}")


def _remove_handler(logger: logging.Logger, handler: Optional[logging.Handler]) -> None:
    if handler is None:
        return
    if handler in logger.handlers:
        logger.removeHandler(handler)
    try:
        handler.close()
    except Exception as e:
        print(f"Error while setting logger: {e}")


def _sanitize_log_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return cleaned.strip("._-") or "run"


def setup_uma_logging(
    debug: bool,
    debug_dir: str = "debug",
    *,
    show_func: bool = False,
    timestamped: bool = True,
) -> None:
    """
    Configure the 'uma' logger (idempotent for notebooks):
    - debug=True  -> console DEBUG; also write debug/debug.log with full paths
    - debug=False -> console ERROR only
    - show_func   -> include function name after the path
    """
    global _console, _file_handler, _file_handler_ts

    # ---- Console handler (filename only) ----
    if _has_console_handler(logger_uma):
        # avoid duplicates when re-running cells
        _remove_console_handlers(logger_uma)

    _console = logging.StreamHandler(sys.stdout)
    func_field = " %(funcName)s()" if show_func else ""
    # ONLY filename + line number on console
    console_fmt = (
        f"%(asctime)s %(levelname)-7s %(filename)s:%(lineno)d{func_field}: %(message)s"
    )
    _console.setFormatter(logging.Formatter(console_fmt, "%H:%M:%S"))
    logger_uma.addHandler(_console)

    # ---- Levels ----
    logger_uma.setLevel(logging.DEBUG)
    if debug:
        _console.setLevel(logging.DEBUG)
    else:
        _console.setLevel(logging.ERROR)

    # ---- File handler (full path) ----
    if debug:
        os.makedirs(debug_dir, exist_ok=True)
        if _file_handler is None:
            _file_handler = logging.FileHandler(
                os.path.join(debug_dir, "debug.log"), encoding="utf-8"
            )
            _file_handler.setLevel(logging.DEBUG)
            _file_handler.setFormatter(logging.Formatter(_FILE_FMT, "%H:%M:%S"))
            logger_uma.addHandler(_file_handler)
        # Optional per-run timestamped file (idempotent in notebooks)
        if timestamped and _file_handler_ts is None:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
            ts_path = os.path.join(debug_dir, f"debug_{ts}.log")
            _file_handler_ts = logging.FileHandler(ts_path, encoding="utf-8")
            _file_handler_ts.setLevel(logging.DEBUG)
            _file_handler_ts.setFormatter(logging.Formatter(_FILE_FMT, "%H:%M:%S"))
            logger_uma.addHandler(_file_handler_ts)
    else:
        if _file_handler is not None:
            _remove_handler(logger_uma, _file_handler)
            _file_handler = None
        if _file_handler_ts is not None:
            _remove_handler(logger_uma, _file_handler_ts)
            _file_handler_ts = None


def start_run_logging(
    *,
    debug_dir: str = "debug",
    run_kind: str = "bot",
    context: Optional[str] = None,
) -> str:
    global _run_file_handler, _run_log_path

    stop_run_logging()

    run_root = Path(debug_dir) / "runs" / datetime.now().strftime("%Y-%m-%d")
    run_root.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    parts = [_sanitize_log_component(run_kind)]
    if context:
        parts.append(_sanitize_log_component(context))

    filename = f"{ts}_{'_'.join(parts)}.log"
    run_path = run_root / filename

    _run_file_handler = logging.FileHandler(run_path, encoding="utf-8")
    _run_file_handler.setLevel(logging.DEBUG)
    _run_file_handler.setFormatter(logging.Formatter(_FILE_FMT, "%H:%M:%S"))
    logger_uma.addHandler(_run_file_handler)

    _run_log_path = str(run_path)
    logger_uma.info("[LOG] Run log file: %s", _run_log_path)
    return _run_log_path


def stop_run_logging() -> Optional[str]:
    global _run_file_handler, _run_log_path

    last_path = _run_log_path
    if _run_file_handler is not None:
        _remove_handler(logger_uma, _run_file_handler)
        _run_file_handler = None
    _run_log_path = None
    return last_path


def get_current_run_log_path() -> Optional[str]:
    return _run_log_path


def get_logger(name=None) -> logging.Logger:
    """Return the global logger or a child (uma.<name>) that shares handlers/levels."""
    if not name or name == "uma":
        return logger_uma
    child = logger_uma.getChild(str(name))
    # children propagate to parent; we don't attach handlers to children
    return child
