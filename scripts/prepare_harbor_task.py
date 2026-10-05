"""Stage repo sources into a Harbor task's environment/payload/ dir.

Harbor builds environment/ as the Docker context, so the demo platform,
service registry, and runbooks must be physically copied in. Run before
`harbor run`:

    uv run python scripts/prepare_harbor_task.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "harbor" / "tasks"
SOURCES = ("demo_platform", "configs", "knowledge")


def stage(task_dir: Path) -> None:
    payload = task_dir / "environment" / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    for name in SOURCES:
        src = REPO / name
        dst = payload / name
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    # Runtime state must be fresh in the image — never ship local state.
    runtime = payload / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    print(f"staged payload -> {payload}")


def main() -> int:
    tasks = [d for d in TASKS.iterdir() if (d / "task.toml").exists()]
    if not tasks:
        print(f"no tasks found under {TASKS}", file=sys.stderr)
        return 1
    for task_dir in tasks:
        stage(task_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
