"""sentinelsre CLI — demo platform control, scenario injection, incident runs.

sentinelsre doctor                          # config/key sanity check
sentinelsre demo up|down|status             # local platform lifecycle
sentinelsre scenario inject wrong-inventory-endpoint [--bad-url ...]
sentinelsre incident run --title ... --service checkout-service
                        [--file alert.json] [--scripted]
                        [--approval-mode manual|auto_safe]
sentinelsre incident approve <id> [--deny] [--approver you]
sentinelsre incident status <id> | list
sentinelsre report show <id>
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import typer

from sre_agent.models.incident import IncidentRequest, Severity

app = typer.Typer(
    name="sentinelsre",
    help="SentinelSRE — sandboxed SRE incident-response agent",
    no_args_is_help=True,
)
demo_app = typer.Typer(help="Demo platform lifecycle", no_args_is_help=True)
scenario_app = typer.Typer(help="Failure scenario injection", no_args_is_help=True)
incident_app = typer.Typer(help="Incident runs and approvals", no_args_is_help=True)
report_app = typer.Typer(help="Incident reports", no_args_is_help=True)
app.add_typer(demo_app, name="demo")
app.add_typer(scenario_app, name="scenario")
app.add_typer(incident_app, name="incident")
app.add_typer(report_app, name="report")


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


@demo_app.command("up")
def demo_up() -> None:
    """Start all demo services (checkout, inventory, order-worker)."""
    from demo_platform.ops_cli.service_manager import platform_up

    results = platform_up()
    states = {
        name: (info["state"] == "RUNNING" and info["ready"] in (True, None))
        for name, info in results["readiness"].items()
    }
    for name, ok in states.items():
        typer.echo(f"  {name}: {'up' if ok else 'FAILED'}")
    if not all(states.values()):
        raise typer.Exit(code=1)


@demo_app.command("down")
def demo_down() -> None:
    """Stop all demo services."""
    from demo_platform.ops_cli.service_manager import platform_down

    platform_down()
    typer.echo("platform stopped")


@demo_app.command("status")
def demo_status() -> None:
    from demo_platform.ops_cli import service_manager as sm

    data = sm.services_list()
    for s in data["services"]:
        typer.echo(f"  {s['name']}: port={s.get('port')} deps={s.get('dependencies')}")


# ---------------------------------------------------------------------------
# scenario
# ---------------------------------------------------------------------------


@scenario_app.command("inject")
def scenario_inject(
    name: str = typer.Argument(..., help="scenario id, e.g. wrong-inventory-endpoint"),
    bad_url: str | None = typer.Option(None, "--bad-url", help="dead endpoint to point inventory_url at"),
) -> None:
    """Inject a failure scenario into the running platform."""
    if name == "wrong-inventory-endpoint":
        from demo_platform.scenarios.wrong_inventory_endpoint import inject

        out = inject(bad_url=bad_url)
        typer.echo(json.dumps(out, indent=2, default=str))
    else:
        typer.echo(f"unknown scenario {name!r}", err=True)
        raise typer.Exit(code=2)


@scenario_app.command("list")
def scenario_list() -> None:
    typer.echo("  wrong-inventory-endpoint — repoints checkout-service.inventory_url to a dead endpoint")


# ---------------------------------------------------------------------------
# incident
# ---------------------------------------------------------------------------


@incident_app.command("run")
def incident_run(
    file: Path | None = typer.Option(None, "--file", help="alert JSON file"),
    title: str | None = typer.Option(None, "--title"),
    description: str = typer.Option("", "--description"),
    service: list[str] = typer.Option([], "--service", "-s", help="affected service(s)"),
    severity: str = typer.Option("unknown", "--severity"),
    scenario_id: str | None = typer.Option(None, "--scenario-id"),
    scripted: bool = typer.Option(False, "--scripted", help="keyless deterministic mode"),
    approval_mode: str | None = typer.Option(None, "--approval-mode", help="manual | auto_safe | deny"),
) -> None:
    """Run the agent on one incident. Live mode needs a provider API key."""
    from sre_agent.runner import run_incident

    if file:
        request = IncidentRequest.model_validate_json(file.read_text(encoding="utf-8"))
    else:
        if not title:
            typer.echo("--title or --file is required", err=True)
            raise typer.Exit(code=2)
        request = IncidentRequest(
            incident_id=f"inc-{uuid4().hex[:12]}",
            title=title,
            description=description or title,
            affected_services=list(service),
            severity_hint=Severity(severity.upper()),
            scenario_id=scenario_id,
        )
    if approval_mode:
        approval_mode = approval_mode.replace("-", "_")
    st = asyncio.run(run_incident(request, scripted=scripted, approval_mode=approval_mode))
    typer.echo(f"incident {st.incident_id}: {st.status.value}")
    if st.status.value == "AWAITING_APPROVAL":
        typer.echo(f"paused for approval — run: sentinelsre incident approve {st.incident_id}")
    if st.final_report_path:
        typer.echo(f"report: {st.final_report_path}")
    if st.escalation_reason:
        typer.echo(f"escalated: {st.escalation_reason}")


@incident_app.command("approve")
def incident_approve(
    incident_id: str = typer.Argument(...),
    deny: bool = typer.Option(False, "--deny", help="deny instead of approve"),
    approver: str = typer.Option("cli:user", "--approver"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Resume an incident paused at AWAITING_APPROVAL."""
    from sre_agent.runner import resume_incident

    st = asyncio.run(resume_incident(incident_id, approved=not deny, approver=approver, reason=reason))
    typer.echo(f"incident {st.incident_id}: {st.status.value}")
    if st.final_report_path:
        typer.echo(f"report: {st.final_report_path}")


