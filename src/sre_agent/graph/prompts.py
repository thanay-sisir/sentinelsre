"""Prompt loading. Prompt files live next to this module, versioned by name.

Every report records which prompt version produced it (spec section 19:
reports carry prompt_version so LangSmith comparisons can attribute changes).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts"

PROMPT_VERSIONS: dict[str, str] = {
    "commander": "commander_v1",
    "investigator": "investigator_v1",
    "planner": "planner_v1",
    "reporter": "reporter_v1",
}


@lru_cache(maxsize=8)
def load_prompt(role: str) -> str:
    """Return the system prompt body for a role (e.g. 'investigator')."""
    version = PROMPT_VERSIONS[role]
    path = _PROMPT_DIR / f"{version}.md"
    return path.read_text(encoding="utf-8")
