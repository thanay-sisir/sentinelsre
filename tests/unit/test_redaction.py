"""Redaction coverage — anything that smells like a secret gets masked."""

from sre_agent.policy.redaction import contains_secret, redact_text, redact_value


def test_bearer_token():
    out = redact_text("Authorization: Bearer abcdef1234567890token")
    assert "abcdef" not in out and "REDACTED" in out


def test_api_key_assignment():
    for text in (
        "api_key=sk-abcdef1234567890",
        '"password": "hunter2"',
        "client_secret: 'xyz789abcdef'",
    ):
        out = redact_text(text)
        assert "REDACTED" in out, text


def test_url_credentials():
    out = redact_text("postgres://admin:s3cret@db.internal:5432/app")
    assert "s3cret" not in out and "***:***@" in out


def test_pem_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\nOw==\n-----END RSA PRIVATE KEY-----"
    out = redact_text(f"config: {pem} tail")
    assert "MIIB" not in out and "REDACTED PRIVATE KEY" in out


def test_env_secret_names():
    out = redact_text("OPENAI_API_KEY=sk-real123456789 DB_PASSWORD=topsecret")
    assert "sk-real" not in out and "topsecret" not in out


def test_non_secret_untouched():
    text = "inventory_url=http://127.0.0.1:8082 level=ERROR"
    assert redact_text(text) == text


def test_nested_redaction():
    val = {"a": {"b": "token=abc1234567"}, "list": ["key=sk-abcdef123456"]}
    out = redact_value(val)
    assert "abc1234567" not in str(out) and "sk-abcdef" not in str(out)


def test_contains_secret():
    assert contains_secret("api_key=supersecretvalue")
    assert not contains_secret("plain log line")
