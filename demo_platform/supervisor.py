"""Supervisor module: `python -m demo_platform.supervisor up|down|status`.

`up --block` runs the platform in the foreground: spawned service processes
are children of this supervisor and live as long as it does. This is the
recommended way to run the demo locally (keep it in its own terminal, Ctrl+C
to stop everything). Detached children are unreliable on Windows dev hosts
where the spawning shell may reap its process tree.
"""

from __future__ import annotations

import json
import sys
import time

from demo_platform.ops_cli import service_manager as sm


def _up(block: bool) -> None:
    print(json.dumps(sm.platform_up(), indent=2, default=str))
    if not block:
        return
    print("platform running in foreground; Ctrl+C to stop", file=sys.stderr)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print(json.dumps(sm.platform_down(), indent=2, default=str))


def main() -> None:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    if cmd == "up":
        _up(block="--block" in argv)
    elif cmd == "down":
        print(json.dumps(sm.platform_down(), indent=2, default=str))
    elif cmd == "status":
        print(json.dumps(sm.platform_status(), indent=2, default=str))
    else:
        print(
            json.dumps({"ok": False, "usage": "up [--block]|down|status"}),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
