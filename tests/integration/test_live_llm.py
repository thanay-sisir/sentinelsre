"""Live-model E2E: real LangGraph + LLM against the in-process platform.

Skipped unless a provider key is present — CI runs `pytest -m "not live_llm"`.
This is the true end-to-end: scripted driver bypassed, real structured-output
planner/commander, real LangSmith traces when LANGSMITH_API_KEY is set.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from demo_platform.scenarios import wrong_inventory_endpoint as scenario

from sre_agent.config import Settings
from sre_agent.models.incident import IncidentRequest, Severity
from sre_agent.persistence.database import init_schema
from sre_agent.runner import build_runtime, run_incident

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]

_HAS_KEY = bool(
    os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
)


@pytest.mark.skipif(not _HAS_KEY, reason="no LLM provider key in env")
async def test_live_incident_resolves_or_escalates_cleanly(platform, tmp_path):
    """A live run must terminate deterministically — RESOLVED on success,
    ESCALATED with a reason on failure. Never hangs, never crashes the graph."""
    scenario.inject()
    time.sleep(0.5)
    try:
        settings = Settings(
            _env_file=Path.cwd() / ".env",
            sre_artifact_dir=tmp_path / "reports",
            sre_database_url=f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}",
            sre_approval_mode="auto_safe",
        )
        rt = build_runtime(settings)
        await init_schema(rt["engine"])
        st = await run_incident(
            IncidentRequest(
                incident_id=f"inc-live-{int(time.time())}",
                title="Checkout failures spiking",
                description="Synthetic checkout transactions are failing with HTTP 503.",
                affected_services=["checkout-service"],
                severity_hint=Severity.HIGH,
                scenario_id="wrong-inventory-endpoint",
            ),
            scripted=False,
            rt=rt,
        )
        assert st.status.value in ("RESOLVED", "ESCALATED", "AWAITING_APPROVAL")
        assert st.evidence
        assert st.final_report_path is None or Path(st.final_report_path).exists()
    finally:
        scenario.reset()
