"""In-process service hosting for tests and constrained environments.

Spawns each demo service as a thread inside the calling process instead of a
detached child. Web services run a real uvicorn.Server bound to their normal
port, so HTTP probes, synthetic checks, and /admin/reload behave identically.
The worker runs main() on a thread with a cooperative stop flag.

Why this exists: on hosts with aggressive job-object/EDR reaping, detached
children are killed a few seconds after spawn. In-process hosting keeps the
platform deterministic under pytest (SRE_PLATFORM_INPROCESS=1). CLI `demo up`
still uses real subprocesses for realism.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import uvicorn

from demo_platform.common import paths


def inproc_enabled() -> bool:
    return os.environ.get("SRE_PLATFORM_INPROCESS", "") == "1"


@dataclass
class _Hosted:
    name: str
    thread: threading.Thread | None = None
    server: Any = None  # uvicorn.Server for web services
    stop_flag: Any = None  # module-level _STOP Event for workers
    meta: dict[str, Any] = field(default_factory=dict)


_HOSTED: dict[str, _Hosted] = {}
_HOSTED_PID = os.getpid()


def spawn(name: str, svc: dict[str, Any]) -> dict[str, Any]:
    """Start service `name` in a thread. svc is the registry entry."""
    paths.ensure_dirs()
    stop(name)  # idempotent
    if svc["kind"] == "web":
        return _spawn_web(name, svc)
    return _spawn_worker(name, svc)


def _spawn_web(name: str, svc: dict[str, Any]) -> dict[str, Any]:
    module = svc["module"].split(":")[0]
    attr = svc["module"].split(":")[1] if ":" in svc["module"] else "app"
    mod = importlib.import_module(module)
    app = getattr(mod, attr)
    cfg = uvicorn.Config(
        app,
        host=svc["host"],
        port=int(svc["port"]),
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(cfg)
    hosted = _Hosted(name=name, server=server)
    hosted.thread = threading.Thread(target=server.run, daemon=True, name=f"svc-{name}")
    hosted.thread.start()
    _HOSTED[name] = hosted
    return {"pid": _HOSTED_PID}


def _spawn_worker(name: str, svc: dict[str, Any]) -> dict[str, Any]:
    mod = importlib.import_module(svc["module"])
    hosted = _Hosted(name=name)
    hosted.thread = threading.Thread(target=mod.main, daemon=True, name=f"svc-{name}")
    hosted.thread.start()
    _HOSTED[name] = hosted
    return {"pid": _HOSTED_PID}


def is_alive(name: str) -> bool:
    h = _HOSTED.get(name)
    return bool(h and h.thread and h.thread.is_alive())


def stop(name: str, timeout: float = 8.0) -> bool:
    h = _HOSTED.pop(name, None)
    if not h or not h.thread:
        return True
    if h.server is not None:
        h.server.should_exit = True
        h.thread.join(timeout=timeout)
    else:
        with contextlib.suppress(Exception):
            mod = importlib.import_module("demo_platform.order_worker.worker")
            mod.request_stop()
        h.thread.join(timeout=timeout)
    return not h.thread.is_alive()


def stop_all() -> None:
    for name in list(_HOSTED):
        stop(name)