@incident_app.command("status")
def incident_status(incident_id: str) -> None:
    """Show a stored incident's status, hypotheses, and actions."""
    from sre_agent.persistence.database import init_schema
    from sre_agent.persistence.repository import IncidentRepository
    from sre_agent.runner import build_runtime

    async def _go() -> None:
        rt = build_runtime()
        await init_schema(rt["engine"])
        st = await IncidentRepository(rt["sessions"]).get(incident_id)
        if st is None:
            typer.echo(f"no incident {incident_id!r}", err=True)
            raise typer.Exit(code=1)
        typer.echo(
            json.dumps(
                {
                    "incident_id": st.incident_id,
                    "status": st.status.value,
                    "severity": st.severity.value,
                    "evidence": len(st.evidence),
                    "hypotheses": [
                        {"desc": h.candidate_root_cause, "confidence": h.confidence} for h in st.hypotheses
                    ],
                    "actions": len(st.executed_actions),
                    "verification": [
                        {"check": v.check_type.value, "passed": v.passed} for v in st.verification_results
                    ],
                    "escalation_reason": st.escalation_reason,
                    "report_path": st.final_report_path,
                },
                indent=2,
                default=str,
            )
        )

    asyncio.run(_go())


@incident_app.command("list")
def incident_list(limit: int = typer.Option(20, "--limit")) -> None:
    from sre_agent.persistence.database import init_schema
    from sre_agent.persistence.repository import IncidentRepository
    from sre_agent.runner import build_runtime

    async def _go() -> None:
        rt = build_runtime()
        await init_schema(rt["engine"])
        rows = await IncidentRepository(rt["sessions"]).list_incidents(limit)
        for r in rows:
            typer.echo(
                f"  {r['incident_id']}  {r['status']:<12} {r['severity']:<8} "
                f"{r['started_at']}  {r['title'][:60]}"
            )
        if not rows:
            typer.echo("  (no incidents recorded)")

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# report / doctor
# ---------------------------------------------------------------------------


@report_app.command("show")
def report_show(incident_id: str, json_out: bool = typer.Option(False, "--json")) -> None:
    """Print the stored report for an incident."""
    from sre_agent.config import get_settings

    base = Path(get_settings().sre_artifact_dir) / incident_id
    path = base / ("report.json" if json_out else "report.md")
    if not path.exists():
        typer.echo(f"no report at {path}", err=True)
        raise typer.Exit(code=1)
    typer.echo(path.read_text(encoding="utf-8"))


@app.command("doctor")
def doctor() -> None:
    """Sanity-check configuration: keys, files, demo platform reachability."""
    from sre_agent.config import (
        get_settings,
        load_policy_config,
        load_service_registry,
    )

    ok = True
    settings = get_settings()
    try:
        reg = load_service_registry(settings.sre_service_config_file)
        typer.echo(f"services.yaml: {len(reg.services)} services {reg.names()}")
    except Exception as exc:
        ok = False
        typer.echo(f"services.yaml FAILED: {exc}")
    try:
        pol = load_policy_config(settings.sre_policy_file)
        typer.echo(f"policy.yaml: {len(pol.operations)} operations allowlisted")
    except Exception as exc:
        ok = False
        typer.echo(f"policy.yaml FAILED: {exc}")
    try:
        settings.llm_api_key()
        typer.echo(f"LLM key ({settings.sre_llm_provider}): set")
    except Exception:
        typer.echo(f"LLM key ({settings.sre_llm_provider}): NOT set — use --scripted or set env")
    typer.echo(f"LangSmith tracing: {'enabled' if settings.langsmith_enabled() else 'disabled (no key)'}")
    typer.echo(f"artifact dir: {settings.sre_artifact_dir.resolve()}")
    if not ok:
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
