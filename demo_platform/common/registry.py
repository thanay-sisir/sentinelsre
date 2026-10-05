"""Standalone loader for configs/services.yaml.

Kept dependency-free of sre_agent so the platform can run inside a Harbor
sandbox on its own.
"""

from __future__ import annotations

from typing import Any

import yaml

from demo_platform.common import paths


def load_registry() -> dict[str, Any]:
    path = paths.services_yaml()
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "services" not in data:
        raise RuntimeError(f"invalid service registry: {path}")
    return data


def service_names() -> list[str]:
    return list(load_registry()["services"])


def service_def(name: str) -> dict[str, Any]:
    services = load_registry()["services"]
    if name not in services:
        raise KeyError(f"unknown service: {name}")
    return {"name": name, **services[name]}


def synthetic_checks() -> dict[str, Any]:
    return load_registry().get("synthetic_checks", {})
