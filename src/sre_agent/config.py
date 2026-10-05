"""Application configuration: env settings + YAML registries.

Everything the agent may touch is configured here — no hardcoded service
names, ports, policy thresholds, or model names in source code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sre_agent.exceptions import ConfigError, UnknownServiceError

LLMProvider = Literal["xai", "openrouter", "openai", "custom"]
RunMode = Literal["demo", "benchmark"]
ApprovalMode = Literal["manual", "auto_safe", "deny"]

PROVIDER_BASE_URLS: dict[str, str] = {
    "xai": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
}


class Settings(BaseSettings):
    """Environment-driven settings. Reads .env if present."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM provider ---
    sre_llm_provider: LLMProvider = "xai"
    xai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    sre_llm_base_url: str | None = None
    sre_llm_api_key: SecretStr | None = None  # for provider=custom

    sre_model: str = "grok-4-fast"
    sre_model_commander: str | None = None
    sre_model_investigator: str | None = None
    sre_model_planner: str | None = None
    sre_model_reporter: str | None = None

    # --- LangSmith (optional) ---
    langsmith_api_key: SecretStr | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "sentinelsre-agent-traces"
    langsmith_tracing: bool = True
    langsmith_workspace_id: str | None = None

    # --- Runtime ---
    sre_environment: str = "local"
    sre_run_mode: RunMode = "demo"
    sre_approval_mode: ApprovalMode = "manual"
    sre_database_url: str = "sqlite+aiosqlite:///./reports/sentinelsre.db"
    sre_artifact_dir: Path = Path("./reports")
    sre_policy_file: Path = Path("./configs/policy.yaml")
    sre_service_config_file: Path = Path("./configs/services.yaml")
    sre_runtime_dir: Path = Path("./runtime")

    # --- Budgets ---
    sre_max_model_turns: int = 15
    sre_max_tool_calls: int = 35
    sre_max_remediation_attempts: int = 2
    sre_max_verification_retries: int = 3
    sre_max_no_progress_cycles: int = 3
    sre_max_log_lines: int = 200
    sre_max_tool_output_bytes: int = 51200
    sre_max_active_hypotheses: int = 3
    sre_action_confidence_threshold: float = 0.80
    sre_incident_timeout_seconds: int = 600

    # --- Demo platform ---
    sre_checkout_port: int = 8081
    sre_inventory_port: int = 8082

    # --- Harbor ---
    harbor_output_dir: Path = Path("./jobs")
    harbor_langsmith_dataset: str = "sentinelsre-harbor-tasks"
    harbor_langsmith_experiment: str = "baseline-v1"

    def model_for(self, role: str) -> str:
        """Resolve the model name for an agent role with SRE_MODEL fallback."""
        override = getattr(self, f"sre_model_{role}", None)
        return override or self.sre_model

    def llm_base_url(self) -> str:
        if self.sre_llm_provider == "custom":
            if not self.sre_llm_base_url:
                raise ConfigError("SRE_LLM_PROVIDER=custom requires SRE_LLM_BASE_URL to be set")
            return self.sre_llm_base_url
        return PROVIDER_BASE_URLS[self.sre_llm_provider]

    def llm_api_key(self) -> SecretStr:
        """Resolve the API key for the configured provider."""
        key = {
            "xai": self.xai_api_key,
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "custom": self.sre_llm_api_key,
        }[self.sre_llm_provider]
        if key is None:
            env_name = {
                "xai": "XAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "custom": "SRE_LLM_API_KEY",
            }[self.sre_llm_provider]
            raise ConfigError(f"provider {self.sre_llm_provider!r} requires {env_name} to be set")
        return key

    def langsmith_enabled(self) -> bool:
        return self.langsmith_api_key is not None and self.langsmith_tracing


# --------------------------------------------------------------------------
# Service registry (configs/services.yaml)
# --------------------------------------------------------------------------


class ServiceDef(BaseModel):
    name: str = ""  # filled from the services dict key by ServiceRegistry
    kind: Literal["web", "worker"]
    module: str
    port: int | None = None
    host: str = "127.0.0.1"
    health_path: str | None = None
    ready_path: str | None = None
    metrics_path: str | None = None
    config_file: str
    log_file: str
    patchable_fields: list[str] = Field(default_factory=list)
    secret_fields: list[str] = Field(default_factory=list)
    mutating_ops: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    description: str = ""

    def base_url(self) -> str:
        if self.port is None:
            raise ConfigError(f"service {self.name!r} has no port (kind={self.kind})")
        return f"http://{self.host}:{self.port}"


class SyntheticCheckDef(BaseModel):
    description: str
    service: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ServiceRegistry(BaseModel):
    version: int = 1
    services: dict[str, ServiceDef]
    synthetic_checks: dict[str, SyntheticCheckDef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _attach_names(self) -> Self:
        for name, svc in self.services.items():
            svc.name = name
        return self

    def get(self, name: str) -> ServiceDef:
        try:
            return self.services[name]
        except KeyError:
            raise UnknownServiceError(name) from None

    def names(self) -> list[str]:
        return list(self.services)


# --------------------------------------------------------------------------
# Policy config (configs/policy.yaml)
# --------------------------------------------------------------------------


class OperationPolicy(BaseModel):
    risk: str
    description: str = ""
    auto_approve_in_benchmark: bool = False
    is_rollback: bool = False


class PolicyConfig(BaseModel):
    version: int = 1
    defaults: dict[str, Any] = Field(default_factory=dict)
    operations: dict[str, OperationPolicy] = Field(default_factory=dict)
    prohibited: list[str] = Field(default_factory=list)
    forbidden_path_patterns: list[str] = Field(default_factory=list)
    scenario_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def confidence_threshold(self) -> float:
        return float(self.defaults.get("confidence_threshold", 0.80))

    def min_evidence_for_medium_risk(self) -> int:
        return int(self.defaults.get("min_evidence_for_medium_risk", 2))

    def token_ttl_seconds(self) -> int:
        return int(self.defaults.get("approval_token_ttl_seconds", 300))

    def rollback_required_for(self, operation: str) -> bool:
        return operation in self.defaults.get("rollback_required_for", [])

    def operation(self, name: str) -> OperationPolicy | None:
        return self.operations.get(name)

    def is_prohibited(self, operation: str) -> bool:
        return operation in self.prohibited


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} is not a YAML mapping")
    return data


def load_service_registry(path: Path | None = None) -> ServiceRegistry:
    path = path or Path("./configs/services.yaml")
    return ServiceRegistry.model_validate(_load_yaml(path))


def load_policy_config(path: Path | None = None) -> PolicyConfig:
    path = path or Path("./configs/policy.yaml")
    return PolicyConfig.model_validate(_load_yaml(path))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
