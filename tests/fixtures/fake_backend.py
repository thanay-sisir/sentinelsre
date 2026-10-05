"""In-memory FakeOpsBackend for unit tests — no processes, no LLM."""

from __future__ import annotations

import hashlib
import json
from typing import Any

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
    DependencyHealth,
    DependencyReport,
    DiskUsage,
    HealthCheckResult,
    LogFileInfo,
    LogRecord,
    OperationResult,
    ServiceInfo,
    ServiceMetrics,
    ServiceState,
    ServiceStatus,
)


class FakeOpsBackend:
    """Scriptable backend. `configs` holds per-service config dicts; patch
    respects `patchable` fields; `healthy` toggles readiness/synthetics."""

    name = "fake"

    def __init__(self) -> None:
        self.services = {
            "checkout-service": {
                "kind": "web",
                "port": 8081,
                "dependencies": ["inventory-service"],
            },
            "inventory-service": {"kind": "web", "port": 8082, "dependencies": []},
            "order-worker": {"kind": "worker", "port": None, "dependencies": []},
        }
        self.patchable = {"checkout-service": {"inventory_url", "request_timeout_ms"}}
        self.configs: dict[str, dict[str, Any]] = {
            "checkout-service": {
                "inventory_url": "http://127.0.0.1:8099",
                "request_timeout_ms": 2000,
            },
            "inventory-service": {"request_timeout_ms": 2000},
            "order-worker": {"poll_interval_ms": 500},
        }
        self.backups: dict[str, dict[str, Any]] = {}
        self.running = {"checkout-service": True, "inventory-service": True, "order-worker": True}
        self.ready = {"checkout-service": False, "inventory-service": True}
        self.checkout_pass = False
        self.logs: dict[str, list[dict[str, Any]]] = {
            "checkout-service": [
                {
                    "level": "ERROR",
                    "service": "checkout-service",
                    "event": "dependency_request_failed",
                    "message": "Connection refused to http://127.0.0.1:8099",
                    "metadata": {},
                }
            ]
        }
        self.calls: list[tuple[str, tuple, dict]] = []

    def _svc(self, name: str) -> dict:
        if name not in self.services:
            raise UnknownServiceError(name)
        return self.services[name]

    def _hash(self, svc: str) -> str:
        return hashlib.sha256(json.dumps(self.configs[svc], sort_keys=True).encode()).hexdigest()

    async def list_services(self) -> list[ServiceInfo]:
        return [
            ServiceInfo(
                name=n,
                kind=s["kind"],
                port=s["port"],
                dependencies=s.get("dependencies", []),
            )
            for n, s in self.services.items()
        ]

    async def get_service_status(self, service: str) -> ServiceStatus:
        self._svc(service)
        return ServiceStatus(
            service=service,
            state=ServiceState.RUNNING if self.running[service] else ServiceState.STOPPED,
            pid=1234 if self.running[service] else None,
            uptime_seconds=42.0 if self.running[service] else None,
            readiness=self.ready.get(service),
        )

    async def get_logs(self, service, limit, query=None, min_level=None):
        self._svc(service)
        recs = self.logs.get(service, [])
        if query:
            recs = [r for r in recs if query.lower() in json.dumps(r).lower()]
        if min_level:
            order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
            recs = [r for r in recs if order.get(r.get("level", "INFO"), 1) >= order[min_level]]
        return [LogRecord.model_validate(r) for r in recs[-limit:]]

    async def get_metrics(self, service, window_minutes):
        self._svc(service)
        return ServiceMetrics(
            service=service,
            window_minutes=window_minutes,
            request_count=10,
            error_count=5 if not self.checkout_pass else 0,
            error_rate=0.5 if not self.checkout_pass else 0.0,
            dependency_failure_count=5 if not self.checkout_pass else 0,
            service_up=self.running[service],
        )

    async def get_dependency_status(self, service):
        svc = self._svc(service)
        deps = []
        for dep in svc.get("dependencies", []):
            deps.append(
                DependencyHealth(
                    name=dep,
                    expected_url="http://127.0.0.1:8082",
                    configured_url=self.configs[service].get("inventory_url"),
                    urls_match=self.configs[service].get("inventory_url") == "http://127.0.0.1:8082",
                    expected_reachable=True,
                    configured_reachable=self.configs[service].get("inventory_url")
                    == "http://127.0.0.1:8082",
                )
            )
        return DependencyReport(service=service, dependencies=deps)

    async def read_config(self, service, keys=None):
        self._svc(service)
        values = self.configs[service]
        if keys:
            values = {k: values[k] for k in keys if k in values}
        return ConfigSnapshot(service=service, values=values, content_hash=self._hash(service))

    async def get_config_hash(self, service):
        self._svc(service)
        return self._hash(service)

    async def patch_config(self, service, changes, expected_hash):
        self._svc(service)
        allowed = self.patchable.get(service, set())
        bad = set(changes) - allowed
        if bad:
            raise NotAllowlistedError(f"fields {sorted(bad)} not patchable")
        current = self._hash(service)
        if expected_hash and expected_hash != current:
            raise StaleConfigHashError(service, expected_hash, current)
        backup_id = f"{service}-b{len(self.backups)}"
        self.backups[backup_id] = dict(self.configs[service])
        self.configs[service].update(changes)
        if changes.get("inventory_url") == "http://127.0.0.1:8082":
            self.ready["checkout-service"] = True
            self.checkout_pass = True
        return ConfigChangeResult(
            success=True,
            operation="patch_config",
            target=service,
            backup_id=backup_id,
            previous_hash=current,
            new_hash=self._hash(service),
            applied_fields=sorted(changes),
            detail="patched",
        )

    async def restore_config(self, service, backup_id):
        self._svc(service)
        if backup_id not in self.backups:
            raise BackendOperationError("restore_config", service, "no backup")
        self.configs[service] = dict(self.backups[backup_id])
        self.ready["checkout-service"] = False
        self.checkout_pass = False
        return OperationResult(success=True, operation="restore_config", target=service, detail="restored")

    async def restart_service(self, service):
        self._svc(service)
        self.running[service] = True
        return OperationResult(success=True, operation="restart", target=service, detail="restarted")

    async def reload_service(self, service):
        self._svc(service)
        if not self.running[service]:
            raise BackendOperationError("reload", service, "not running")
        return OperationResult(success=True, operation="reload", target=service, detail="reloaded")

    async def start_service(self, service):
        self._svc(service)
        self.running[service] = True
        return OperationResult(success=True, operation="start", target=service, detail="started")

    async def rotate_logs(self, service):
        self._svc(service)
        return OperationResult(success=True, operation="rotate_logs", target=service, detail="rotated")

    async def health_check(self, service):
        self._svc(service)
        return HealthCheckResult(
            service=service,
            alive=self.running[service],
            ready=self.ready.get(service, True),
            status_code=200,
            detail="ok",
        )

    async def synthetic_check(self, check_name):
        if check_name != "checkout":
            raise BackendOperationError("synthetic", check_name, "unknown check")
        return CheckResult(
            check_name=check_name,
            passed=self.checkout_pass,
            status_code=200 if self.checkout_pass else 503,
            detail="ok" if self.checkout_pass else "inventory_unavailable",
        )

    async def get_disk_usage(self, scope):
        if scope != "runtime":
            raise NotAllowlistedError("scope not allowlisted")
        return DiskUsage(scope=scope, used_bytes=1, total_bytes=100, percent=1.0)

    async def list_large_log_files(self, service, min_size_mb):
        self._svc(service)
        return [LogFileInfo(path=f"{service}.jsonl", size_bytes=2048)]

    async def search_runbooks(self, query, limit):
        return [{"runbook": "checkout_dependency_failure.md", "section": "x", "excerpt": "...", "score": 0.5}]

    async def get_action_history(self):
        return []
