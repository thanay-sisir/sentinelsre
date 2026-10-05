"""inventory-service: returns product availability. Always healthy in
scenario 1 — the fault lives in checkout's config, not here."""

from __future__ import annotations

import time

import uvicorn
from fastapi import FastAPI

from demo_platform.common import registry
from demo_platform.common.logging_setup import JsonlLogger
from demo_platform.common.metrics import MetricsRegistry

INVENTORY = {
    "SKU-1001": {"sku": "SKU-1001", "name": "Mechanical keyboard", "stock": 42},
    "SKU-1002": {"sku": "SKU-1002", "name": "USB-C hub", "stock": 7},
    "SKU-1003": {"sku": "SKU-1003", "name": "Webcam 1080p", "stock": 0},
}

logger = JsonlLogger("inventory-service")
metrics = MetricsRegistry()
app = FastAPI(title="inventory-service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "inventory-service"}


@app.get("/ready")
def ready() -> dict:
    return {"status": "ready", "service": "inventory-service"}


@app.get("/inventory/{sku}")
def get_inventory(sku: str) -> dict:
    start = time.perf_counter()
    metrics.inc("request_count")
    item = INVENTORY.get(sku)
    if item is None:
        metrics.inc("error_count")
        logger.warning("sku_not_found", f"sku {sku} not in catalog", sku=sku)
        return {"sku": sku, "in_stock": False, "stock": 0}
    logger.info("inventory_lookup", f"lookup {sku}", sku=sku, stock=item["stock"])
    metrics.observe_latency((time.perf_counter() - start) * 1000)
    return {**item, "in_stock": item["stock"] > 0}


@app.get("/metrics")
def get_metrics() -> dict:
    return metrics.snapshot("inventory-service")


def main() -> None:
    svc = registry.service_def("inventory-service")
    uvicorn.run(app, host=svc["host"], port=svc["port"], log_level="warning")


if __name__ == "__main__":
    main()
