"""Unit tests for settings and YAML registry loading."""

import pytest

from sre_agent.config import (
    Settings,
    load_policy_config,
    load_service_registry,
)
from sre_agent.exceptions import ConfigError, UnknownServiceError


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("SRE_LLM_PROVIDER", raising=False)
    s = Settings(_env_file=None)  # ignore any local .env
    assert s.sre_llm_provider == "xai"
    assert s.sre_run_mode == "demo"
    assert s.sre_approval_mode == "manual"
    assert s.sre_max_tool_calls == 35


def test_model_for_role_fallback():
    s = Settings(_env_file=None, sre_model="base-model")
    assert s.model_for("investigator") == "base-model"
    s2 = Settings(_env_file=None, sre_model="base", sre_model_investigator="inv-model")
    assert s2.model_for("investigator") == "inv-model"
    assert s2.model_for("planner") == "base"


def test_llm_base_url_presets():
    assert "api.x.ai" in Settings(_env_file=None, sre_llm_provider="xai").llm_base_url()
    assert "openrouter" in Settings(_env_file=None, sre_llm_provider="openrouter").llm_base_url()


def test_custom_provider_requires_base_url():
    with pytest.raises(ConfigError):
        Settings(_env_file=None, sre_llm_provider="custom").llm_base_url()


def test_missing_api_key_raises_config_error(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    s = Settings(_env_file=None, sre_llm_provider="xai", xai_api_key=None)
    with pytest.raises(ConfigError, match="XAI_API_KEY"):
        s.llm_api_key()


def test_service_registry_loads():
    reg = load_service_registry()
    assert "checkout-service" in reg.names()
    co = reg.get("checkout-service")
    assert co.port == 8081
    assert "inventory_url" in co.patchable_fields
    assert co.dependencies == ["inventory-service"]
    assert reg.get("order-worker").kind == "worker"


def test_registry_unknown_service():
    reg = load_service_registry()
    with pytest.raises(UnknownServiceError):
        reg.get("billing-service")


def test_policy_config_loads():
    pol = load_policy_config()
    assert pol.confidence_threshold() == 0.80
    patch = pol.operation("patch_runtime_config")
    assert patch is not None and patch.risk == "MEDIUM"
    assert pol.is_prohibited("arbitrary_shell")
    assert pol.rollback_required_for("patch_runtime_config")
    assert "checkout-service" in pol.scenario_overrides["wrong-inventory-endpoint"]["acceptable_targets"]
