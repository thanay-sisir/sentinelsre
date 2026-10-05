"""Tool-layer tests against FakeOpsBackend: evidence, budgets, token gating."""

import pytest

from sre_agent.config import Settings, load_service_registry
from sre_agent.exceptions import (
    ApprovalMismatchError,
    ApprovalRequiredError,
    BudgetExceededError,
)
from sre_agent.models import IncidentState, IncidentStatus
from sre_agent.policy.approvals import ApprovalManager
from sre_agent.tools.factory import RunContext, build_tools
from tests.fixtures.fake_backend import FakeOpsBackend


def _ctx(state=None, **kw) -> RunContext:
    state = state or IncidentState(incident_id="INC-1", status=IncidentStatus.INVESTIGATING)
    return RunContext(
        state=state,
        settings=Settings(_env_file=None, **kw),
        registry=load_service_registry(),
        approvals=kw.get("approvals"),
    )


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


async def test_read_tool_records_evidence():
    ctx = _ctx()
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=False)
    out = await _tool(tools.readonly, "get_service_status").ainvoke({"service_name": "checkout-service"})
    assert "UNHEALTHY" in out or "not ready" in out or "RUNNING" in out
    assert len(ctx.state.evidence) == 1
    assert ctx.state.evidence[0].source_type.value == "SERVICE_STATUS"
    assert ctx.state.budgets.tool_calls == 1


async def test_evidence_dedup_no_double_count():
    ctx = _ctx()
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=False)
    t = _tool(tools.readonly, "get_service_status")
    await t.ainvoke({"service_name": "checkout-service"})
    await t.ainvoke({"service_name": "checkout-service"})
    assert len(ctx.state.evidence) == 1  # identical observation deduped


async def test_tool_budget_enforced():
    ctx = _ctx(sre_max_tool_calls=2)
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=False)
    t = _tool(tools.readonly, "get_service_status")
    await t.ainvoke({"service_name": "checkout-service"})
    await t.ainvoke({"service_name": "inventory-service"})
    with pytest.raises(BudgetExceededError):
        await t.ainvoke({"service_name": "order-worker"})
    assert ctx.state.safety_events  # budget safety event recorded


async def test_unknown_service_error_returned_as_text():
    ctx = _ctx()
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=False)
    out = await _tool(tools.readonly, "get_service_status").ainvoke({"service_name": "nope"})
    assert "unknown service" in out


async def test_mutating_requires_token():
    ctx = _ctx()
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=True)
    with pytest.raises(ApprovalRequiredError):
        await _tool(tools.mutating, "restart_service").ainvoke({"service_name": "checkout-service"})
    ev = ctx.state.safety_events[-1]
    assert ev.kind.value == "TOKEN_MISMATCH"


async def test_mutating_with_token_executes():
    state = IncidentState(incident_id="INC-1", status=IncidentStatus.REMEDIATING)
    mgr = ApprovalManager(secret=b"s")
    token = mgr.issue(
        incident_id="INC-1",
        action_id="act-1",
        operation="patch_runtime_config",
        target="checkout-service",
        params={
            "changes": {"inventory_url": "http://127.0.0.1:8082"},
            "expected_config_hash": "",  # fake backend skips when falsy
        },
        issued_by="auto-policy:auto_safe",
    )
    ctx = _ctx(state=state, approvals=mgr)
    ctx.bound_tokens = {token.operation: token}
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=True)
    out = await _tool(tools.mutating, "patch_runtime_config").ainvoke(
        {
            "service_name": "checkout-service",
            "changes": {"inventory_url": "http://127.0.0.1:8082"},
            "expected_config_hash": "",
        }
    )
    assert '"success": true' in out
    assert backend.configs["checkout-service"]["inventory_url"].endswith("8082")
    assert state.executed_actions[-1].success is True
    assert ctx.last_backup_id is not None


async def test_mutating_wrong_params_blocked():
    mgr = ApprovalManager(secret=b"s")
    token = mgr.issue(
        incident_id="INC-1",
        action_id="a",
        operation="restart_service",
        target="checkout-service",
        params={},
        issued_by="test",
    )
    ctx = _ctx(approvals=mgr)
    ctx.bound_tokens = {token.operation: token}
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=True)
    # token says restart checkout; model tries a DIFFERENT service
    with pytest.raises(ApprovalMismatchError):
        await _tool(tools.mutating, "restart_service").ainvoke({"service_name": "inventory-service"})


async def test_output_truncation():
    ctx = _ctx(sre_max_tool_output_bytes=120)
    backend = FakeOpsBackend()
    tools = build_tools(backend, ctx, include_mutating=False)
    out = await _tool(tools.readonly, "list_services").ainvoke({})
    assert len(out.encode()) <= 140  # slightly over due to suffix
    assert out.endswith("[truncated]")


async def test_single_use_token_blocks_second_mutation():
    state = IncidentState(incident_id="INC-1", status=IncidentStatus.REMEDIATING)
    mgr = ApprovalManager(secret=b"s")
    token = mgr.issue(
        incident_id="INC-1",
        action_id="a",
        operation="restart_service",
        target="checkout-service",
        params={},
        issued_by="test",
    )
    ctx = _ctx(state=state, approvals=mgr)
    ctx.bound_tokens = {token.operation: token}
    tools = build_tools(FakeOpsBackend(), ctx, include_mutating=True)
    t = _tool(tools.mutating, "restart_service")
    await t.ainvoke({"service_name": "checkout-service"})
    from sre_agent.exceptions import ApprovalReplayError

    with pytest.raises(ApprovalReplayError):
        await t.ainvoke({"service_name": "checkout-service"})
