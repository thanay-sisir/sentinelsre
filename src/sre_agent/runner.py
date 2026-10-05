"""Run wiring: builds Settings/registry/policy/backend/ctx and drives either
the LangGraph workflow (live models) or the scripted deterministic driver.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langgraph.types import Command

from sre_agent.backends.local_process import LocalProcessOpsBackend
from sre_agent.config import (
    Settings,
    get_settings,
    load_policy_config,
    load_service_registry,
)
from sre_agent.graph.graph import build_graph
from sre_agent.lifecycle import new_incident_state
from sre_agent.llm.factory import make_model
from sre_agent.llm.scripted import run_scripted_incident
from sre_agent.models.incident import IncidentRequest, IncidentState, IncidentStatus
from sre_agent.observability.local_logging import AuditLogger
from sre_agent.persistence.database import (
    init_schema,
    make_engine,
    make_session_factory,
)
from sre_agent.persistence.repository import IncidentRepository
from sre_agent.policy.approvals import ApprovalManager
from sre_agent.policy.engine import PolicyEngine
from sre_agent.tools.factory import RunContext


def build_runtime(
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Load config + construct backend/policy/repo shared by run & approve."""
    settings = settings or get_settings()
    registry = load_service_registry(settings.sre_service_config_file)
    policy_cfg = load_policy_config(settings.sre_policy_file)
    backend = LocalProcessOpsBackend()
    engine = make_engine(settings.sre_database_url)
    return {
        "settings": settings,
        "registry": registry,
        "policy_cfg": policy_cfg,
        "backend": backend,
        "engine": engine,
        "sessions": make_session_factory(engine),
    }


def make_ctx(
    rt: dict[str, Any],
    state: IncidentState,
    *,
    scripted: bool,
) -> RunContext:
    settings: Settings = rt["settings"]
    policy = PolicyEngine(
        rt["policy_cfg"],
        run_mode=state.run_mode,
        approval_mode=state.approval_mode,
    )
    approvals = ApprovalManager(ttl_seconds=rt["policy_cfg"].token_ttl_seconds())
    audit = AuditLogger(state.incident_id, Path(settings.sre_artifact_dir))
    models: dict[str, Any] = {}
    if not scripted:
        for role in ("commander", "investigator", "planner", "reporter"):
            models[role] = make_model(settings, role)
    return RunContext(
        state=state,
        settings=settings,
        registry=rt["registry"],
        backend=rt["backend"],
        models=models,
        policy=policy,
        approvals=approvals,
        audit=audit,
    )


def _configure_langsmith(settings: Settings, incident_id: str) -> None:
    """Enable native LangSmith tracing when a key is configured."""
    if settings.langsmith_enabled():
        assert settings.langsmith_api_key is not None
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key.get_secret_value())
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        if settings.langsmith_workspace_id:
            os.environ.setdefault("LANGSMITH_WORKSPACE_ID", settings.langsmith_workspace_id)
    else:
        os.environ.setdefault("LANGSMITH_TRACING", "false")


async def run_incident(
    request: IncidentRequest,
    *,
    scripted: bool = False,
    approval_mode: str | None = None,
    rt: dict[str, Any] | None = None,
) -> IncidentState:
    """Run one incident to completion (or pause at the approval interrupt)."""
    rt = rt or build_runtime()
    settings: Settings = rt["settings"]
    _configure_langsmith(settings, request.incident_id)
    await init_schema(rt["engine"])
    repo = IncidentRepository(rt["sessions"])

    st = new_incident_state(
        request,
        run_mode=settings.sre_run_mode,
        approval_mode=approval_mode or settings.sre_approval_mode,
    )
    st.scenario_id = request.scenario_id
    ctx = make_ctx(rt, st, scripted=scripted)
    ctx.audit_event("run_started", scripted=scripted, mode=st.approval_mode)

    if scripted:
        st = await run_scripted_incident(ctx)
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        ckpt_path = Path(settings.sre_artifact_dir) / "checkpoints.db"
        async with AsyncSqliteSaver.from_conn_string(str(ckpt_path)) as saver:
            graph = build_graph(checkpointer=saver)
            result = await graph.ainvoke(
                {"incident": st},
                config={
                    "configurable": {
                        "thread_id": request.incident_id,
                        "ctx": ctx,
                    },
                    "run_name": f"incident:{request.incident_id}",
                    "metadata": {
                        "incident_id": request.incident_id,
                        "scenario_id": request.scenario_id,
                        "mode": "live",
                    },
                },
            )
            interrupted = bool(result.get("__interrupt__"))
            st = result["incident"]
            assert isinstance(st, IncidentState)
            if interrupted:
                await repo.save(st)
                return st

    await repo.save(st)
    return st


async def resume_incident(
    incident_id: str,
    *,
    approved: bool,
    approver: str,
    reason: str | None = None,
    rt: dict[str, Any] | None = None,
) -> IncidentState:
    """Resume a graph paused at the approval interrupt (new process safe)."""
    rt = rt or build_runtime()
    settings: Settings = rt["settings"]
    _configure_langsmith(settings, incident_id)
    await init_schema(rt["engine"])
    repo = IncidentRepository(rt["sessions"])

    # Fresh ctx — the checkpointer restores IncidentState; nodes rebind
    # ctx.state at entry. Tokens are minted post-resume by remediate.
    placeholder = IncidentState(incident_id=incident_id, status=IncidentStatus.RECEIVED)
    ctx = make_ctx(rt, placeholder, scripted=False)
    ctx.audit_event("approval_resume", approved=approved, approver=approver, reason=reason)

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    ckpt_path = Path(settings.sre_artifact_dir) / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(ckpt_path)) as saver:
        graph = build_graph(checkpointer=saver)
        result = await graph.ainvoke(
            Command(resume={"approved": approved, "approver": approver, "reason": reason}),
            config={
                "configurable": {"thread_id": incident_id, "ctx": ctx},
                "run_name": f"incident:{incident_id}:resume",
            },
        )
    st = result["incident"]
    assert isinstance(st, IncidentState)
    ctx.state = st
    await repo.save(st)
    return st
