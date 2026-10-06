"""Deterministic verification executor.

Runs a RemediationPlan's verification_steps against the backend. No LLM is
involved — recovery is only claimed when these checks pass.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sre_agent.backends.base import OpsBackend
from sre_agent.models import CheckType, VerificationResult, VerificationStep

_RATE_TO_COUNTER = {"error_rate": "error_count"}


async def _run_one(backend: OpsBackend, step: VerificationStep) -> VerificationResult:
    """Execute a single verification step -> VerificationResult."""
    params: dict[str, Any] = step.params
    observed = ""
    passed = False
    details: dict[str, Any] = {}
    try:
        if step.check_type in (CheckType.HEALTH, CheckType.READINESS):
            hc = await backend.health_check(step.target)
            details = hc.model_dump(mode="json")
            if step.check_type == CheckType.HEALTH:
                passed = hc.alive
                observed = f"alive={hc.alive}"
            else:
                passed = bool(hc.ready)
                observed = f"ready={hc.ready}"
        elif step.check_type == CheckType.SYNTHETIC:
            cr = await backend.synthetic_check(step.target)
            details = cr.model_dump(mode="json")
            passed = cr.passed
            observed = f"passed={cr.passed} status={cr.status_code} {cr.detail[:120]}"
        elif step.check_type == CheckType.PROCESS_RUNNING:
            ss = await backend.get_service_status(step.target)
            details = ss.model_dump(mode="json")
            passed = ss.state.value == "RUNNING"
            observed = f"state={ss.state.value} pid={ss.pid}"
        elif step.check_type == CheckType.CONFIG_VALUE:
            cs = await backend.read_config(step.target)
            details = {"values": cs.values}
            expected_key = params.get("key")
            expected_val = params.get("value")
            actual = cs.values.get(expected_key) if expected_key else None
            passed = expected_key is not None and actual == expected_val
            observed = f"{expected_key}={actual!r} (expected {expected_val!r})"
        elif step.check_type == CheckType.METRIC_THRESHOLD:
            metric = params.get("metric", "error_rate")
            op = params.get("op", "lte")
            threshold = float(params.get("threshold", 0.0))
            sm = await backend.get_metrics(step.target, int(params.get("window_minutes", 5)))
            # Post-remediation delta mode: rate metrics (error_rate) map to
            # their cumulative counters — "no new errors since the fix"
            # instead of "lifetime/window rate <= 0", which can never pass on
            # a process that lived through the fault.
            counter = _RATE_TO_COUNTER.get(metric, metric)
            baseline = params.get(f"_baseline_{counter}")
            if baseline is not None:
                cur = getattr(sm, counter, None)
                details = {"metric": metric, "counter": counter, "value": cur, "baseline": baseline}
                if cur is None:
                    passed = False
                    observed = f"counter {counter} unavailable"
                else:
                    delta = cur - baseline
                    passed = delta <= threshold if op == "lte" else delta >= threshold
                    observed = f"{counter} delta={delta} (since baseline {baseline}) {op} {threshold}"
            else:
                value = getattr(sm, metric, None)
                details = {"metric": metric, "value": value}
                if value is None:
                    passed = False
                    observed = f"metric {metric} unavailable"
                else:
                    passed = value <= threshold if op == "lte" else value >= threshold
                    observed = f"{metric}={value} {op} {threshold}"
        else:
            observed = f"unsupported check_type {step.check_type}"
    except Exception as exc:
        observed = f"check errored: {exc.__class__.__name__}: {exc}"
        passed = False
    return VerificationResult(
        check_type=step.check_type,
        target=step.target,
        expected=step.expected,
        observed=observed,
        passed=passed,
        details=details,
    )


DEFAULT_STEPS = [
    VerificationStep(
        check_type=CheckType.PROCESS_RUNNING,
        target="checkout-service",
        expected="checkout-service process running",
    ),
    VerificationStep(
        check_type=CheckType.READINESS,
        target="checkout-service",
        expected="checkout-service readiness 200",
    ),
    VerificationStep(
        check_type=CheckType.SYNTHETIC,
        target="checkout",
        expected="synthetic checkout transaction succeeds",
    ),
]


async def run_verification_steps(
    backend: OpsBackend,
    steps: list[VerificationStep],
    *,
    retries: int = 3,
    backoff_base: float = 0.8,
) -> list[VerificationResult]:
    """Run steps with bounded retry of *failing* checks (transient tolerance)."""
    effective = steps or DEFAULT_STEPS
    results: list[VerificationResult] = []
    pending = list(effective)
    attempt = 0
    while pending and attempt <= retries:
        attempt += 1
        next_pending: list[VerificationStep] = []
        for step in pending:
            res = await _run_one(backend, step)
            if not res.passed and attempt <= retries:
                next_pending.append(step)
            else:
                results.append(res)
        pending = next_pending
        if pending and attempt <= retries:
            await asyncio.sleep(backoff_base * attempt)
    # Report failures for steps that exhausted retries
    for step in pending:
        results.append(await _run_one(backend, step))
    return results
