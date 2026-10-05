"""Fault injector for the `wrong-inventory-endpoint` scenario.

Replaces checkout-service's inventory_url with a dead endpoint after backing
up the pristine config and recording hidden ground truth under
runtime/.verifier/ (invisible to agent tools — the verifier reads it directly).

Usage:
    python -m demo_platform.scenarios.wrong_inventory_endpoint inject
    python -m demo_platform.scenarios.wrong_inventory_endpoint reset
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from typing import Any

from demo_platform.common import config_store, paths

SCENARIO_ID = "wrong-inventory-endpoint"
BAD_URL = "http://127.0.0.1:8099"  # dead port — nothing listens here


def _write_ground_truth(expected_url: str) -> None:
    vt = paths.verifier_dir()
    vt.mkdir(parents=True, exist_ok=True)
    (vt / "ground_truth.json").write_text(
        json.dumps(
            {
                "scenario_id": SCENARIO_ID,
                "injected_fault": "checkout-service config inventory_url -> dead port",
                "field": "inventory_url",
                "injected_value": BAD_URL,
                "expected_value": expected_url,
                "injected_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def inject() -> dict[str, Any]:
    paths.ensure_dirs()
    config_store.seed_defaults()
    cfg = config_store.load("checkout-service")
    expected_url = cfg.get("inventory_url")
    # stash pristine config for reset + verifier
    vt = paths.verifier_dir()
    vt.mkdir(parents=True, exist_ok=True)
    (vt / "pristine_checkout-service.json").write_text(
        json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8"
    )
    cfg["inventory_url"] = BAD_URL
    config_store.write("checkout-service", cfg)
    _write_ground_truth(str(expected_url))
    # Hot-reload if the service is already running (best effort).
    reloaded = False
    try:
        from demo_platform.ops_cli import service_manager as sm

        st = sm.service_status("checkout-service")
        if st["pid"]:
            sm.service_reload("checkout-service")
            reloaded = True
    except Exception:
        reloaded = False
    return {
        "scenario": SCENARIO_ID,
        "injected": True,
        "reloaded": reloaded,
        "note": "checkout-service inventory_url now points at a dead port",
    }


def reset() -> dict[str, Any]:
    vt = paths.verifier_dir()
    pristine = vt / "pristine_checkout-service.json"
    restored = False
    if pristine.exists():
        data = json.loads(pristine.read_text(encoding="utf-8"))
        config_store.write("checkout-service", data)
        pristine.unlink()
        restored = True
        try:
            from demo_platform.ops_cli import service_manager as sm

            st = sm.service_status("checkout-service")
            if st["pid"]:
                sm.service_reload("checkout-service")
        except Exception as exc:  # noqa: BLE001 — reload is best-effort on reset
            print(f"reset: reload skipped: {exc}", file=sys.stderr)
    gt = vt / "ground_truth.json"
    if gt.exists():
        gt.unlink()
    return {"scenario": SCENARIO_ID, "reset": True, "config_restored": restored}


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    t0 = time.time()
    if cmd == "inject":
        out = inject()
    elif cmd == "reset":
        out = reset()
    else:
        print(json.dumps({"ok": False, "usage": "inject|reset"}), file=sys.stderr)
        sys.exit(1)
    out["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
