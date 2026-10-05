"""Tool factory: builds the typed tool surface over an OpsBackend.

Every backend call is wrapped with: budget tick -> approval-token redemption
for mutations -> backend call -> evidence + ActionRecord + audit rows ->
redaction -> byte truncation -> JSON string back to the model.

Domain/validation errors are returned to the model as text (it can correct
its call); budget, approval, and policy errors propagate — they end the loop.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from sre_agent.backends.base import OpsBackend
from sre_agent.config import ServiceRegistry, Settings
from sre_agent.exceptions import (
    ApprovalError,
    ApprovalRequiredError,
    BudgetExceededError,
    PolicyDeniedError,
)
from sre_agent.models import (
    ActionRecord,
    ApprovalToken,
    EvidenceRecord,
    EvidenceSourceType,
    IncidentState,
    SafetyEvent,
    SafetyEventKind,
)
from sre_agent.observability.local_logging import AuditLogger, get_logger
from sre_agent.policy.approvals import ApprovalManager
from sre_agent.policy.redaction import redact_text, redact_value

log = get_logger("tools")


@dataclass
class RunContext:
    """Per-incident shared context injected into every tool."""

    state: IncidentState
    settings: Settings
    registry: ServiceRegistry
    approvals: ApprovalManager | None = None
    bound_token: ApprovalToken | None = None
    audit: AuditLogger | None = None
    last_backup_id: str | None = None
    counters: dict[str, int] = field(default_factory=dict)

    def tick_tool_call(self) -> None:
        self.state.budgets.tool_calls += 1
        if self.state.budgets.tool_calls > self.settings.sre_max_tool_calls:
            self.state.add_safety_event(
                SafetyEvent(
                    event_id=f"se-{int(time.time() * 1000)}",
                    incident_id=self.state.incident_id,
                    kind=SafetyEventKind.BUDGET_EXCEEDED,
                    detail=f"tool_calls budget exceeded ({self.settings.sre_max_tool_calls})",
                    timestamp=datetime.now(UTC),
                )
            )
            raise BudgetExceededError("tool call budget exhausted")

    def record_evidence(
        self,
        *,
        source_type: EvidenceSourceType,
        source_name: str,
        tool_name: str,
        query_or_parameters: dict[str, Any],
        summary: str,
        structured_data: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> tuple[EvidenceRecord, bool]:
        rec = EvidenceRecord(
            incident_id=self.state.incident_id,
            source_type=source_type,
            source_name=source_name,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            query_or_parameters=redact_value(query_or_parameters),
            summary=redact_text(summary),
            structured_data=redact_value(structured_data),
            redacted=True,
        )
        return self.state.add_evidence(rec)

    def audit_event(self, event: str, **fields: Any) -> None:
        if self.audit:
            self.audit.record(event, **fields)
        log.info(event, **{k: str(v)[:200] for k, v in fields.items()})


@dataclass
class ToolSets:
    readonly: list[BaseTool]
    mutating: list[BaseTool]


# ---------------------------------------------------------------------------
# Shared wrappers
# ---------------------------------------------------------------------------


async def _run_read(
    ctx: RunContext,
    name: str,
    source_type: EvidenceSourceType,
    source_name: str,
    call: Callable[[], Awaitable[Any]],
    summarize: Callable[[Any], str],
    params: dict[str, Any] | None = None,
) -> str:
    ctx.tick_tool_call()
    t0 = time.perf_counter()
    try:
        result = await call()
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        ok, err = True, None
    except (BudgetExceededError, ApprovalError, PolicyDeniedError):
        raise
    except Exception as exc:
        ok, err, data, result = False, exc, {"error": str(exc)}, None
    ctx.audit_event(
        "tool_call",
        tool=name,
        params=params or {},
        success=ok,
        error=err,
        duration_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    if ok:
        ctx.record_evidence(
            source_type=source_type,
            source_name=source_name,
            tool_name=name,
            query_or_parameters=params or {},
            summary=summarize(result),
            structured_data=data if isinstance(data, dict) else {"items": data},
        )
    out = redact_text(json.dumps(data, default=str))
    limit = ctx.settings.sre_max_tool_output_bytes
    if len(out.encode()) > limit:
        out = out[:limit] + "...[truncated]"
    return out


def _require_token(ctx: RunContext, operation: str, target: str, params: dict[str, Any]) -> None:
    """Verify + consume the bound approval token for exactly this action."""
    if ctx.bound_token is None or ctx.approvals is None:
        ctx.state.add_safety_event(
            SafetyEvent(
                event_id=f"se-{int(time.time() * 1000)}",
                incident_id=ctx.state.incident_id,
                kind=SafetyEventKind.TOKEN_MISMATCH,
                detail=f"{operation} attempted without approval token",
                timestamp=datetime.now(UTC),
            )
        )
        raise ApprovalRequiredError(f"{operation} on {target} requires an approved action token")
    ctx.approvals.redeem(ctx.bound_token, operation=operation, target=target, params=params)


async def _run_mutate(
    ctx: RunContext,
    tool_name: str,
    operation: str,
    target: str,
    params: dict[str, Any],
    call: Callable[[], Awaitable[Any]],
) -> str:
    record = ActionRecord(
        incident_id=ctx.state.incident_id,
        plan_id=ctx.state.pending_plan.plan_id if ctx.state.pending_plan else None,
        tool_name=tool_name,
        target=target,
        parameters_redacted=redact_value({**params, "target": target}),
        approval_reference=ctx.bound_token.action_id if ctx.bound_token else None,
        started_at=datetime.now(UTC),
    )
    t0 = time.perf_counter()
    data: dict[str, Any]
    try:
        result = await call()
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        record.success = bool(data.get("success", True))
        record.result_summary = str(data.get("detail", ""))[:300]
        record.backend_result = data
        if backup := data.get("backup_id"):
            ctx.last_backup_id = backup
        ok, err = True, None
    except (BudgetExceededError, ApprovalError, PolicyDeniedError):
        record.success = False
        record.finished_at = datetime.now(UTC)
        ctx.state.executed_actions.append(record)
        raise
    except Exception as exc:
        record.success = False
        record.result_summary = str(exc)[:300]
        data = {"success": False, "error": str(exc)}
        ok, err = False, exc
    record.finished_at = datetime.now(UTC)
    ctx.state.executed_actions.append(record)
    if record.success:
        ctx.record_evidence(
            source_type=EvidenceSourceType.ACTION_RESULT,
            source_name=target,
            tool_name=tool_name,
            query_or_parameters=params,
            summary=f"{operation} on {target}: ok — {record.result_summary}",
            structured_data=data,
        )
    ctx.audit_event(
        "action_result",
        tool=tool_name,
        target=target,
        params=params,
        success=ok,
        error=err,
        duration_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    return redact_text(json.dumps(data, default=str))


# ---------------------------------------------------------------------------
# Read-only tools (bound to the Investigation node)
# ---------------------------------------------------------------------------


def build_readonly_tools(ctx: RunContext, backend: OpsBackend) -> list[BaseTool]:
    EST = EvidenceSourceType

    async def list_services() -> str:
        """List all known demo services with ports, kind, and declared deps."""
        return await _run_read(
            ctx,
            "list_services",
            EST.SERVICE_STATUS,
            "registry",
            backend.list_services,
            lambda r: f"{len(r)} services registered",
        )

    async def get_service_status(service_name: str) -> str:
        """Process state, pid, uptime, restart count, listen addresses,
        readiness for one service."""
        return await _run_read(
            ctx,
            "get_service_status",
            EST.SERVICE_STATUS,
            service_name,
            lambda: backend.get_service_status(service_name),
            lambda r: (
                f"{service_name}: {r.state.value} ready={r.readiness} pid={r.pid} restarts={r.restart_count}"
            ),
            {"service_name": service_name},
        )

    async def get_recent_logs(service_name: str, limit: int = 50, min_level: str | None = None) -> str:
        """Most recent structured log records for a service (bounded)."""
        limit = min(limit, ctx.settings.sre_max_log_lines)
        return await _run_read(
            ctx,
            "get_recent_logs",
            EST.LOG,
            service_name,
            lambda: backend.get_logs(service_name, limit=limit, min_level=min_level),
            lambda r: f"{len(r)} log records from {service_name}",
            {"service_name": service_name, "limit": limit, "min_level": min_level},
        )

    async def search_logs(service_name: str, query: str, limit: int = 50) -> str:
        """Search a service's logs for records containing the query string."""
        limit = min(limit, ctx.settings.sre_max_log_lines)
        return await _run_read(
            ctx,
            "search_logs",
            EST.LOG,
            service_name,
            lambda: backend.get_logs(service_name, limit=limit, query=query),
            lambda r: f"{len(r)} records matching {query!r} in {service_name}",
            {"service_name": service_name, "query": query, "limit": limit},
        )

    async def get_service_metrics(service_name: str, window_minutes: int = 5) -> str:
        """Operational metrics: request/error counts, error rate, p95 latency,
        dependency failures, queue depth, disk usage."""
        return await _run_read(
            ctx,
            "get_service_metrics",
            EST.METRIC,
            service_name,
            lambda: backend.get_metrics(service_name, window_minutes),
            lambda r: (
                f"{service_name}: err_rate={r.error_rate} "
                f"dep_failures={r.dependency_failure_count} up={r.service_up}"
            ),
            {"service_name": service_name, "window_minutes": window_minutes},
        )

    async def get_dependency_status(service_name: str) -> str:
        """Configured dependency endpoint vs registry-expected endpoint and
        reachability of each."""
        return await _run_read(
            ctx,
            "get_dependency_status",
            EST.DEPENDENCY,
            service_name,
            lambda: backend.get_dependency_status(service_name),
            lambda r: (
                f"{service_name} deps: " + ", ".join(f"{d.name} match={d.urls_match}" for d in r.dependencies)
            ),
            {"service_name": service_name},
        )

    async def get_disk_usage(path_scope: str = "runtime") -> str:
        """Disk utilization for an allowlisted scope (only 'runtime')."""
        return await _run_read(
            ctx,
            "get_disk_usage",
            EST.METRIC,
            path_scope,
            lambda: backend.get_disk_usage(path_scope),
            lambda r: f"disk {r.scope}: {r.percent}% used",
            {"path_scope": path_scope},
        )

    async def list_large_log_files(service_name: str, min_size_mb: float = 1.0) -> str:
        """Log files above a size threshold for a service."""
        return await _run_read(
            ctx,
            "list_large_log_files",
            EST.LOG,
            service_name,
            lambda: backend.list_large_log_files(service_name, min_size_mb),
            lambda r: f"{len(r)} large log files for {service_name}",
            {"service_name": service_name, "min_size_mb": min_size_mb},
        )

    async def read_runtime_config(service_name: str, keys: list[str] | None = None) -> str:
        """Allowlisted runtime config fields for a service (secrets redacted)
        plus a content hash for optimistic concurrency."""
        return await _run_read(
            ctx,
            "read_runtime_config",
            EST.CONFIGURATION,
            service_name,
            lambda: backend.read_config(service_name, keys),
            lambda r: f"{service_name} config hash={r.content_hash[:12]} fields={sorted(r.values)}",
            {"service_name": service_name, "keys": keys},
        )

    async def get_config_hash(service_name: str) -> str:
        """Content hash of a service's runtime config — capture before patching."""
        return await _run_read(
            ctx,
            "get_config_hash",
            EST.CONFIGURATION,
            service_name,
            lambda: backend.get_config_hash(service_name),
            lambda r: f"{service_name} config hash {r[:12]}",
            {"service_name": service_name},
        )

    async def run_health_check(service_name: str) -> str:
        """Liveness + readiness probe for a service."""
        return await _run_read(
            ctx,
            "run_health_check",
            EST.HEALTH_CHECK,
            service_name,
            lambda: backend.health_check(service_name),
            lambda r: f"{service_name} alive={r.alive} ready={r.ready}",
            {"service_name": service_name},
        )

    async def run_synthetic_transaction(transaction_name: str) -> str:
        """Run a deterministic end-to-end functional check (e.g. 'checkout')."""
        return await _run_read(
            ctx,
            "run_synthetic_transaction",
            EST.SYNTHETIC_CHECK,
            transaction_name,
            lambda: backend.synthetic_check(transaction_name),
            lambda r: f"{transaction_name}: {'PASS' if r.passed else 'FAIL'} ({r.detail[:120]})",
            {"transaction_name": transaction_name},
        )

    async def search_runbooks(query: str, limit: int = 3) -> str:
        """Keyword search over the local runbook corpus; returns section
        excerpts with relevance scores."""
        return await _run_read(
            ctx,
            "search_runbooks",
            EST.RUNBOOK,
            query,
            lambda: backend.search_runbooks(query, limit),
            lambda r: f"{len(r)} runbook sections matched {query!r}",
            {"query": query, "limit": limit},
        )

    async def get_incident_action_history() -> str:
        """Ops already recorded in this environment's audit log — avoids
        repeating actions."""
        return await _run_read(
            ctx,
            "get_incident_action_history",
            EST.ACTION_RESULT,
            "audit",
            backend.get_action_history,
            lambda r: f"{len(r)} ops already recorded",
        )

    async def get_incident_evidence() -> str:
        """Recap of evidence ids and summaries collected so far this incident."""
        ctx.tick_tool_call()
        items = [
            {
                "evidence_id": e.evidence_id,
                "source": e.source_name,
                "type": e.source_type.value,
                "summary": e.summary,
            }
            for e in ctx.state.evidence[-50:]
        ]
        return json.dumps({"count": len(items), "evidence": items}, default=str)

    return [
        StructuredTool.from_function(coroutine=list_services, name="list_services"),
        StructuredTool.from_function(coroutine=get_service_status, name="get_service_status"),
        StructuredTool.from_function(coroutine=get_recent_logs, name="get_recent_logs"),
        StructuredTool.from_function(coroutine=search_logs, name="search_logs"),
        StructuredTool.from_function(coroutine=get_service_metrics, name="get_service_metrics"),
        StructuredTool.from_function(coroutine=get_dependency_status, name="get_dependency_status"),
        StructuredTool.from_function(coroutine=get_disk_usage, name="get_disk_usage"),
        StructuredTool.from_function(coroutine=list_large_log_files, name="list_large_log_files"),
        StructuredTool.from_function(coroutine=read_runtime_config, name="read_runtime_config"),
        StructuredTool.from_function(coroutine=get_config_hash, name="get_config_hash"),
        StructuredTool.from_function(coroutine=run_health_check, name="run_health_check"),
        StructuredTool.from_function(coroutine=run_synthetic_transaction, name="run_synthetic_transaction"),
        StructuredTool.from_function(coroutine=search_runbooks, name="search_runbooks"),
        StructuredTool.from_function(
            coroutine=get_incident_action_history,
            name="get_incident_action_history",
        ),
        StructuredTool.from_function(coroutine=get_incident_evidence, name="get_incident_evidence"),
    ]


