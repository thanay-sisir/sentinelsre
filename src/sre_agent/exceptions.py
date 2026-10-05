"""Typed exceptions for SentinelSRE. Broad `except Exception` is forbidden;
callers catch these specific types."""

from __future__ import annotations


class SentinelError(Exception):
    """Base class for all SentinelSRE errors."""


class ConfigError(SentinelError):
    """Invalid or missing configuration / environment variables."""


class UnknownServiceError(SentinelError):
    """Service name is not present in the service registry."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"unknown service: {service!r}")


class NotAllowlistedError(SentinelError):
    """A field, operation, or path is not on the allowlist."""


class StaleConfigHashError(SentinelError):
    """expected_config_hash does not match current config content hash."""

    def __init__(self, service: str, expected: str, actual: str) -> None:
        self.service = service
        self.expected = expected
        self.actual = actual
        super().__init__(f"stale config hash for {service!r}: expected {expected}, actual {actual}")


class BackendUnavailableError(SentinelError):
    """The ops backend cannot reach the managed environment."""


class BackendOperationError(SentinelError):
    """A backend operation executed but failed."""

    def __init__(self, operation: str, target: str, detail: str) -> None:
        self.operation = operation
        self.target = target
        self.detail = detail
        super().__init__(f"{operation} on {target!r} failed: {detail}")


class PolicyDeniedError(SentinelError):
    """The policy engine denied the proposed action."""


class ApprovalError(SentinelError):
    """Base class for approval-token failures."""


class ApprovalRequiredError(ApprovalError):
    """A mutating operation was attempted without a valid token."""


class ApprovalExpiredError(ApprovalError):
    """Approval token is past its expiry."""


class ApprovalReplayError(ApprovalError):
    """Approval token has already been redeemed (single-use)."""


class ApprovalMismatchError(ApprovalError):
    """Token does not match the attempted operation/target/parameters."""


class ApprovalInvalidSignatureError(ApprovalError):
    """Approval token signature verification failed."""


class IllegalTransitionError(SentinelError):
    """Attempted an incident state transition that the lifecycle forbids."""

    def __init__(self, previous: str, new: str) -> None:
        self.previous = previous
        self.new = new
        super().__init__(f"illegal incident transition: {previous} -> {new}")


class BudgetExceededError(SentinelError):
    """A configured run budget (turns, tool calls, time, ...) was exhausted."""


class StructuredOutputError(SentinelError):
    """Model structured output failed validation after repair attempts."""


class VerificationFailedError(SentinelError):
    """Post-remediation verification checks did not pass."""
