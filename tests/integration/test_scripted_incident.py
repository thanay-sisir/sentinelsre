"""E2E: scripted incident run against the live demo platform.

Covers the full pipeline keyless: alert -> evidence -> hypothesis -> plan ->
policy -> approval -> token-gated remediation -> deterministic verification
-> report + persistence. LLM nodes are absent; scripted stand-ins exercise
the same tools/tokens/audit machinery.
"""

import json
import time
from pathlib import Path

import pytest
from demo_platform.ops_cli import service_manager as sm
from demo_platform.scenarios import wrong_inventory_endpoint as scenario

from sre_agent.config import Settings
from sre_agent.models.incident import IncidentRequest, Severity
from sre_agent.persistence.database import init_schema
from sre_agent.persistence.repository import IncidentRepository
from sre_agent.runner import build_runtime, run_incident

pytestmark = pytest.mark.integration


def _request() -> IncidentRequest:
    return IncidentRequest(
        incident_id="inc-scripted-e2e",
        title="Checkout failures spiking",
        description="Synthetic checkout transactions are failing with HTTP 503.",
        affected_services=["checkout-service"],
        severity_hint=Severity.HIGH,
        scenario_id="wrong-inventory-endpoint",
    )


async def test_scripted_end_to_end_resolves(platform, tmp_path):
    scenario.inject()
    time.sleep(0.5)
    try:
        settings = Settings(
            _env_file=None,
            sre_artifact_dir=tmp_path / "reports",
            sre_database_url=f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}",
            sre_approval_mode="auto_safe",
        )
        rt = build_runtime(settings)
        await init_schema(rt["engine"])

        st = await run_incident(_request(), scripted=True, rt=rt)

        # lifecycle outcome
        assert st.status.value == "RESOLVED", st.escalation_reason

        # evidence trail exists and is grounded
        assert len(st.evidence) >= 5
        assert any(e.source_type.value == "DEPENDENCY" for e in st.evidence)
        assert any(e.source_type.value == "SYNTHETIC_CHECK" for e in st.evidence)

        # hypothesis confirmed with real evidence ids
        assert st.hypotheses[0].status.value == "CONFIRMED"
        assert st.hypotheses[0].confidence >= 0.8

        # plan + policy + executed remediation
        plan = st.pending_plan
        assert plan is not None
        assert plan.operation == "patch_runtime_config"
        assert plan.target_service == "checkout-service"
        executed = {a.tool_name: a for a in st.executed_actions}
        assert executed["patch_runtime_config"].success is True
        assert executed["reload_service"].success is True

        # deterministic verification all passed
        assert st.verification_results
        assert all(v.passed for v in st.verification_results)

        # platform actually recovered
        assert sm.check_synthetic("checkout")["passed"]
        assert sm.check_health("checkout-service")["ready"] is True

        # approval audit trail + tokens were consumed (single-use)
        assert st.approval_requests
        assert all(a.status.value == "APPROVED" for a in st.approval_requests)

        # reports + persistence artifacts
        assert st.final_report_path and Path(st.final_report_path).exists()
        out_dir = Path(st.final_report_path).parent
        rep = json.loads((out_dir / "report.json").read_text())
        assert rep["final_status"] == "RESOLVED"
        assert rep["root_cause_confidence"] >= 0.8
        assert rep["verification_results"]
        assert (out_dir / "action_log.jsonl").exists()

        loaded = await IncidentRepository(rt["sessions"]).get(st.incident_id)
        assert loaded is not None
        assert loaded.status.value == "RESOLVED"
    finally:
        scenario.reset()


async def test_scripted_manual_mode_records_approval(platform, tmp_path):
    """Manual approval mode is honored in scripted runs (harness approves)."""
    scenario.inject()
    time.sleep(0.5)
    try:
        settings = Settings(
            _env_file=None,
            sre_artifact_dir=tmp_path / "reports",
            sre_database_url=f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}",
            sre_approval_mode="manual",
        )
        rt = build_runtime(settings)
        await init_schema(rt["engine"])
        req = _request()
        req.incident_id = "inc-scripted-manual"
        st = await run_incident(req, scripted=True, rt=rt)
        assert st.status.value == "RESOLVED", st.escalation_reason
        assert any(t.previous_status.value == "AWAITING_APPROVAL" for t in st.transitions)
        approvers = {a.approver for a in st.approval_requests}
        assert "scripted:harness" in approvers
    finally:
        scenario.reset()
