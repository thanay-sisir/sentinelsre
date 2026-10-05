"""Redaction of secrets before data reaches logs, state, traces, or reports."""

from __future__ import annotations

import re
from typing import Any

# (label, pattern, replacement-with-capture)
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization headers / bearer tokens
    (
        re.compile(r"(?i)(authorization|bearer)\s*[:=]?\s*(['\"]?)(bearer\s+)?[\w.\-+/=]{8,}\2"),
        r"\1=***REDACTED***",
    ),
    # sk-/xai-/gsk_ style API keys
    (re.compile(r"\b(sk|xai|gsk|grok|or|pk|api)[-_][A-Za-z0-9_\-]{10,}\b"), "***REDACTED***"),
    # key=value or json "key": "value" for secret-ish names
    (
        re.compile(
            r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|pwd|access[_-]?key|"
            r"private[_-]?key|client[_-]?secret)"
            r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]{4,})"
        ),
        r"\1\2***REDACTED***",
    ),
    # URLs with embedded credentials  scheme://user:pass@host
    (re.compile(r"\b(\w+://)[^/\s:]+:[^/\s@]+@"), r"\1***:***@"),
    # PEM blocks
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "***REDACTED PRIVATE KEY***",
    ),
    # env-style FOO_SECRET=value / FOO_TOKEN=value / *_KEY=value
    (re.compile(r"(?i)\b([A-Z][A-Z0-9_]*(?:_KEY|_SECRET|_TOKEN|_PASSWORD))=(\S+)"), r"\1=***REDACTED***"),
]


def redact_text(text: str) -> str:
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside dicts/lists/tuples."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


def contains_secret(text: str) -> bool:
    """Heuristic detector used by the verifier to scan reports."""
    return redact_text(text) != text
