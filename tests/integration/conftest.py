"""Shared integration fixtures: the real demo platform hosted in-process.

Services run as threads inside pytest (SRE_PLATFORM_INPROCESS=1) — detached
grandchildren get reaped by the job object on this Windows host, so real
subprocesses die mid-test. In-process uvicorn still binds the real ports, so
probes, synthetic checks, and /admin/reload behave identically.
"""

import os
from pathlib import Path

import pytest
from demo_platform.ops_cli import service_manager as sm


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    """Boot the whole platform in a throwaway runtime dir."""
    rt = tmp_path_factory.mktemp("runtime")
    os.environ["SRE_RUNTIME_DIR"] = str(rt)
    os.environ["SRE_PLATFORM_ROOT"] = str(Path.cwd())
    os.environ["SRE_PLATFORM_INPROCESS"] = "1"
    sm.platform_up(wait_ready_s=20.0)
    yield
    sm.platform_down()
    os.environ.pop("SRE_PLATFORM_INPROCESS", None)
