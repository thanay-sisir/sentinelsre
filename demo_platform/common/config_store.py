"""Runtime config files for demo services: read/write/hash/backup.

Config lives at runtime/config/<service>.json. Hashing is over canonical
JSON (sorted keys) so it is stable across formatting.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from demo_platform.common import paths, registry


class ConfigStoreError(Exception):
    pass


class StaleHashError(ConfigStoreError):
    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"stale config hash: expected {expected}, actual {actual}")


class UnknownFieldError(ConfigStoreError):
    pass


def config_path(service: str) -> Path:
    svc = registry.service_def(service)  # raises KeyError for unknown
    return paths.config_dir() / svc["config_file"]


def load(service: str) -> dict[str, Any]:
    path = config_path(service)
    if not path.exists():
        raise ConfigStoreError(f"config file missing for {service}: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def content_hash(service: str) -> str:
    data = load(service)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def write(service: str, data: dict[str, Any]) -> None:
    """Atomic write via tmp + rename."""
    path = config_path(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def backup(service: str) -> str:
    """Copy current config to backups dir; returns backup_id."""
    paths.ensure_dirs()
    src = config_path(service)
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    backup_id = f"{service}-{ts}-{content_hash(service)[:8]}"
    dst = paths.backups_dir() / f"{backup_id}.json"
    dst.write_bytes(src.read_bytes())
    return backup_id


def backup_path(backup_id: str) -> Path:
    # Guard against traversal in backup ids.
    if not backup_id or any(c in backup_id for c in ("/", "\\", "..")):
        raise ConfigStoreError(f"invalid backup_id: {backup_id!r}")
    p = paths.backups_dir() / f"{backup_id}.json"
    if not p.exists():
        raise ConfigStoreError(f"backup not found: {backup_id}")
    return p


def patch(service: str, changes: dict[str, Any], expected_hash: str) -> tuple[str, dict[str, Any], str, str]:
    """Validate + apply a patch. Returns (backup_id, new_data, prev_hash, new_hash).

    Raises StaleHashError / UnknownFieldError / ConfigStoreError.
    """
    svc = registry.service_def(service)
    patchable = set(svc.get("patchable_fields") or [])
    bad = [k for k in changes if k not in patchable]
    if bad:
        raise UnknownFieldError(
            f"fields not patchable on {service}: {sorted(bad)} (allowed: {sorted(patchable)})"
        )
    current = load(service)
    prev_hash = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    if expected_hash and expected_hash != prev_hash:
        raise StaleHashError(expected_hash, prev_hash)
    backup_id = backup(service)
    current.update(changes)
    write(service, current)
    new_hash = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    return backup_id, current, prev_hash, new_hash


def restore(service: str, backup_id: str) -> tuple[str, str]:
    """Restore config from a backup. Returns (prev_hash, new_hash)."""
    src = backup_path(backup_id)
    prev_hash = content_hash(service) if config_path(service).exists() else ""
    data = json.loads(src.read_text(encoding="utf-8"))
    write(service, data)
    new_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return prev_hash, new_hash


DEFAULT_CONFIGS: dict[str, dict[str, Any]] = {
    "checkout-service": {
        "inventory_url": "http://127.0.0.1:8082",
        "request_timeout_ms": 2000,
    },
    "inventory-service": {"request_timeout_ms": 2000},
    "order-worker": {"poll_interval_ms": 500, "batch_size": 5},
}


def seed_defaults() -> None:
    """Write default configs for any service whose file is missing."""
    paths.ensure_dirs()
    for name, data in DEFAULT_CONFIGS.items():
        path = config_path(name)
        if not path.exists():
            write(name, data)
