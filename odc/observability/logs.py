"""Structured logging for ODC.

Plain stdlib logging with a small JSON formatter. Rich handles pretty
console output, JSON handles machine parsing. Same record, two views.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.logging import RichHandler

_initialized = False


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Initialize logging once. Console via rich, optional file via JSON."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger("odc")
    root.setLevel(level.upper())
    root.propagate = False

    # Console: rich, human-friendly
    console = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        markup=False,
        log_time_format="[%X]",
    )
    console.setLevel(level.upper())
    root.addHandler(console)

    if log_dir:
        log_file = log_dir / "odc.jsonl"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level.upper())
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)


class JsonFormatter(logging.Formatter):
    """One JSON object per log line. Easy to grep / pipe into jq."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Carry any structured fields the caller attached.
        for k, v in record.__dict__.items():
            if k in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            ):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "odc") -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, event: str, **fields) -> None:
    """Log with structured fields attached.

    >>> log_event(log, logging.INFO, "tool_call", tool="web.fetch", url="...")
    """
    # The stdlib LogRecord has a few reserved attribute names that we
    # must not overwrite. If a caller happens to use one as a field
    # name (very common: `name`, `args`, `message`), rename to a safe
    # `odc_*` prefix. This avoids the "Attempt to overwrite '<x>' in
    # LogRecord" KeyError that would otherwise crash the log call.
    _RESERVED = {
        "name", "msg", "args", "message", "levelname", "levelno",
        "pathname", "filename", "module", "exc_info", "exc_text",
        "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName",
        "process", "taskName",
    }
    safe: dict = {}
    for k, v in fields.items():
        if k in _RESERVED:
            safe[f"odc_{k}"] = v
        else:
            safe[k] = v
    logger.log(level, event, extra=safe)
