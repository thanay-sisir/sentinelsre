"""order-worker: drains runtime/queue/orders.jsonl in the background.

Reports liveness via a heartbeat file (runtime/state.json is supervisor-owned,
so the worker writes runtime/queue/worker_heartbeat.json). Scenario 2 stops
this process while the web APIs stay healthy.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from demo_platform.common import config_store, paths
from demo_platform.common.logging_setup import JsonlLogger
from demo_platform.common.metrics import MetricsRegistry

SERVICE = "order-worker"
logger = JsonlLogger(SERVICE)
metrics = MetricsRegistry()

QUEUE = "orders.jsonl"
HEARTBEAT = "worker_heartbeat.json"


def _queue_path():
    return paths.queue_dir() / QUEUE


def queue_depth() -> int:
    p = _queue_path()
    if not p.exists():
        return 0
    with p.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _drain(batch_size: int) -> int:
    """Remove up to batch_size lines from the queue file. Returns count."""
    p = _queue_path()
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    pending = [line for line in lines if line.strip()]
    take = pending[:batch_size]
    rest = pending[batch_size:]
    p.write_text("".join(rest), encoding="utf-8")
    return len(take)


def _write_heartbeat() -> None:
    hb = {
        "service": SERVICE,
        "ts": datetime.now(UTC).isoformat(),
        "queue_depth": queue_depth(),
        "processed_total": metrics.get("processed"),
    }
    (paths.queue_dir() / HEARTBEAT).write_text(json.dumps(hb, indent=2), encoding="utf-8")


def main() -> None:
    paths.ensure_dirs()
    config_store.seed_defaults()
    cfg = config_store.load(SERVICE)
    poll_s = float(cfg.get("poll_interval_ms", 500)) / 1000.0
    batch = int(cfg.get("batch_size", 5))
    logger.info("worker_started", f"polling every {poll_s}s, batch={batch}")
    while True:
        processed = _drain(batch)
        if processed:
            metrics.inc("processed", processed)
            logger.info(
                "orders_processed",
                f"processed {processed} orders",
                queue_depth=queue_depth(),
            )
        _write_heartbeat()
        time.sleep(poll_s)


if __name__ == "__main__":
    main()
