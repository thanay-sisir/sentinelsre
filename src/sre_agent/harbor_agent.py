"""SentinelSREAgent — Harbor custom-agent wrapper.

The LangGraph pipeline runs on the HOST (model keys stay local); only the
demo platform lives inside the sandbox. All ops reach the environment through
HarborOpsBackend -> fixed `sre-ops` subcommands, so the agent surface inside
the env is identical to local runs.

Usage:
    harbor run -t harbor/tasks/wrong-inventory-endpoint \
        -a sre_agent.harbor_agent:SentinelSREAgent -e langsmith \
        --env-file .env
"""

from __future__ import annotations

import asyncio
from typing import override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from sre_agent import __version__
from sre_agent.backends.harbor_backend import HarborOpsBackend
from sre_agent.config import get_settings
from sre_agent.models.incident import IncidentRequest, Severity
from sre_agent.persistence.database import init_schema
from sre_agent.runner import build_runtime, run_incident

SCENARIO_ID = "wrong-inventory-endpoint"


# harbor ships no py.typed, so BaseAgent resolves to Any under strict mypy.
class SentinelSREAgent(BaseAgent):  # type: ignore[misc]
    """Runs the SentinelSRE incident pipeline against a Harbor sandbox."""

    @staticmethod
    @override
    def name() -> str:
        return "sentinelsre"

    @override
    def version(self) -> str:
        return __version__

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        """Bring the demo platform up inside the env (fault already baked)."""
        backend = HarborOpsBackend(environment)
        result = await backend.platform_up()
        self.logger.info("platform_up", extra={"result": result})

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        settings = get_settings()
        rt = build_runtime(settings)
        rt["backend"] = HarborOpsBackend(environment)
        await init_schema(rt["engine"])

        incident_id = f"harbor-{self.context_id or 'local'}"
        request = IncidentRequest(
            incident_id=incident_id,
            title="Checkout failures spiking",
            description=instruction[:2000] or "Synthetic checkout transactions are failing.",
            affected_services=["checkout-service"],
            severity_hint=Severity.HIGH,
            scenario_id=SCENARIO_ID,
        )
        state = await run_incident(request, scripted=False, rt=rt)

        context.metadata = {
            **(context.metadata or {}),
            "final_status": state.status.value,
            "incident_id": state.incident_id,
            "escalation_reason": state.escalation_reason,
            "n_evidence": len(state.evidence),
            "n_actions": len(state.executed_actions),
            "langsmith_trace_id": state.trace_metadata.get("langsmith_trace_id"),
        }

        if state.status.value == "AWAITING_APPROVAL":
            # Auto-approve in unattended Harbor trials: the policy gate still
            # stands; this replaces the CLI approval step only.
            from sre_agent.runner import resume_incident

            state = await asyncio.wait_for(
                resume_incident(state.incident_id, approved=True, approver="harbor:agent", rt=rt),
                timeout=settings.sre_incident_timeout_seconds,
            )
            context.metadata["final_status"] = state.status.value
