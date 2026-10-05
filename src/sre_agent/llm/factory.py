"""LLM factory — provider-agnostic chat model construction.

xAI Grok uses langchain-xai's ChatXAI (native structured outputs).
OpenRouter/OpenAI/custom endpoints use ChatOpenAI with an OpenAI-compatible
base_url. Per-role model names fall back to SRE_MODEL.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from sre_agent.config import Settings


def make_model(settings: Settings, role: str, **kwargs: Any) -> BaseChatModel:
    """Return a chat model for the given agent role (commander|investigator|
    planner|reporter)."""
    model_name = settings.model_for(role)
    provider = settings.sre_llm_provider
    kwargs.setdefault("temperature", 0)
    kwargs.setdefault("timeout", 60)
    kwargs.setdefault("max_retries", 2)

    if provider == "xai":
        from langchain_xai import ChatXAI

        return ChatXAI(
            model=model_name,
            api_key=settings.llm_api_key().get_secret_value(),
            **kwargs,
        )

    # OpenAI-compatible endpoints (openai / openrouter / custom)
    from langchain_openai import ChatOpenAI

    extra_headers = {}
    if provider == "openrouter":
        extra_headers = {
            "HTTP-Referer": "https://github.com/sentinelsre",
            "X-Title": "SentinelSRE",
        }
    return ChatOpenAI(
        model=model_name,
        api_key=settings.llm_api_key().get_secret_value(),
        base_url=settings.llm_base_url(),
        default_headers=extra_headers or None,
        **kwargs,
    )
