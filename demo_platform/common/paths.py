"""Path resolution for the demo platform.

The platform is standalone (runs on a dev host or inside a Harbor sandbox
without sre_agent installed). All state lives under a runtime dir that is
derived from SRE_PLATFORM_ROOT / SRE_RUNTIME_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path


def platform_root() -> Path:
    return Path(os.environ.get("SRE_PLATFORM_ROOT", ".")).resolve()


def runtime_dir() -> Path:
    raw = os.environ.get("SRE_RUNTIME_DIR")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (platform_root() / p).resolve()
    return platform_root() / "runtime"


def services_yaml() -> Path:
    raw = os.environ.get("SRE_SERVICE_CONFIG_FILE")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (platform_root() / p).resolve()
    return platform_root() / "configs" / "services.yaml"


def runbooks_dir() -> Path:
    return platform_root() / "knowledge" / "runbooks"


def pids_dir() -> Path:
    return runtime_dir() / "pids"


def logs_dir() -> Path:
    return runtime_dir() / "logs"


def config_dir() -> Path:
    return runtime_dir() / "config"


def backups_dir() -> Path:
    return runtime_dir() / "backups"


def queue_dir() -> Path:
    return runtime_dir() / "queue"


def verifier_dir() -> Path:
    """Hidden ground-truth dir — never readable through agent tools."""
    return runtime_dir() / ".verifier"


def audit_log() -> Path:
    return runtime_dir() / "audit.jsonl"


def state_file() -> Path:
    return runtime_dir() / "state.json"


def ensure_dirs() -> None:
    for d in (
        runtime_dir(),
        pids_dir(),
        logs_dir(),
        config_dir(),
        backups_dir(),
        queue_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
