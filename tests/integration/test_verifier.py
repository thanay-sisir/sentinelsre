"""E2E: the Harbor task verifier grades the live platform correctly.

Runs harbor/tasks/*/tests/verify.py as a subprocess against the in-process
platform — exactly what happens inside the sandbox verifier phase.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from demo_platform.ops_cli import service_manager as sm
from demo_platform.scenarios import wrong_inventory_endpoint as scenario

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
VERIFY = REPO / "harbor" / "tasks" / "wrong-inventory-endpoint" / "tests" / "verify.py"


def _run_verifier(runtime_dir: Path, out_dir: Path) -> dict[str, float]:
    env = {
        **os.environ,
        "SRE_OPS_HOME": str(REPO),
        "SRE_RUNTIME_DIR": str(runtime_dir),
        "SRE_VERIFIER_OUT": str(out_dir),
        "PYTHONPATH": str(REPO),
    }
    res = subprocess.run([sys.executable, str(VERIFY)], env=env, capture_output=True, text=True, timeout=60)
    reward_json = out_dir / "reward.json"
    assert reward_json.exists(), f"verifier wrote nothing: {res.stderr}"
    return json.loads(reward_json.read_text())


async def test_verifier_grades_broken_then_fixed(platform, tmp_path):
    runtime_dir = Path(os.environ["SRE_RUNTIME_DIR"])

    scenario.inject()
    time.sleep(0.5)
    try:
        broken = _run_verifier(runtime_dir, tmp_path / "broken")
        assert broken["config_fixed"] == 0.0
        assert broken["synthetic_passed"] == 0.0
        assert broken["policy_clean"] == 1.0  # nothing ran, nothing touched .verifier
        assert 0.0 < broken["reward"] < 1.0
    finally:
        scenario.reset()

    # Oracle-equivalent repair: restore config + reload, then verify again.
    st = sm.service_status("checkout-service")
    if not st["pid"]:
        sm.service_start("checkout-service")
    sm.service_reload("checkout-service")
    time.sleep(0.5)

    fixed = _run_verifier(runtime_dir, tmp_path / "fixed")
    assert fixed["config_fixed"] == 1.0
    assert fixed["service_healthy"] == 1.0
    assert fixed["synthetic_passed"] == 1.0
    assert fixed["policy_clean"] == 1.0
    assert fixed["reward"] == 1.0
