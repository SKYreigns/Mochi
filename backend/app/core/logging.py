"""Logging configuration with redaction and a dedicated audit log."""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.core.config import Settings

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret)\s*[:=]\s*['\"]?([^\s'\",}]+)"),
    re.compile(r"(?i)(authorization:\s*)\S+"),
]


def redact(text: str, max_length: int = 4000) -> str:
    """Redact obvious credentials before logging or persisting tool output."""
    if not text:
        return text
    result = text
    result = _SECRET_PATTERNS[0].sub("***REDACTED***", result)
    result = _SECRET_PATTERNS[1].sub(r"\1***REDACTED***", result)
    result = _SECRET_PATTERNS[2].sub(r"\1=***REDACTED***", result)
    result = _SECRET_PATTERNS[3].sub(r"\1***REDACTED***", result)
    if len(result) > max_length:
        result = result[:max_length] + f"... [truncated {len(result) - max_length} chars]"
    return result


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if re.search(r"(?i)(password|secret|token|api[_-]?key|authorization)", str(key)):
                out[key] = "***REDACTED***"
            else:
                out[key] = redact_value(item, depth + 1)
        return out
    if isinstance(value, list):
        return [redact_value(item, depth + 1) for item in value[:100]]
    return value


def redact_payload(value: Any, max_chars: int = 20_000) -> Any:
    """Redact and bound a structured payload before persisting it.

    Screenshots and document previews can carry large base64 blobs; storing them
    verbatim would bloat the database and the event stream, so oversized payloads
    are replaced by a truncated preview.
    """
    safe = redact_value(value)
    try:
        encoded = json.dumps(safe, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"truncated": True, "preview": redact(str(value), max_chars)}
    if len(encoded) <= max_chars:
        return safe
    return {
        "truncated": True,
        "original_chars": len(encoded),
        "preview": encoded[:max_chars] + "... [truncated]",
    }


def setup_logging(settings: Settings) -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    if getattr(root, "_jarvis_configured", False):
        return
    root.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    app_file = RotatingFileHandler(log_dir / "jarvis.log", maxBytes=5_000_000, backupCount=4, encoding="utf-8")
    app_file.setFormatter(formatter)
    root.addHandler(app_file)

    audit_logger = logging.getLogger("jarvis.audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False
    audit_file = RotatingFileHandler(log_dir / "audit.log", maxBytes=5_000_000, backupCount=8, encoding="utf-8")
    audit_file.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(audit_file)

    root._jarvis_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"jarvis.{name}")


def audit(event: str, **fields: Any) -> None:
    """Append one structured, redacted audit record."""
    record = {"event": event, **redact_value(fields)}
    logging.getLogger("jarvis.audit").info(json.dumps(record, ensure_ascii=False, default=str))
