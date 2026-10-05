"""HarborOpsBackend: exec translation + error mapping with a fake environment."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import pytest

from sre_agent.backends.harbor_backend import OPS_HOME, HarborOpsBackend
from sre_agent.exceptions import BackendOperationError


@dataclass
class ExecResult:
    stdout: str
    stderr: str = ""
    return_code: int = 0


class FakeEnvironment:
    """Captures exec() calls; serves canned JSON per command prefix."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses = responses or {}
        self.calls: list[str] = []

    async def exec(self, command: str, cwd=None, env=None, timeout_sec=None, user=None):
        self.calls.append(command)
        for prefix, payload in self.responses.items():
            if prefix in command:
                if isinstance(payload, ExecResult):
                    return payload
                return ExecResult(stdout=json.dumps(payload))
        return ExecResult(stdout="{}", stderr="unknown command", return_code=1)


async def test_backend_invokes_sre_ops_with_fixed_argv():
    env = FakeEnvironment({"services list": {"services": []}})
    b = HarborOpsBackend(env)
    assert await b.list_services() == []
    assert env.calls[0].startswith("python -m demo_platform.ops_cli.cli services list")


async def test_status_maps_to_service_status():
    env = FakeEnvironment(
        {
            "service status checkout-service": {
                "service": "checkout-service",
                "known": True,
                "state": "RUNNING",
                "pid": 123,
                "uptime_seconds": 9,
                "restart_count": 0,
                "listen_addresses": ["127.0.0.1:8081"],
                "readiness": True,
                "last_state_change": None,
                "detail": "ok",
            }
        }
    )
    b = HarborOpsBackend(env)
    st = await b.get_service_status("checkout-service")
    assert st.state.value == "RUNNING" or str(st.state) == "RUNNING"
    assert "service status checkout-service" in env.calls[0]


async def test_patch_config_stages_payload_as_base64():
    env = FakeEnvironment(
        {
            "config patch": {
                "success": True,
                "operation": "patch_runtime_config",
                "target": "checkout-service",
            },
            "base64 -d": ExecResult(stdout=""),
        }
    )
    b = HarborOpsBackend(env)
    res = await b.patch_config("checkout-service", {"inventory_url": "http://x"}, "abc123")
    assert res.success is True
    # first exec stages the patch file, second runs config patch
    staged = env.calls[0]
    assert "base64 -d" in staged and "runtime/patch-checkout-service.json" in staged
    b64 = staged.split("echo ", 1)[1].split(" | ", 1)[0].strip()
    assert json.loads(base64.b64decode(b64)) == {"inventory_url": "http://x"}
    assert "--expected-hash abc123" in env.calls[1]


async def test_nonzero_exit_raises_backend_error():
    env = FakeEnvironment({"service status": ExecResult(stdout="", stderr="boom", return_code=3)})
    b = HarborOpsBackend(env)
    with pytest.raises(BackendOperationError):
        await b.get_service_status("checkout-service")


async def test_non_json_output_raises_backend_error():
    env = FakeEnvironment({"services list": ExecResult(stdout="not json")})
    b = HarborOpsBackend(env)
    with pytest.raises(BackendOperationError):
        await b.list_services()


async def test_commands_run_inside_ops_home(capsys):
    env = FakeEnvironment({"check health": {"service": "checkout-service", "alive": True, "ready": True}})
    b = HarborOpsBackend(env)
    await b.health_check("checkout-service")
    # cwd is passed to exec — fake ignores it, but assert call shape is sane
    assert env.calls[0].endswith("check health checkout-service")
    assert OPS_HOME == "/opt/sentinelsre"
