"""LocalProcessOpsBackend — drives the demo platform on this host via
demo_platform.ops_cli.service_manager (same code the sre-ops CLI exposes).

All service_manager calls are sync/blocking; we offload to a thread so the
backend surface stays async and matches the Harbor backend.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from demo_platform.common.config_store import (
    ConfigStoreError,
    StaleHashError,
    UnknownFieldError,
)
from demo_platform.ops_cli import service_manager as sm
from demo_platform.ops_cli.service_manager import OpsError

from sre_agent.exceptions import (
    BackendOperationError,
    NotAllowlistedError,
    StaleConfigHashError,
    UnknownServiceError,
)
from sre_agent.models.backend import (
    CheckResult,
    ConfigChangeResult,
    ConfigSnapshot,
    DependencyReport,
    DiskUsage,
    HealthCheckResult,
    LogFileInfo,
    LogRecord,
    OperationResult,
    ServiceInfo,
    ServiceMetrics,
    ServiceStatus,
)


def _err(service: str, op: str, exc: Exception) -> Exception:
    if isinstance(exc, KeyError):
        return UnknownServiceError(service)
    if isinstance(exc, StaleHashError):
        return StaleConfigHashError(service, exc.expected, exc.actual)
    if isinstance(exc, UnknownFieldError):
        return NotAllowlistedError(str(exc))
    if isinstance(exc, (OpsError, ConfigStoreError)):
        return BackendOperationError(op, service, str(exc))
    return BackendOperationError(op, service, f"{exc.__class__.__name__}: {exc}")


class LocalProcessOpsBackend:
    """OpsBackend over local demo processes. `environment_name` is cosmetic."""

    name = "local-process"

    async def _call(self, op: str, service: str, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:
            raise _err(service, op, exc) from exc

    async def list_services(self) -> list[ServiceInfo]:
        data = await self._call("list", "*", sm.services_list)
        return [ServiceInfo.model_validate(s) for s in data["services"]]

    async def get_service_status(self, service: str) -> ServiceStatus:
        data = await self._call("status", service, sm.service_status, service)
        return ServiceStatus.model_validate(data)

    async def get_logs(
        self,
        service: str,
        limit: int,
        query: str | None = None,
        min_level: str | None = None,
    ) -> list[LogRecord]:
        data = await self._call("logs", service, sm.logs_read, service, limit, min_level, query)
        return [LogRecord.model_validate(r) for r in data["records"]]

    async def get_metrics(self, service: str, window_minutes: int) -> ServiceMetrics:
        data = await self._call("metrics", service, sm.metrics_get, service, window_minutes)
        return ServiceMetrics.model_validate(data)

    async def get_dependency_status(self, service: str) -> DependencyReport:
        data = await self._call("deps", service, sm.deps_status, service)
        return DependencyReport.model_validate(data)

    async def read_config(self, service: str, keys: list[str] | None = None) -> ConfigSnapshot:
        data = await self._call("config_read", service, sm.config_read, service, keys)
        return ConfigSnapshot.model_validate(data)

    async def get_config_hash(self, service: str) -> str:
        data = await self._call("config_hash", service, sm.config_hash, service)
        return str(data["content_hash"])

    async def patch_config(
        self, service: str, changes: dict[str, Any], expected_hash: str
    ) -> ConfigChangeResult:
        data = await self._call("config_patch", service, sm.config_patch, service, changes, expected_hash)
        return ConfigChangeResult.model_validate(data)

    async def restore_config(self, service: str, backup_id: str) -> OperationResult:
        data = await self._call("config_restore", service, sm.config_restore, service, backup_id)
        return OperationResult.model_validate(data)

    async def restart_service(self, service: str) -> OperationResult:
        data = await self._call("restart", service, sm.service_restart, service)
        return OperationResult.model_validate(data)

    async def reload_service(self, service: str) -> OperationResult:
        data = await self._call("reload", service, sm.service_reload, service)
        return OperationResult.model_validate(data)

    async def start_service(self, service: str) -> OperationResult:
        data = await self._call("start", service, sm.service_start, service)
        return OperationResult.model_validate(data)

    async def rotate_logs(self, service: str) -> OperationResult:
        data = await self._call("rotate_logs", service, sm.logs_rotate, service)
        return OperationResult.model_validate(data)

    async def health_check(self, service: str) -> HealthCheckResult:
        data = await self._call("health", service, sm.check_health, service)
        return HealthCheckResult.model_validate(data)

    async def synthetic_check(self, check_name: str) -> CheckResult:
        data = await self._call("synthetic", check_name, sm.check_synthetic, check_name)
        return CheckResult.model_validate(data)

    async def get_disk_usage(self, scope: str) -> DiskUsage:
        data = await self._call("disk", scope, sm.disk_usage, scope)
        return DiskUsage.model_validate(data)

    async def list_large_log_files(self, service: str, min_size_mb: float) -> list[LogFileInfo]:
        data = await self._call("logs_large", service, sm.logs_large, service, min_size_mb)
        return [LogFileInfo.model_validate(f) for f in data["files"]]

    async def search_runbooks(self, query: str, limit: int) -> list[dict[str, Any]]:
        data = await self._call("runbooks", "*", sm.runbooks_search, query, limit)
        return list(data["results"])

    async def get_action_history(self) -> list[dict[str, Any]]:
        from demo_platform.common import paths

        audit = paths.audit_log()
        if not audit.exists():
            return []
        lines = audit.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-100:]:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # --- platform lifecycle (not part of the agent-facing OpsBackend) ---

    async def platform_up(self) -> dict[str, Any]:
        return await asyncio.to_thread(sm.platform_up)

    async def platform_down(self) -> dict[str, Any]:
        return await asyncio.to_thread(sm.platform_down)
