"""In-memory metrics registry for demo services."""

from __future__ import annotations

import threading
import time
from collections import deque


class MetricsRegistry:
    """Counters + recent latencies; cheap and deterministic."""

    def __init__(self, max_latency_samples: int = 512, max_events: int = 8192) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        # Timestamped request/error events -> true windowed rates (a rate
        # check right after a fix must see only post-fix traffic, not the
        # lifetime of the process that lived through the fault).
        self._events: deque[tuple[float, str]] = deque(maxlen=max_events)
        self._latencies: deque[float] = deque(maxlen=max_latency_samples)
        self._started = time.monotonic()

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount
            now = time.monotonic()
            for _ in range(amount):
                self._events.append((now, name))

    def get(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def observe_latency(self, ms: float) -> None:
        with self._lock:
            self._latencies.append(ms)

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started

    def snapshot(self, service: str, window_minutes: int = 5) -> dict:
        with self._lock:
            lat = sorted(self._latencies)
            cutoff = time.monotonic() - window_minutes * 60
            req = sum(1 for ts, n in self._events if n == "request_count" and ts >= cutoff)
            err = sum(1 for ts, n in self._events if n == "error_count" and ts >= cutoff)
            p95 = None
            if lat:
                idx = min(len(lat) - 1, int(0.95 * len(lat)))
                p95 = round(lat[idx], 2)
            return {
                "service": service,
                "window_minutes": window_minutes,
                "request_count": req,
                "error_count": err,
                "error_rate": round(err / req, 4) if req else 0.0,
                "p95_latency_ms": p95,
                "dependency_failure_count": self._counters.get("dependency_failure_count", 0),
                "uptime_seconds": round(self.uptime_seconds(), 1),
            }