# ---------------------------------------------------------------------------
# Mutating tools (bound ONLY in the remediate node, gated by approval token)
# ---------------------------------------------------------------------------


def build_mutating_tools(ctx: RunContext, backend: OpsBackend) -> list[BaseTool]:
    async def patch_runtime_config(
        service_name: str, changes: dict[str, Any], expected_config_hash: str
    ) -> str:
        """Patch allowlisted fields of a service's runtime config. Takes a
        backup first and verifies expected_config_hash for optimistic
        concurrency. Requires an approved remediation plan."""
        params = {"changes": changes, "expected_config_hash": expected_config_hash}
        _require_token(ctx, "patch_runtime_config", service_name, params)
        return await _run_mutate(
            ctx,
            "patch_runtime_config",
            "patch_runtime_config",
            service_name,
            params,
            lambda: backend.patch_config(service_name, changes, expected_config_hash),
        )

    async def restore_runtime_config(service_name: str, backup_id: str) -> str:
        """Restore a service's runtime config from a prior backup_id
        (the rollback path)."""
        params = {"backup_id": backup_id}
        _require_token(ctx, "restore_runtime_config", service_name, params)
        return await _run_mutate(
            ctx,
            "restore_runtime_config",
            "restore_runtime_config",
            service_name,
            params,
            lambda: backend.restore_config(service_name, backup_id),
        )

    async def restart_service(service_name: str) -> str:
        """Restart a known demo service."""
        _require_token(ctx, "restart_service", service_name, {})
        return await _run_mutate(
            ctx,
            "restart_service",
            "restart_service",
            service_name,
            {},
            lambda: backend.restart_service(service_name),
        )

    async def reload_service(service_name: str) -> str:
        """Reload a service's runtime config in place."""
        _require_token(ctx, "reload_service", service_name, {})
        return await _run_mutate(
            ctx,
            "reload_service",
            "reload_service",
            service_name,
            {},
            lambda: backend.reload_service(service_name),
        )

    async def start_service(service_name: str) -> str:
        """Start a known stopped demo service."""
        _require_token(ctx, "start_service", service_name, {})
        return await _run_mutate(
            ctx,
            "start_service",
            "start_service",
            service_name,
            {},
            lambda: backend.start_service(service_name),
        )

    async def rotate_service_logs(service_name: str) -> str:
        """Safely rotate a service's logs (active file preserved)."""
        _require_token(ctx, "rotate_service_logs", service_name, {})
        return await _run_mutate(
            ctx,
            "rotate_service_logs",
            "rotate_service_logs",
            service_name,
            {},
            lambda: backend.rotate_logs(service_name),
        )

    return [
        StructuredTool.from_function(coroutine=patch_runtime_config, name="patch_runtime_config"),
        StructuredTool.from_function(coroutine=restore_runtime_config, name="restore_runtime_config"),
        StructuredTool.from_function(coroutine=restart_service, name="restart_service"),
        StructuredTool.from_function(coroutine=reload_service, name="reload_service"),
        StructuredTool.from_function(coroutine=start_service, name="start_service"),
        StructuredTool.from_function(coroutine=rotate_service_logs, name="rotate_service_logs"),
    ]


def build_tools(backend: OpsBackend, ctx: RunContext, *, include_mutating: bool) -> ToolSets:
    return ToolSets(
        readonly=build_readonly_tools(ctx, backend),
        mutating=build_mutating_tools(ctx, backend) if include_mutating else [],
    )
