"""checkout-service: accepts checkouts, depends on inventory-service.

Reads runtime config (runtime/config/checkout-service.json) at startup and on
/admin/reload. All dependency calls go through the configured inventory_url —
that indirection is what scenario `wrong-inventory-endpoint` corrupts.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from demo_platform.common import config_store, paths, registry
from demo_platform.common.logging_setup import JsonlLogger
from demo_platform.common.metrics import MetricsRegistry

SERVICE = "checkout-service"
logger = JsonlLogger(SERVICE)
metrics = MetricsRegistry()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    config_store.seed_defaults()
    load_config()
    yield


app = FastAPI(title=SERVICE, lifespan=_lifespan)

_config: dict[str, Any] = {}


def load_config() -> dict[str, Any]:
    global _config
    try:
        _config = config_store.load(SERVICE)
        logger.info("config_loaded", "runtime config loaded", config=_config)
    except Exception as exc:  # config unreadable -> not ready, but stay up
        logger.error("config_load_failed", str(exc))
        _config = {}
    return _config


class CheckoutRequest(BaseModel):
    sku: str
    qty: int = 1


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE}


def _probe_inventory() -> tuple[bool, str]:
    """Check the configured inventory endpoint. Returns (reachable, detail)."""
    url = _config.get("inventory_url")
    if not url:
        return False, "inventory_url missing from runtime config"
    timeout = float(_config.get("request_timeout_ms", 2000)) / 1000.0
    try:
        resp = httpx.get(f"{url}/health", timeout=timeout)
        if resp.status_code == 200:
            return True, f"inventory reachable at {url}"
        return False, f"inventory at {url} returned HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"inventory at {url}: {exc.__class__.__name__}: {exc}"


@app.get("/ready")
def ready() -> JSONResponse:
    ok, detail = _probe_inventory()
    metrics.inc("request_count")
    if ok:
        return JSONResponse({"status": "ready", "service": SERVICE, "detail": detail})
    metrics.inc("error_count")
    logger.warning("readiness_failed", detail, dependency="inventory-service")
    return JSONResponse(
        {"status": "not_ready", "service": SERVICE, "detail": detail},
        status_code=503,
    )


@app.post("/checkout")
def checkout(req: CheckoutRequest) -> JSONResponse:
    start = time.perf_counter()
    request_id = uuid4().hex[:12]
    metrics.inc("request_count")
    url = _config.get("inventory_url")
    timeout = float(_config.get("request_timeout_ms", 2000)) / 1000.0
    logger.info(
        "checkout_received",
        f"checkout {req.sku} x{req.qty}",
        request_id=request_id,
        inventory_url=url,
    )
    try:
        resp = httpx.get(f"{url}/inventory/{req.sku}", timeout=timeout)
        resp.raise_for_status()
        inv = resp.json()
    except Exception as exc:
        metrics.inc("error_count")
        metrics.inc("dependency_failure_count")
        logger.error(
            "dependency_request_failed",
            f"inventory lookup failed for {req.sku}: {exc}",
            request_id=request_id,
            dependency="inventory-service",
            inventory_url=url,
            error=str(exc),
        )
        return JSONResponse(
            {
                "status": "failed",
                "error": "inventory_unavailable",
                "request_id": request_id,
            },
            status_code=503,
        )

    if not inv.get("in_stock"):
        metrics.inc("error_count")
        logger.warning("checkout_rejected", f"{req.sku} out of stock", request_id=request_id)
        return JSONResponse(
            {"status": "rejected", "reason": "out_of_stock", "request_id": request_id},
            status_code=409,
        )

    order_id = f"ord-{uuid4().hex[:10]}"
    paths.ensure_dirs()
    with (paths.queue_dir() / "orders.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "order_id": order_id,
                    "sku": req.sku,
                    "qty": req.qty,
                    "request_id": request_id,
                    "enqueued_at": time.time(),
                }
            )
            + "\n"
        )
    metrics.observe_latency((time.perf_counter() - start) * 1000)
    logger.info("checkout_completed", f"order {order_id} accepted", request_id=request_id)
    return JSONResponse({"status": "accepted", "order_id": order_id, "request_id": request_id})


@app.get("/metrics")
def get_metrics() -> dict:
    return metrics.snapshot(SERVICE)


@app.post("/admin/reload")
def reload_config() -> dict:
    cfg = load_config()
    return {"reloaded": True, "config_keys": sorted(cfg)}


def main() -> None:
    svc = registry.service_def(SERVICE)
    uvicorn.run(app, host=svc["host"], port=svc["port"], log_level="warning")


if __name__ == "__main__":
    main()
