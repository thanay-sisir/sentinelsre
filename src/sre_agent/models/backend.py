"""Typed request/response models shared by all OpsBackend implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ServiceState(StrEnum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class ServiceInfo(BaseModel):
    name: str
    kind: str  # web | worker
    port: int | None = None
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)


class ServiceStatus(BaseModel):
    service: str
    known: bool = True
    state: ServiceState
    pid: int | None = None
    uptime_seconds: float | None = None
    restart_count: int = 0
    listen_addresses: list[str] = Field(default_factory=list)
    readiness: bool | None = None
    last_state_change: datetime | None = None
    detail: str = ""


class LogRecord(BaseModel):
    timestamp: datetime | None = None
    level: str = "INFO"
    service: str = ""
    event: str = ""
    message: str = ""
    request_id: str | None = None
    incident_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False


class ServiceMetrics(BaseModel):
    service: str
    window_minutes: int = 5
    request_count: int = 0
    error_count: int = 0
    error_rate: float = 0.0
    p95_latency_ms: float | None = None
    dependency_failure_count: int = 0
    restart_count: int = 0
    queue_depth: int | None = None
    disk_usage_percent: float | None = None
    service_up: bool = False


class DependencyHealth(BaseModel):
    name: str
    expected_url: str | None = None
    reachable: bool | None = None
    detail: str = ""


class DependencyReport(BaseModel):
    service: str
    dependencies: list[DependencyHealth] = Field(default_factory=list)


class ConfigSnapshot(BaseModel):
    service: str
    values: dict[str, Any]  # secret fields are redacted before this is built
    content_hash: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OperationResult(BaseModel):
    success: bool
    operation: str
    target: str
    detail: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None


class ConfigChangeResult(OperationResult):
    backup_id: str | None = None
    previous_hash: str | None = None
    new_hash: str | None = None
    applied_fields: list[str] = Field(default_factory=list)


class HealthCheckResult(BaseModel):
    service: str
    alive: bool
    ready: bool | None = None
    status_code: int | None = None
    detail: str = ""
    latency_ms: float | None = None


class CheckResult(BaseModel):
    check_name: str
    passed: bool
    status_code: int | None = None
    detail: str = ""
    latency_ms: float | None = None


class DiskUsage(BaseModel):
    scope: str
    used_bytes: int
    total_bytes: int
    percent: float


class LogFileInfo(BaseModel):
    path: str
    size_bytes: int
    modified_at: datetime | None = None
