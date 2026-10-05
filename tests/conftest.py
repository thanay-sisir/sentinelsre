"""Shared pytest fixtures."""

import pytest

from sre_agent.config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Settings that ignore any local .env file."""
    return Settings(_env_file=None)
