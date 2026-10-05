"""HarborOpsBackend — drives the demo platform inside a Harbor sandbox.

Every OpsBackend method maps to a fixed `sre-ops` subcommand executed inside
the environment via environment.exec(). The model never gets raw shell: this
backend is the only exec surface and argv is constructed from typed params.

Config patches need a file in-env, so `patch_config` writes the change payload
via base64'd stdin heredoc before invoking `config patch`.
"""

from __future__ import annotations

import base64
import json
import shlex
from typing import Any, Protocol

from sre_agent.exceptions import BackendOperationError, UnknownServiceError
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

OPS_HOME = "/opt/sentinelsre"
OPS = "python -m demo_platform.ops_cli.cli"


class SupportsExec(Protocol):
    """Minimal slice of harbor.environments.base.BaseEnvironment we need."""

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> Any: ...


def _q(value: Any) -> str:
    return shlex.quote(str(value))


class HarborOpsBackend:
    """OpsBackend over a Harbor BaseEnvironment (exec-only surface)."""

    name = "harbor-environment"

    def __init__(self, environment: SupportsExec, *, timeout_sec: int = 60) -> None:
        self._env = environment
        self._timeout = timeout_sec

    async def _ops(self, *argv: str, timeout_sec: int | None = None) -> dict[str, Any]:
        cmd = f"{OPS} {' '.join(_q(a) for a in argv)}"
        res = await self._env.exec(cmd, cwd=OPS_HOME, timeout_sec=timeout_sec or self._timeout)
        if res.return_code != 0:
            raise BackendOperationError(
                argv[0] if argv else "ops",
                argv[-1] if argv else "?",
                (res.stderr or res.stdout or "").strip()[:500] or f"exit={res.return_code}",
            )
        try:
            parsed: dict[str, Any] = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise BackendOperationError(
                argv[0] if argv else "ops",
                argv[-1] if argv else "?",
                f"non-JSON ops output: {res.stdout[:200]!r}",
            ) from exc
        return parsed

    async def list_services(self) -> list[ServiceInfo]:
        data = await self._ops("services", "list")
        return [ServiceInfo.model_validate(s) for s in data["services"]]

    async def get_service_status(self, service: str) -> ServiceStatus:
        try:
            data = await self._ops("service", "status", service)
        except BackendOperationError as exc:
            if "unknown" in str(exc).lower():
                raise UnknownServiceError(service) from exc
            raise
        return ServiceStatus.model_validate(data)

    async def get_logs(
        self,
        service: str,
        limit: int,
        query: str | None = None,
        min_level: str | None = None,
    ) -> list[LogRecord]:
        argv = ["logs", "read", service, "--limit", str(limit)]
        if min_level:
            argv += ["--min-level", min_level]
        if query:
            argv += ["--query", query]
        data = await self._ops(*argv)
        return [LogRecord.model_validate(r) for r in data["records"]]

    async def get_metrics(self, service: str, window_minutes: int) -> ServiceMetrics:
        data = await self._ops("metrics", "get", service, "--window", str(window_minutes))
        return ServiceMetrics.model_validate(data)

    async def get_dependency_status(self, service: str) -> DependencyReport:
        data = await self._ops("deps", "status", service)
        return DependencyReport.model_validate(data)

    async def read_config(self, service: str, keys: list[str] | None = None) -> ConfigSnapshot:
        argv = ["config", "read", service]
        if keys:
            argv += ["--keys", ",".join(keys)]
        data = await self._ops(*argv)
        return ConfigSnapshot.model_validate(data)

    async def get_config_hash(self, service: str) -> str:
        data = await self._ops("config", "hash", service)
        return str(data["content_hash"])

    async def patch_config(
        self, service: str, changes: dict[str, Any], expected_hash: str
    ) -> ConfigChangeResult:
        # Payload travels as base64 to dodge all shell-quoting pitfalls.
        # Staging lands in the platform's private runtime dir, not world-writable /tmp.
        b64 = base64.b64encode(json.dumps(changes).encode()).decode()
        staging = f"runtime/patch-{service}.json"
        write = f"mkdir -p runtime && echo {b64} | base64 -d > {staging}"
        res = await self._env.exec(write, cwd=OPS_HOME, timeout_sec=self._timeout)
        if res.return_code != 0:
            raise BackendOperationError("config_patch", service, f"patch staging failed: {res.stderr[:200]}")
        data = await self._ops(
            "config",
            "patch",
            service,
            "--patch-file",
            staging,
            "--expected-hash",
            expected_hash,
        )
        return ConfigChangeResult.model_validate(data)

    async def restore_config(self, service: str, backup_id: str) -> OperationResult:
        data = await self._ops("config", "restore", service, "--backup-id", backup_id)
        return OperationResult.model_validate(data)

    async def restart_service(self, service: str) -> OperationResult:
        data = await self._ops("service", "restart", service)
        return OperationResult.model_validate(data)

    async def reload_service(self, service: str) -> OperationResult:
        data = await self._ops("service", "reload", service)
        return OperationResult.model_validate(data)

    async def start_service(self, service: str) -> OperationResult:
        data = await self._ops("service", "start", service)
        return OperationResult.model_validate(data)

    async def rotate_logs(self, service: str) -> OperationResult:
        data = await self._ops("logs", "rotate", service)
        return OperationResult.model_validate(data)

    async def health_check(self, service: str) -> HealthCheckResult:
        data = await self._ops("check", "health", service)
        return HealthCheckResult.model_validate(data)

    async def synthetic_check(self, check_name: str) -> CheckResult:
        data = await self._ops("check", "synthetic", check_name)
        return CheckResult.model_validate(data)

    async def get_disk_usage(self, scope: str) -> DiskUsage:
        data = await self._ops("disk", "usage", "--scope", scope)
        return DiskUsage.model_validate(data)

    async def list_large_log_files(self, service: str, min_size_mb: float) -> list[LogFileInfo]:
        data = await self._ops("logs", "large", service, "--min-mb", str(min_size_mb))
        return [LogFileInfo.model_validate(f) for f in data["files"]]

    async def search_runbooks(self, query: str, limit: int) -> list[dict[str, Any]]:
        data = await self._ops("runbooks", "search", query, "--limit", str(limit))
        return list(data["results"])

    async def get_action_history(self) -> list[dict[str, Any]]:
        res = await self._env.exec(
            "tail -n 100 runtime/audit.jsonl 2>/dev/null || true",
            cwd=OPS_HOME,
            timeout_sec=self._timeout,
        )
        out: list[dict[str, Any]] = []
        for line in res.stdout.splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # --- platform lifecycle (harness-side, not agent-facing) ---

    async def platform_up(self) -> dict[str, Any]:
        return await self._ops("platform", "up", timeout_sec=120)

    async def platform_down(self) -> dict[str, Any]:
        return await self._ops("platform", "down")
