"""Local benchmark driver: runs N incident trials and writes an eval report.

Two modes:
  scripted — deterministic offline driver, no model key needed. Measures the
             full orchestration/policy/verification pipeline.
  live     — real LangGraph + LLM. Requires provider + LangSmith keys in .env.

Services run in-process (SRE_PLATFORM_INPROCESS=1) so the benchmark works on
hosts where detached children get reaped. For real subprocess benchmarking
use `sentinelsre demo up` in a separate terminal.

    uv run python scripts/run_benchmark.py --trials 3 [--mode live]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # demo_platform is not an installed package


def _env(runtime_dir: Path, artifact_dir: Path) -> None:
    os.environ["SRE_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["SRE_PLATFORM_ROOT"] = str(REPO)
    os.environ["SRE_PLATFORM_INPROCESS"] = "1"
    os.environ["SRE_ARTIFACT_DIR"] = str(artifact_dir)


async def _one_trial(idx: int, mode: str, runtime_dir: Path, artifact_dir: Path) -> dict:
    """Run one incident trial; returns per-trial metrics."""
    # Fresh imports per call are unnecessary — modules resolve env at call time.
    from demo_platform.scenarios import wrong_inventory_endpoint as scenario

    from sre_agent.config import Settings
    from sre_agent.models.incident import IncidentRequest, Severity
    from sre_agent.persistence.database import init_schema
    from sre_agent.runner import build_runtime, run_incident

    scenario.reset()
    scenario.inject()
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    incident_id = f"bench-{mode}-{idx}-{int(time.time())}"
    try:
        settings = Settings(
            _env_file=REPO / ".env",
            sre_artifact_dir=artifact_dir,
            sre_database_url=f"sqlite+aiosqlite:///{artifact_dir}/bench.db",
            sre_approval_mode="auto_safe",
        )
        rt = build_runtime(settings)
        await init_schema(rt["engine"])
        req = IncidentRequest(
            incident_id=incident_id,
            title="Checkout failures spiking",
            description="Synthetic checkout transactions are failing with HTTP 503.",
            affected_services=["checkout-service"],
            severity_hint=Severity.HIGH,
            scenario_id="wrong-inventory-endpoint",
        )
        st = await run_incident(req, scripted=(mode == "scripted"), rt=rt)
        wall = round(time.monotonic() - t0, 2)
        verified = bool(st.verification_results) and all(v.passed for v in st.verification_results)
        return {
            "incident_id": incident_id,
            "mode": mode,
            "final_status": st.status.value,
            "resolved": st.status.value == "RESOLVED",
            "verified": verified,
            "evidence_count": len(st.evidence),
            "hypothesis_confidence": st.hypotheses[0].confidence if st.hypotheses else None,
            "root_cause": st.hypotheses[0].candidate_root_cause if st.hypotheses else None,
            "actions": [a.tool_name for a in st.executed_actions],
            "actions_ok": sum(1 for a in st.executed_actions if a.success),
            "model_turns": st.budgets.model_turns,
            "tool_calls": st.budgets.tool_calls,
            "escalation_reason": st.escalation_reason,
            "wall_seconds": wall,
            "report": st.final_report_path,
        }
    finally:
        scenario.reset()


async def main_async(trials: int, mode: str) -> int:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    artifact_dir = REPO / "reports" / f"benchmark-{ts}"
    runtime_dir = artifact_dir / "runtime"
    _env(runtime_dir, artifact_dir)

    from demo_platform.ops_cli import service_manager as sm

    print(f"[bench] platform up (in-process) — runtime {runtime_dir}")
    sm.platform_up(wait_ready_s=30.0)

    results = []
    try:
        for i in range(trials):
            print(f"[bench] trial {i + 1}/{trials} ({mode})…")
            res = await _one_trial(i, mode, runtime_dir, artifact_dir)
            results.append(res)
            print(f"  -> {res['final_status']} in {res['wall_seconds']}s actions={res['actions']}")
    finally:
        sm.platform_down()

    resolved = sum(1 for r in results if r["resolved"])
    summary = {
        "benchmark": "wrong-inventory-endpoint",
        "mode": mode,
        "started_at": ts,
        "trials": trials,
        "resolved": resolved,
        "resolve_rate": round(resolved / trials, 4) if trials else 0.0,
        "avg_wall_seconds": round(sum(r["wall_seconds"] for r in results) / len(results), 2)
        if results
        else 0.0,
        "trials_detail": results,
    }
    out = artifact_dir / "benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[bench] resolve rate {summary['resolve_rate']:.0%} ({resolved}/{trials}) — report: {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--mode", choices=["scripted", "live"], default="scripted")
    args = p.parse_args()
    if args.mode == "live":
        from dotenv import load_dotenv

        load_dotenv(REPO / ".env")
        if not os.environ.get("XAI_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
            print("live mode needs XAI_API_KEY (or OPENAI_API_KEY) in .env", file=sys.stderr)
            return 2
    return asyncio.run(main_async(args.trials, args.mode))


if __name__ == "__main__":
    raise SystemExit(main())
