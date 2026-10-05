"""OpsBackend protocol — every agent tool routes through this interface.

The agent never touches the environment directly. Backends enforce service
allowlists, typed args, and timeouts; policy + approval gating happens in the
tool layer above this interface.
"""

from __future__ import annotations

from typing import Any, Protocol

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


class OpsBackend(Protocol):
    """Async ops interface. Implementations: local_process, harbor_environment."""

    async def list_services(self) -> list[ServiceInfo]: ...

    async def get_service_status(self, service: str) -> ServiceStatus: ...

    async def get_logs(
        self,
        service: str,
        limit: int,
        query: str | None = None,
        min_level: str | None = None,
    ) -> list[LogRecord]: ...

    async def get_metrics(self, service: str, window_minutes: int) -> ServiceMetrics: ...

    async def get_dependency_status(self, service: str) -> DependencyReport: ...

    async def read_config(self, service: str, keys: list[str] | None = None) -> ConfigSnapshot: ...

    async def get_config_hash(self, service: str) -> str: ...

    async def patch_config(
        self, service: str, changes: dict[str, Any], expected_hash: str
    ) -> ConfigChangeResult: ...

    async def restore_config(self, service: str, backup_id: str) -> OperationResult: ...

    async def restart_service(self, service: str) -> OperationResult: ...

    async def reload_service(self, service: str) -> OperationResult: ...

    async def start_service(self, service: str) -> OperationResult: ...

    async def rotate_logs(self, service: str) -> OperationResult: ...

    async def health_check(self, service: str) -> HealthCheckResult: ...

    async def synthetic_check(self, check_name: str) -> CheckResult: ...

    async def get_disk_usage(self, scope: str) -> DiskUsage: ...

    async def list_large_log_files(self, service: str, min_size_mb: float) -> list[LogFileInfo]: ...

    async def search_runbooks(self, query: str, limit: int) -> list[dict[str, Any]]: ...

    async def get_action_history(self) -> list[dict[str, Any]]: ...
