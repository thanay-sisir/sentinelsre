"""Structured local logging + append-only per-incident audit log.

Every tool call, policy decision, backend op, and verification lands in
reports/<incident_id>/action_log.jsonl — the source the Harbor verifier
checks for the expected action sequence.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from structlog.typing import Processor

from sre_agent.policy.redaction import redact_value


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    import logging

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if fmt == "console":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), 20)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "sre_agent") -> Any:
    return structlog.get_logger(name)


class AuditLogger:
    """Append-only JSONL audit log for one incident."""

    def __init__(self, incident_id: str, artifact_dir: Path) -> None:
        self.incident_id = incident_id
        self.dir = Path(artifact_dir) / incident_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path = self.dir / "action_log.jsonl"
        self._log = get_logger("audit")

    @property
    def path(self) -> Path:
        return self._path

    def record(self, event: str, **fields: Any) -> None:
        row = {
            "timestamp": datetime.now(UTC).isoformat(),
            "incident_id": self.incident_id,
            "event": event,
            **redact_value(fields),
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def timed(self, event: str, **fields: Any) -> _TimedRecord:
        """Context manager: records event + duration_ms + success flag."""
        return _TimedRecord(self, event, fields)


class _TimedRecord:
    def __init__(self, audit: AuditLogger, event: str, fields: dict[str, Any]):
        self._audit = audit
        self._event = event
        self._fields = fields
        self._t0 = 0.0
        self.error: str | None = None

    def __enter__(self) -> _TimedRecord:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        duration_ms = round((time.perf_counter() - self._t0) * 1000, 1)
        self._audit.record(
            self._event,
            duration_ms=duration_ms,
            success=exc is None,
            error=str(exc) if exc else None,
            **self._fields,
        )
        return False  # never swallow


def log_uncaught(exc: BaseException) -> None:
    print(f"[sentinelsre] fatal: {exc}", file=sys.stderr)
