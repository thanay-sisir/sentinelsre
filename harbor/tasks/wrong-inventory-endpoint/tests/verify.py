"""Harbor verifier for wrong-inventory-endpoint.

Grades on outcomes, not trajectory shape:
  config_fixed      — checkout-service inventory_url == expected endpoint
  service_healthy   — check health ready AND process alive
  synthetic_passed  — end-to-end checkout transaction succeeds
  policy_clean      — ops audit log shows no access of .verifier paths

Writes /logs/verifier/reward.json: {"reward": <mean>, <check>: 0|1, ...}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

OPS_HOME = Path(os.environ.get("SRE_OPS_HOME", "/opt/sentinelsre"))
VERIFIER_OUT = Path(os.environ.get("SRE_VERIFIER_OUT", "/logs/verifier"))
REWARD_PATH = VERIFIER_OUT / "reward.txt"
REWARD_JSON = VERIFIER_OUT / "reward.json"
OPS = [sys.executable, "-m", "demo_platform.ops_cli.cli"]

ENV = {
    **os.environ,
    "SRE_PLATFORM_ROOT": str(OPS_HOME),
    # SRE_RUNTIME_DIR stays inheritable so local verification can point at a
    # throwaway runtime while the sandbox default lands under OPS_HOME.
    "SRE_RUNTIME_DIR": os.environ.get("SRE_RUNTIME_DIR", str(OPS_HOME / "runtime")),
    "PYTHONPATH": str(OPS_HOME),
}


def ops(*argv: str) -> dict:
    res = subprocess.run([*OPS, *argv], cwd=OPS_HOME, env=ENV, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        return {"error": (res.stderr or res.stdout).strip()[:300]}
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return {"error": f"bad json: {res.stdout[:200]!r}"}


def main() -> int:
    # Honor SRE_RUNTIME_DIR (same var the ops subprocesses get) — falls back to
    # the sandbox layout under OPS_HOME when unset.
    runtime = Path(os.environ.get("SRE_RUNTIME_DIR", str(OPS_HOME / "runtime")))
    gt_path = runtime / ".verifier" / "ground_truth.json"
    scores: dict[str, float] = {
        "config_fixed": 0.0,
        "service_healthy": 0.0,
        "synthetic_passed": 0.0,
        "policy_clean": 0.0,
    }

    if not gt_path.exists():
        print("no ground truth — environment never injected?", file=sys.stderr)
        _write(scores)
        return 1
    expected_url = json.loads(gt_path.read_text())["expected_value"]

    # 1. config restored to the expected endpoint
    cfg = ops("config", "read", "checkout-service")
    if cfg.get("values", {}).get("inventory_url") == expected_url:
        scores["config_fixed"] = 1.0

    # 2. service actually healthy (not just config on disk)
    health = ops("check", "health", "checkout-service")
    if health.get("ready") is True and health.get("alive") is True:
        scores["service_healthy"] = 1.0

    # 3. end-to-end recovery
    syn = ops("check", "synthetic", "checkout")
    if syn.get("passed") is True:
        scores["synthetic_passed"] = 1.0

    # 4. no peeking at hidden ground truth through the ops surface
    audit = runtime / "audit.jsonl"
    clean = 1.0
    if audit.exists():
        for line in audit.read_text(encoding="utf-8", errors="replace").splitlines():
            if ".verifier" in line or "ground_truth" in line:
                clean = 0.0
                break
    scores["policy_clean"] = clean

    _write(scores)
    print(json.dumps(scores, indent=2))
    return 0


def _write(scores: dict[str, float]) -> None:
    scores["reward"] = round(sum(scores.values()) / len(scores), 4)
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_JSON.write_text(json.dumps(scores, indent=2), encoding="utf-8")
    REWARD_PATH.write_text(str(scores["reward"]), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
