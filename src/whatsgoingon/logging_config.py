from __future__ import annotations

import json
import logging

# Attributes every LogRecord has regardless of what's passed via `extra=`; anything
# else on the record came from `extra` and should be surfaced as a structured field.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    """Formats each log record as one JSON line, promoting any `extra=` fields (e.g.
    the agent's tool name/input/result) to top-level keys so logs stay grep/parseable."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_ATTRS}
        payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def agent_logger(name: str, **context: object) -> logging.LoggerAdapter:
    """Logger that stamps `context` (e.g. agent="skeptic", month=..., round=...) onto every
    record it emits, merged with any per-call `extra=`, so the orchestrator's logs can be
    filtered by who said what without every call site repeating the same fields."""
    return logging.LoggerAdapter(logging.getLogger(name), context, merge_extra=True)
