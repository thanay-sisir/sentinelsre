"""Structured JSON logging for demo services.

Each service appends JSONL records to runtime/logs/<service>.jsonl with the
schema from the spec (timestamp/level/service/event/message/request_id/...).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from demo_platform.common import paths


class JsonlLogger:
    """Minimal dependency-free JSONL logger (stdout mirror optional)."""

    def __init__(self, service: str, mirror_stdout: bool = False) -> None:
        self.service = service
        self.mirror_stdout = mirror_stdout
        paths.ensure_dirs()
        self._path = paths.logs_dir() / f"{service}.jsonl"
        self._fh: TextIO = self._path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def log(
        self,
        level: str,
        event: str,
        message: str,
        *,
        request_id: str | None = None,
        incident_id: str | None = None,
        **metadata: Any,
    ) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "service": self.service,
            "event": event,
            "message": message,
            "request_id": request_id,
            "incident_id": incident_id,
            "metadata": metadata,
        }
        line = json.dumps(record, default=str)
        self._fh.write(line + "\n")
        self._fh.flush()
        if self.mirror_stdout:
            print(line, file=sys.stdout, flush=True)

    def info(self, event: str, message: str, **kw: Any) -> None:
        self.log("INFO", event, message, **kw)

    def warning(self, event: str, message: str, **kw: Any) -> None:
        self.log("WARNING", event, message, **kw)

    def error(self, event: str, message: str, **kw: Any) -> None:
        self.log("ERROR", event, message, **kw)

    def close(self) -> None:
        self._fh.close()
