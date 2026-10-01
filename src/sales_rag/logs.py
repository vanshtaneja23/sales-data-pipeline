"""Structured (JSON-lines) logging. Every log record is one JSON object, so query logs can be
grepped, shipped to a log store, or loaded with pandas without parsing free text."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(level: int = logging.INFO, log_file: Path | None = None) -> None:
    root = logging.getLogger("sales_rag")
    root.setLevel(level)
    root.handlers.clear()
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(JsonFormatter())
    root.addHandler(stream)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    root.propagate = False


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})
