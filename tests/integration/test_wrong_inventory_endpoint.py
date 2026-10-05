"""Integration test: platform up -> inject fault -> observe breakage ->
oracle repair -> verify recovery -> rollback path -> platform down.

Runs the real demo platform as child processes of pytest (no Docker needed).
Marked `integration` — runs in CI but skipped with -m "not integration".
"""

import json
import time
from pathlib import Path

import pytest
from demo_platform.common import paths
from demo_platform.ops_cli import service_manager as sm
from demo_platform.scenarios import wrong_inventory_endpoint as scenario

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    """Boot the whole platform in a throwaway runtime dir.

    Children of pytest stay alive for the test module's duration — that is
    exactly what we need on Windows where detached grandchildren are reaped.
    """
    rt = tmp_path_factory.mktemp("runtime")
    import os

    os.environ["SRE_RUNTIME_DIR"] = str(rt)
    os.environ["SRE_PLATFORM_ROOT"] = str(Path.cwd())
    sm.platform_up(wait_ready_s=20.0)
    yield
    sm.platform_down()


def test_platform_up_all_ready(platform):
    status = sm.platform_status()
    assert status["checkout-service"]["state"] == "RUNNING"
    assert status["inventory-service"]["state"] == "RUNNING"
    assert status["order-worker"]["state"] == "RUNNING"
    assert sm.check_health("checkout-service")["ready"] is True


def test_healthy_synthetic_checkout(platform):
    result = sm.check_synthetic("checkout")
    assert result["passed"], result["detail"]
    assert result["status_code"] == 200


def test_fault_injection_breaks_checkout(platform):
    scenario.inject()
    time.sleep(0.5)  # let reload settle

    health = sm.check_health("checkout-service")
    assert health["alive"] is True
    assert health["ready"] is False

    syn = sm.check_synthetic("checkout")
    assert not syn["passed"]

    cfg = sm.config_read("checkout-service")
    assert cfg["values"]["inventory_url"] == scenario.BAD_URL

    # Dependency evidence: registry says 8082 healthy, config points at dead 8099
    deps = sm.deps_status("checkout-service")
    dep = deps["dependencies"][0]
    assert dep["urls_match"] is False
    assert dep["expected_reachable"] is True
    assert dep["configured_reachable"] is False

    # Log evidence exists
    logs = sm.logs_read("checkout-service", limit=20, query="dependency")
    assert logs["count"] > 0


def test_oracle_repair_restores_service(platform):
    # oracle: correct endpoint from the service registry, not from ground truth
    deps = sm.deps_status("checkout-service")
    correct = deps["dependencies"][0]["expected_url"]
    current_hash = sm.config_hash("checkout-service")["content_hash"]
    result = sm.config_patch("checkout-service", {"inventory_url": correct}, current_hash)
    assert result["success"]
    assert result["backup_id"]
    sm.service_reload("checkout-service")
    time.sleep(0.5)

    assert sm.check_health("checkout-service")["ready"] is True
    assert sm.check_synthetic("checkout")["passed"]
    scenario.reset()


def test_rollback_path(platform):
    """Apply a deliberately-bad patch, verify fails, restore backup, recheck."""
    h = sm.config_hash("checkout-service")["content_hash"]
    bad = sm.config_patch("checkout-service", {"inventory_url": "http://127.0.0.1:8099"}, h)
    sm.service_reload("checkout-service")
    time.sleep(0.5)
    assert not sm.check_synthetic("checkout")["passed"]

    sm.config_restore("checkout-service", bad["backup_id"])
    sm.service_reload("checkout-service")
    time.sleep(0.5)
    assert sm.check_health("checkout-service")["ready"] is True
    assert sm.check_synthetic("checkout")["passed"]


def test_policy_guards(platform):
    """Stale hash and non-allowlisted fields are rejected by config_patch."""
    from demo_platform.common.config_store import StaleHashError, UnknownFieldError

    h = sm.config_hash("checkout-service")["content_hash"]
    with pytest.raises(StaleHashError):
        sm.config_patch("checkout-service", {"inventory_url": "x"}, "deadbeef")
    with pytest.raises(UnknownFieldError):
        sm.config_patch("checkout-service", {"secret_sauce": "x"}, h)
    with pytest.raises(KeyError):  # unknown service
        sm.config_patch("billing-service", {"x": 1}, h)


def test_ground_truth_hidden_from_ops_surface(platform):
    """Verifier ground truth exists but is outside every allowlisted read path."""
    vt = paths.verifier_dir()
    vt.mkdir(parents=True, exist_ok=True)
    (vt / "ground_truth.json").write_text(json.dumps({"secret": "answer"}))
    # The ops surface cannot enumerate or read .verifier — it has no op for it.
    listed = sm.logs_large("checkout-service", min_mb=0)
    assert all(".verifier" not in f["path"] for f in listed["files"])
    (vt / "ground_truth.json").unlink()
