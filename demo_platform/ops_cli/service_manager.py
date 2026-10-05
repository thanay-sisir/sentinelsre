"""Allowlisted operations on the demo platform.

This module is the single implementation behind both the `sre-ops` CLI (used
inside Harbor sandboxes) and sre_agent.backends.local_process. Every function
returns JSON-serializable dicts; errors are raised as OpsError subclasses.

Security invariants enforced here:
  * service names must exist in configs/services.yaml
  * config patches only touch `patchable_fields`
  * file operations only under runtime/ allowlisted dirs
  * subprocesses spawned with shell=False, fixed argv, timeouts
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import psutil

from demo_platform.common import config_store, paths, registry
from demo_platform.common.config_store import (
    ConfigStoreError,
    StaleHashError,
    UnknownFieldError,
)


class OpsError(Exception):
    """Operation failed; message is safe to show to the agent."""


class ServiceNotRunningError(OpsError):
    pass


LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
MAX_LOG_LIMIT = 500


# ---------------------------------------------------------------------------
# state.json + process helpers
# ---------------------------------------------------------------------------


def _load_state() -> dict[str, Any]:
    sf = paths.state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"services": {}}


def _save_state(state: dict[str, Any]) -> None:
    paths.ensure_dirs()
    tmp = paths.state_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, paths.state_file())


def _svc_state(state: dict[str, Any], name: str) -> dict[str, Any]:
    return state.setdefault("services", {}).setdefault(
        name,
        {"pid": None, "started_at": None, "restart_count": 0, "last_state_change": None},
    )


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _audit(op: str, target: str, params: dict[str, Any], ok: bool, detail: str) -> None:
    paths.ensure_dirs()
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "actor": "sre-ops",
        "op": op,
        "target": target,
        "params": params,
        "ok": ok,
        "detail": detail,
    }
    with paths.audit_log().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _spawn(name: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    module = svc["module"].split(":")[0]
    paths.ensure_dirs()
    console_log = (paths.logs_dir() / f"{name}.console.log").open("a", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(paths.platform_root())}
    kwargs: dict[str, Any] = {"stdout": console_log, "stderr": subprocess.STDOUT, "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, shell=False
        [sys.executable, "-m", module], cwd=paths.platform_root(), **kwargs
    )
    state = _load_state()
    entry = _svc_state(state, name)
    entry["pid"] = proc.pid
    entry["started_at"] = datetime.now(UTC).isoformat()
    entry["last_state_change"] = entry["started_at"]
    _save_state(state)
    return {"pid": proc.pid}


def _kill(name: str, timeout: float = 10.0) -> bool:
    """Terminate a service process. Returns True if it is gone."""
    state = _load_state()
    entry = _svc_state(state, name)
    pid = entry.get("pid")
    if not _pid_alive(pid):
        entry["pid"] = None
        _save_state(state)
        return True
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    entry["pid"] = None
    entry["last_state_change"] = datetime.now(UTC).isoformat()
    _save_state(state)
    return True


# ---------------------------------------------------------------------------
# Discovery + status
# ---------------------------------------------------------------------------


def services_list() -> dict[str, Any]:
    reg = registry.load_registry()
    out = []
    for name, svc in reg["services"].items():
        out.append(
            {
                "name": name,
                "kind": svc["kind"],
                "port": svc.get("port"),
                "description": svc.get("description", ""),
                "dependencies": svc.get("dependencies", []),
                "listen_address": (f"http://{svc['host']}:{svc['port']}" if svc.get("port") else None),
            }
        )
    return {"services": out}


def _heartbeat_fresh(name: str, max_age_s: float = 30.0) -> bool | None:
    hb_path = paths.queue_dir() / "worker_heartbeat.json"
    if not hb_path.exists():
        return None
    try:
        hb = json.loads(hb_path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(hb["ts"])
        return (datetime.now(UTC) - ts).total_seconds() < max_age_s
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def service_status(name: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    state = _load_state()
    entry = _svc_state(state, name)
    pid = entry.get("pid")
    alive = _pid_alive(pid)

    readiness: bool | None = None
    detail = ""
    uptime = None
    if alive and entry.get("started_at"):
        try:
            started = datetime.fromisoformat(entry["started_at"])
            uptime = round((datetime.now(UTC) - started).total_seconds(), 1)
        except ValueError:
            uptime = None

    if alive and svc["kind"] == "web":
        readiness = _probe_ready(svc)
        detail = "listening" if readiness else "running but not ready"
        svc_state = "RUNNING" if readiness else "UNHEALTHY"
    elif alive:
        hb = _heartbeat_fresh(name)
        readiness = hb if hb is not None else True
        svc_state = "RUNNING" if readiness else "UNHEALTHY"
        detail = "worker heartbeat " + ("fresh" if readiness else "stale/missing")
    else:
        svc_state = "STOPPED" if pid is None else "UNHEALTHY"
        detail = "no live process" if pid is None else f"pid {pid} dead"
        if pid is not None and not alive:
            readiness = False

    return {
        "service": name,
        "known": True,
        "state": svc_state,
        "pid": pid if alive else None,
        "uptime_seconds": uptime,
        "restart_count": entry.get("restart_count", 0),
        "listen_addresses": ([f"{svc['host']}:{svc['port']}"] if svc.get("port") and alive else []),
        "readiness": readiness,
        "last_state_change": entry.get("last_state_change"),
        "detail": detail,
    }


def _probe_ready(svc: dict[str, Any]) -> bool:
    if not svc.get("ready_path") or not svc.get("port"):
        return False
    url = f"http://{svc['host']}:{svc['port']}{svc['ready_path']}"
    try:
        resp = httpx.get(url, timeout=2.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------
# Service lifecycle ops
# ---------------------------------------------------------------------------


def service_start(name: str) -> dict[str, Any]:
    registry.service_def(name)
    status = service_status(name)
    if status["state"] == "RUNNING":
        return {
            "success": True,
            "operation": "start",
            "target": name,
            "detail": "already running",
            "pid": status["pid"],
        }
    config_store.seed_defaults()
    info = _spawn(name)
    _audit("start", name, {}, True, f"pid={info['pid']}")
    return {"success": True, "operation": "start", "target": name, "detail": "started", "pid": info["pid"]}


def service_stop(name: str) -> dict[str, Any]:
    registry.service_def(name)
    _kill(name)
    _audit("stop", name, {}, True, "terminated")
    return {"success": True, "operation": "stop", "target": name, "detail": "stopped"}


def service_restart(name: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    if "restart" not in svc.get("mutating_ops", []):
        raise OpsError(f"restart not allowed on {name}")
    was_running = _pid_alive(_load_state().get("services", {}).get(name, {}).get("pid"))
    _kill(name)
    config_store.seed_defaults()
    info = _spawn(name)
    state = _load_state()
    entry = _svc_state(state, name)
    entry["restart_count"] = int(entry.get("restart_count", 0)) + 1
    _save_state(state)
    _audit("restart", name, {"was_running": was_running}, True, f"pid={info['pid']}")
    return {
        "success": True,
        "operation": "restart",
        "target": name,
        "detail": "restarted",
        "pid": info["pid"],
    }


def service_reload(name: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    if "reload" not in svc.get("mutating_ops", []):
        raise OpsError(f"reload not allowed on {name}")
    if svc["kind"] != "web":
        raise OpsError(f"{name} does not support reload; use restart")
    status = service_status(name)
    if not status["pid"]:
        raise ServiceNotRunningError(f"{name} is not running")
    url = f"http://{svc['host']}:{svc['port']}/admin/reload"
    try:
        resp = httpx.post(url, timeout=5.0)
    except httpx.HTTPError as exc:
        raise OpsError(f"reload call failed: {exc}") from exc
    ok = resp.status_code == 200
    _audit("reload", name, {}, ok, f"http={resp.status_code}")
    return {"success": ok, "operation": "reload", "target": name, "detail": f"reload http {resp.status_code}"}


# ---------------------------------------------------------------------------
# Logs / metrics / deps
# ---------------------------------------------------------------------------

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def logs_read(
    name: str,
    limit: int = 50,
    min_level: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    svc = registry.service_def(name)
    limit = max(1, min(int(limit), MAX_LOG_LIMIT))
    log_path = paths.logs_dir() / svc["log_file"]
    records: list[dict[str, Any]] = []
    if log_path.exists():
        min_ord = LEVEL_ORDER.get((min_level or "DEBUG").upper(), 0)
        q = query.lower() if query else None
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = {"level": "INFO", "message": line}
            if LEVEL_ORDER.get(str(rec.get("level", "INFO")).upper(), 1) < min_ord:
                continue
            if q and q not in json.dumps(rec).lower():
                continue
            rec["service"] = rec.get("service", name)
            records.append(rec)
    records = records[-limit:]
    return {
        "service": name,
        "count": len(records),
        "limit": limit,
        "records": records,
        "truncated": len(records) == limit,
    }


def metrics_get(name: str, window_minutes: int = 5) -> dict[str, Any]:
    svc = registry.service_def(name)
    status = service_status(name)
    out: dict[str, Any] = {
        "service": name,
        "window_minutes": window_minutes,
        "service_up": status["state"] == "RUNNING",
        "restart_count": status["restart_count"],
    }
    if svc["kind"] == "web" and status["pid"]:
        try:
            url = f"http://{svc['host']}:{svc['port']}{svc['metrics_path']}"
            resp = httpx.get(url, timeout=3.0)
            out.update(resp.json())
        except httpx.HTTPError as exc:
            out["metrics_error"] = str(exc)
    if name == "order-worker":
        hb_path = paths.queue_dir() / "worker_heartbeat.json"
        if hb_path.exists():
            try:
                hb = json.loads(hb_path.read_text(encoding="utf-8"))
                out["queue_depth"] = hb.get("queue_depth")
                out["processed_total"] = hb.get("processed_total")
            except json.JSONDecodeError:
                pass
    try:
        usage = shutil.disk_usage(paths.runtime_dir())
        out["disk_usage_percent"] = round(usage.used / usage.total * 100, 2)
    except OSError:
        pass
    q = paths.queue_dir() / "orders.jsonl"
    if q.exists() and name == "checkout-service":
        out["queue_depth"] = sum(1 for line in q.read_text(encoding="utf-8").splitlines() if line.strip())
    return out


def deps_status(name: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    deps = []
    cfg: dict[str, Any] = {}
    with contextlib.suppress(ConfigStoreError):
        cfg = config_store.load(name)
    for dep_name in svc.get("dependencies", []):
        dep = registry.service_def(dep_name)
        expected_url = f"http://{dep['host']}:{dep['port']}" if dep.get("port") else None
        # Convention: config field '<dep-short-name>_url' (inventory_url for
        # inventory-service).
        configured_url = cfg.get(f"{dep_name.split('-')[0]}_url")
        entry: dict[str, Any] = {
            "name": dep_name,
            "expected_url": expected_url,
            "configured_url": configured_url,
        }
        if expected_url:
            try:
                resp = httpx.get(f"{expected_url}/health", timeout=2.0)
                entry["expected_reachable"] = resp.status_code == 200
            except httpx.HTTPError:
                entry["expected_reachable"] = False
        if configured_url and configured_url != expected_url:
            try:
                resp = httpx.get(f"{configured_url}/health", timeout=2.0)
                entry["configured_reachable"] = resp.status_code == 200
            except httpx.HTTPError as exc:
                entry["configured_reachable"] = False
                entry["configured_error"] = f"{exc.__class__.__name__}"
        else:
            entry["configured_reachable"] = entry.get("expected_reachable")
        entry["urls_match"] = configured_url == expected_url
        deps.append(entry)
    return {"service": name, "dependencies": deps}


# ---------------------------------------------------------------------------
# Config ops
# ---------------------------------------------------------------------------

_SECRET_HINTS = ("key", "secret", "token", "password")


def _redact(service: str, values: dict[str, Any]) -> dict[str, Any]:
    svc = registry.service_def(service)
    secrets = set(svc.get("secret_fields") or [])
    out = {}
    for k, v in values.items():
        if k in secrets or any(h in k.lower() for h in _SECRET_HINTS):
            out[k] = "***REDACTED***"
        else:
            out[k] = v
    return out


def config_read(name: str, keys: list[str] | None = None) -> dict[str, Any]:
    registry.service_def(name)
    values = config_store.load(name)
    if keys:
        values = {k: values.get(k) for k in keys if k in values}
    return {
        "service": name,
        "values": _redact(name, values),
        "content_hash": config_store.content_hash(name),
    }


def config_hash(name: str) -> dict[str, Any]:
    registry.service_def(name)
    return {"service": name, "content_hash": config_store.content_hash(name)}


def config_patch(name: str, changes: dict[str, Any], expected_hash: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    if "patch_config" not in svc.get("mutating_ops", []):
        raise OpsError(f"patch_config not allowed on {name}")
    try:
        backup_id, new_data, prev_hash, new_hash = config_store.patch(name, changes, expected_hash)
    except StaleHashError:
        raise
    except UnknownFieldError:
        raise
    except ConfigStoreError as exc:
        raise OpsError(str(exc)) from exc
    _audit(
        "config_patch",
        name,
        {"fields": sorted(changes), "expected_hash": expected_hash},
        True,
        f"backup={backup_id}",
    )
    return {
        "success": True,
        "operation": "patch_config",
        "target": name,
        "backup_id": backup_id,
        "previous_hash": prev_hash,
        "new_hash": new_hash,
        "applied_fields": sorted(changes),
        "values": _redact(name, new_data),
    }


def config_restore(name: str, backup_id: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    if "restore_config" not in svc.get("mutating_ops", []):
        raise OpsError(f"restore_config not allowed on {name}")
    try:
        prev_hash, new_hash = config_store.restore(name, backup_id)
    except ConfigStoreError as exc:
        raise OpsError(str(exc)) from exc
    _audit("config_restore", name, {"backup_id": backup_id}, True, "restored")
    return {
        "success": True,
        "operation": "restore_config",
        "target": name,
        "previous_hash": prev_hash,
        "new_hash": new_hash,
    }


# ---------------------------------------------------------------------------
# Health / synthetic checks / disk / rotation
# ---------------------------------------------------------------------------


def check_health(name: str) -> dict[str, Any]:
    svc = registry.service_def(name)
    status = service_status(name)
    result: dict[str, Any] = {
        "service": name,
        "alive": status["state"] in ("RUNNING", "UNHEALTHY") and status["pid"] is not None,
        "ready": None,
        "detail": status["detail"],
    }
    if svc["kind"] == "web" and status["pid"]:
        t0 = time.perf_counter()
        for path, key in ((svc["health_path"], "health"), (svc["ready_path"], "ready")):
            try:
                resp = httpx.get(f"http://{svc['host']}:{svc['port']}{path}", timeout=3.0)
                result[key] = resp.status_code == 200
                result[f"{key}_status_code"] = resp.status_code
            except httpx.HTTPError as exc:
                result[key] = False
                result[f"{key}_error"] = str(exc)
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    elif svc["kind"] == "worker":
        result["ready"] = status["readiness"]
    return result


def check_synthetic(check_name: str) -> dict[str, Any]:
    checks = registry.synthetic_checks()
    if check_name not in checks:
        raise OpsError(f"unknown synthetic check: {check_name}")
    chk = checks[check_name]
    svc = registry.service_def(chk["service"])
    url = f"http://{svc['host']}:{svc['port']}/checkout"
    t0 = time.perf_counter()
    status_code: int | None = None
    try:
        resp = httpx.post(url, json=chk["payload"], timeout=5.0)
        status_code = resp.status_code
        passed = resp.status_code == 200
        detail = f"HTTP {resp.status_code}"
        try:
            detail += f" body={resp.json()}"
        except json.JSONDecodeError:
            detail += f" body={resp.text[:200]}"
    except httpx.HTTPError as exc:
        passed = False
        detail = f"{exc.__class__.__name__}: {exc}"
    return {
        "check_name": check_name,
        "passed": passed,
        "status_code": status_code,
        "detail": detail,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def disk_usage(scope: str = "runtime") -> dict[str, Any]:
    if scope != "runtime":
        raise OpsError(f"disk scope {scope!r} not allowlisted; only 'runtime'")
    usage = shutil.disk_usage(paths.runtime_dir())
    return {
        "scope": "runtime",
        "used_bytes": usage.used,
        "total_bytes": usage.total,
        "percent": round(usage.used / usage.total * 100, 2),
    }


def logs_large(name: str, min_mb: float = 1.0) -> dict[str, Any]:
    svc = registry.service_def(name)
    files = []
    for p in sorted(paths.logs_dir().glob(f"{name}*.jsonl")):
        size = p.stat().st_size
        if size >= min_mb * 1024 * 1024:
            files.append(
                {
                    "path": p.name,
                    "size_bytes": size,
                    "modified_at": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(),
                }
            )
    return {"service": svc["name"], "min_mb": min_mb, "files": files}


def logs_rotate(name: str, keep: int = 3) -> dict[str, Any]:
    svc = registry.service_def(name)
    if "rotate_logs" not in svc.get("mutating_ops", []):
        raise OpsError(f"rotate_logs not allowed on {name}")
    active = paths.logs_dir() / svc["log_file"]
    rotated: list[str] = []
    if active.exists():
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        dst = active.with_name(f"{name}.{ts}.jsonl")
        shutil.move(str(active), str(dst))
        rotated.append(dst.name)
        active.touch()
    # prune old rotations, newest first
    olds = sorted(
        paths.logs_dir().glob(f"{name}.2*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    pruned = []
    for old in olds[keep:]:
        old.unlink()
        pruned.append(old.name)
    _audit("rotate_logs", name, {"keep": keep}, True, f"rotated={rotated} pruned={pruned}")
    return {
        "success": True,
        "operation": "rotate_logs",
        "target": name,
        "rotated": rotated,
        "pruned": pruned,
    }


# ---------------------------------------------------------------------------
# Runbook search (keyword/BM25-lite over knowledge/runbooks/*.md)
# ---------------------------------------------------------------------------


def runbooks_search(query: str, limit: int = 3, excerpt_chars: int = 400) -> dict:
    """Score runbook sections by keyword overlap. No vector DB needed."""
    rb_dir = paths.runbooks_dir()
    terms = {t for t in re.findall(r"[a-z0-9_-]+", query.lower()) if len(t) > 2}
    results = []
    if rb_dir.exists() and terms:
        for md in sorted(rb_dir.glob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace")
            # split into headed sections
            sections = re.split(r"(?m)^(#{1,3} .*)$", text)
            chunks = []
            for i, part in enumerate(sections):
                if part.startswith("#"):
                    body = sections[i + 1] if i + 1 < len(sections) else ""
                    chunks.append((part.strip("# \n"), body))
                elif i == 0 and part.strip():
                    chunks.append(("introduction", part))
            for heading, body in chunks:
                hay = f"{heading} {body}".lower()
                hits = sum(1 for t in terms if t in hay)
                if hits:
                    score = hits / len(terms)
                    excerpt = re.sub(r"\s+", " ", body).strip()[:excerpt_chars]
                    results.append(
                        {
                            "runbook": md.name,
                            "section": heading,
                            "excerpt": excerpt,
                            "score": round(score, 3),
                        }
                    )
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"query": query, "results": results[:limit]}


# ---------------------------------------------------------------------------
# Platform bring-up / teardown (used by sentinelsre demo + Harbor start.sh)
# ---------------------------------------------------------------------------


def platform_up(wait_ready_s: float = 15.0) -> dict[str, Any]:
    paths.ensure_dirs()
    config_store.seed_defaults()
    started = []
    for name in registry.service_names():
        status = service_status(name)
        if status["state"] != "RUNNING":
            _spawn(name)
            started.append(name)
    # bounded polling for readiness (no blind sleeps)
    deadline = time.monotonic() + wait_ready_s
    readiness: dict[str, Any] = {}
    while time.monotonic() < deadline:
        all_ready = True
        for name in registry.service_names():
            st = service_status(name)
            ok = st["state"] == "RUNNING" and (st["readiness"] in (True, None))
            readiness[name] = {"state": st["state"], "ready": st["readiness"]}
            all_ready = all_ready and ok
        if all_ready:
            break
        time.sleep(0.3)
    return {"started": started, "readiness": readiness}


def platform_down() -> dict[str, Any]:
    stopped = []
    for name in registry.service_names():
        if _kill(name):
            stopped.append(name)
    _audit("platform_down", "all", {}, True, f"stopped={stopped}")
    return {"stopped": stopped}


def platform_status() -> dict[str, Any]:
    return {name: service_status(name) for name in registry.service_names()}
