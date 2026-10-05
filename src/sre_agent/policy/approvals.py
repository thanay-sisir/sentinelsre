"""Approval tokens: HMAC-signed, single-use, scoped to one action.

A token authorizes exactly one (operation, target, params-hash) triple for one
incident. It expires, and redemption is recorded so replay fails. The signing
secret lives only inside the ApprovalManager — never in incident state, logs,
or model context.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sre_agent.exceptions import (
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalReplayError,
)
from sre_agent.models.policy import ApprovalToken


def params_hash(params: dict[str, Any]) -> str:
    """Stable hash of normalized action parameters."""
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()


class ApprovalManager:
    """Issues and redeems approval tokens for one incident run."""

    def __init__(self, ttl_seconds: int = 300, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._redeemed: set[str] = set()

    def _payload(self, token: ApprovalToken) -> bytes:
        return json.dumps(
            {
                "token_id": token.token_id,
                "incident_id": token.incident_id,
                "action_id": token.action_id,
                "operation": token.operation,
                "target": token.target,
                "params_sha256": token.params_sha256,
                "expires_at": token.expires_at.isoformat(),
                "issued_at": token.issued_at.isoformat(),
                "issued_by": token.issued_by,
            },
            sort_keys=True,
        ).encode()

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue(
        self,
        *,
        incident_id: str,
        action_id: str,
        operation: str,
        target: str,
        params: dict[str, Any],
        issued_by: str,
    ) -> ApprovalToken:
        now = datetime.now(UTC)
        token = ApprovalToken(
            token_id=uuid4().hex,
            incident_id=incident_id,
            action_id=action_id,
            operation=operation,
            target=target,
            params_sha256=params_hash(params),
            issued_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
            issued_by=issued_by,
            signature="",
        )
        return token.model_copy(update={"signature": self._sign(self._payload(token))})

    def verify(self, token: ApprovalToken) -> None:
        """Signature + expiry check (does NOT consume the token)."""
        expected = self._sign(self._payload(token))
        if not hmac.compare_digest(expected, token.signature):
            from sre_agent.exceptions import ApprovalInvalidSignatureError

            raise ApprovalInvalidSignatureError(f"token {token.token_id} bad signature")
        if datetime.now(UTC) > token.expires_at:
            raise ApprovalExpiredError(f"token {token.token_id} expired")
        if token.token_id in self._redeemed:
            raise ApprovalReplayError(f"token {token.token_id} already used")

    def redeem(
        self,
        token: ApprovalToken,
        *,
        operation: str,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Verify scope match + mark single-use. Raises typed ApprovalError."""
        self.verify(token)
        mismatches = []
        if operation != token.operation:
            mismatches.append(f"operation {operation!r} != {token.operation!r}")
        if target != token.target:
            mismatches.append(f"target {target!r} != {token.target!r}")
        ph = params_hash(params)
        if ph != token.params_sha256:
            mismatches.append("parameters differ from approved set")
        if mismatches:
            raise ApprovalMismatchError(f"token {token.token_id} mismatch: {'; '.join(mismatches)}")
        self._redeemed.add(token.token_id)
