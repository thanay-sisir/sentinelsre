"""LangSmith tracing wiring: env setup and run-tree correlation."""

import os

import pytest
from pydantic import SecretStr

from sre_agent.config import Settings
from sre_agent.runner import _configure_langsmith


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_PROJECT",
        "LANGSMITH_WORKSPACE_ID",
    ):
        monkeypatch.delenv(var, raising=False)


def test_langsmith_disabled_without_key():
    s = Settings(_env_file=None)
    assert s.langsmith_api_key is None
    assert not s.langsmith_enabled()
    _configure_langsmith(s, "inc-x")
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_langsmith_enabled_sets_env():
    s = Settings(_env_file=None, langsmith_api_key=SecretStr("lsv2-test"))
    assert s.langsmith_enabled()
    _configure_langsmith(s, "inc-x")
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2-test"
    assert os.environ["LANGSMITH_PROJECT"] == "sentinelsre-agent-traces"


def test_langsmith_env_not_clobbered(monkeypatch):
    monkeypatch.setenv("LANGSMITH_PROJECT", "preexisting-project")
    s = Settings(_env_file=None, langsmith_api_key=SecretStr("lsv2-test"))
    _configure_langsmith(s, "inc-x")
    assert os.environ["LANGSMITH_PROJECT"] == "preexisting-project"


def test_capture_trace_metadata_noop_without_tracing():
    from sre_agent.graph.nodes.investigate import _capture_trace_metadata
    from sre_agent.models.incident import IncidentState, IncidentStatus

    st = IncidentState(incident_id="inc-trace-test", status=IncidentStatus.RECEIVED)
    _capture_trace_metadata(st)  # must not raise, no tracing active
    # Either absent entirely or populated — never raises.
    assert isinstance(st.trace_metadata, dict)
