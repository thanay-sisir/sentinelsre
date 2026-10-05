"""sre-ops: the narrow operations CLI the agent talks to.

Every command prints JSON to stdout and exits nonzero on failure. There is
deliberately no `exec`/`shell` command — the surface is fixed and typed.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

from demo_platform.common.config_store import ConfigStoreError
from demo_platform.ops_cli import service_manager as sm
from demo_platform.ops_cli.service_manager import OpsError

app = typer.Typer(name="sre-ops", help="SentinelSRE demo operations CLI")
service_app = typer.Typer(help="service lifecycle")
services_app = typer.Typer(help="service discovery")
logs_app = typer.Typer(help="log operations")
metrics_app = typer.Typer(help="metrics")
deps_app = typer.Typer(help="dependency status")
config_app = typer.Typer(help="runtime config")
check_app = typer.Typer(help="health/synthetic checks")
disk_app = typer.Typer(help="disk usage")
runbooks_app = typer.Typer(help="runbook search")
platform_app = typer.Typer(help="platform bring-up")

app.add_typer(service_app, name="service")
app.add_typer(services_app, name="services")
app.add_typer(logs_app, name="logs")
app.add_typer(metrics_app, name="metrics")
app.add_typer(deps_app, name="deps")
app.add_typer(config_app, name="config")
app.add_typer(check_app, name="check")
app.add_typer(disk_app, name="disk")
app.add_typer(runbooks_app, name="runbooks")
app.add_typer(platform_app, name="platform")


def _emit(fn, *args: Any, **kwargs: Any) -> None:
    try:
        result = fn(*args, **kwargs)
    except (OpsError, ConfigStoreError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise typer.Exit(code=1) from exc
    print(json.dumps(result, indent=2, default=str))


@app.command()
def version() -> None:
    _emit(lambda: {"sre_ops_version": 1, "schema": "1.0"})


@services_app.command("list")
def services_list() -> None:
    _emit(sm.services_list)


@service_app.command("status")
def service_status(name: str) -> None:
    _emit(sm.service_status, name)


@service_app.command("start")
def service_start(name: str) -> None:
    _emit(sm.service_start, name)


@service_app.command("stop")
def service_stop(name: str) -> None:
    _emit(sm.service_stop, name)


@service_app.command("restart")
def service_restart(name: str) -> None:
    _emit(sm.service_restart, name)


@service_app.command("reload")
def service_reload(name: str) -> None:
    _emit(sm.service_reload, name)


@logs_app.command("read")
def logs_read(
    name: str,
    limit: int = typer.Option(50, "--limit"),
    min_level: str | None = typer.Option(None, "--min-level"),
    query: str | None = typer.Option(None, "--query"),
) -> None:
    _emit(sm.logs_read, name, limit, min_level, query)


@logs_app.command("large")
def logs_large(name: str, min_mb: float = typer.Option(1.0, "--min-mb")) -> None:
    _emit(sm.logs_large, name, min_mb)


@logs_app.command("rotate")
def logs_rotate(name: str, keep: int = typer.Option(3, "--keep")) -> None:
    _emit(sm.logs_rotate, name, keep)


@metrics_app.command("get")
def metrics_get(name: str, window: int = typer.Option(5, "--window")) -> None:
    _emit(sm.metrics_get, name, window)


@deps_app.command("status")
def deps_status(name: str) -> None:
    _emit(sm.deps_status, name)


@config_app.command("read")
def config_read(name: str, keys: str | None = typer.Option(None, "--keys")) -> None:
    _emit(sm.config_read, name, keys.split(",") if keys else None)


@config_app.command("hash")
def config_hash(name: str) -> None:
    _emit(sm.config_hash, name)


@config_app.command("patch")
def config_patch(
    name: str,
    patch_file: str = typer.Option(..., "--patch-file"),
    expected_hash: str = typer.Option("", "--expected-hash"),
) -> None:
    try:
        with open(patch_file, encoding="utf-8") as fh:
            changes = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"bad patch file: {exc}"}), file=sys.stderr)
        raise typer.Exit(code=1) from exc
    if not isinstance(changes, dict):
        print(json.dumps({"ok": False, "error": "patch file must contain a JSON object"}), file=sys.stderr)
        raise typer.Exit(code=1)
    _emit(sm.config_patch, name, changes, expected_hash)


@config_app.command("restore")
def config_restore(
    name: str,
    backup_id: str = typer.Option(..., "--backup-id"),
) -> None:
    _emit(sm.config_restore, name, backup_id)


@check_app.command("health")
def check_health(name: str) -> None:
    _emit(sm.check_health, name)


@check_app.command("synthetic")
def check_synthetic(name: str) -> None:
    _emit(sm.check_synthetic, name)


@disk_app.command("usage")
def disk_usage(scope: str = typer.Option("runtime", "--scope")) -> None:
    _emit(sm.disk_usage, scope)


@runbooks_app.command("search")
def runbooks_search(query: str, limit: int = typer.Option(3, "--limit")) -> None:
    _emit(sm.runbooks_search, query, limit)


@platform_app.command("up")
def platform_up(
    block: bool = typer.Option(False, "--block", help="Stay in foreground; Ctrl+C stops all services."),
) -> None:
    _emit(sm.platform_up)
    if block:
        import time

        print("platform running in foreground; Ctrl+C to stop", file=sys.stderr)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            _emit(sm.platform_down)


@platform_app.command("down")
def platform_down() -> None:
    _emit(sm.platform_down)


@platform_app.command("status")
def platform_status() -> None:
    _emit(sm.platform_status)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
