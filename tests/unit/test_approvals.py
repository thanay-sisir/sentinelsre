"""Approval token lifecycle: issue, verify, redeem, expiry, replay, mismatch."""

import pytest

from sre_agent.exceptions import (
    ApprovalExpiredError,
    ApprovalInvalidSignatureError,
    ApprovalMismatchError,
    ApprovalReplayError,
)
from sre_agent.policy.approvals import ApprovalManager, params_hash


@pytest.fixture()
def mgr() -> ApprovalManager:
    return ApprovalManager(ttl_seconds=60, secret=b"test-secret")


def _issue(mgr: ApprovalManager, **kw):
    base = {
        "incident_id": "INC-1",
        "action_id": "act-1",
        "operation": "patch_runtime_config",
        "target": "checkout-service",
        "params": {
            "changes": {"inventory_url": "http://x"},
            "expected_config_hash": "h",
        },
        "issued_by": "test",
    }
    base.update(kw)
    return mgr.issue(**base)


def test_issue_and_redeem(mgr):
    t = _issue(mgr)
    mgr.verify(t)
    mgr.redeem(
        t,
        operation="patch_runtime_config",
        target="checkout-service",
        params={"changes": {"inventory_url": "http://x"}, "expected_config_hash": "h"},
    )


def test_replay_rejected(mgr):
    t = _issue(mgr)
    mgr.redeem(
        t,
        operation="patch_runtime_config",
        target="checkout-service",
        params={"changes": {"inventory_url": "http://x"}, "expected_config_hash": "h"},
    )
    with pytest.raises(ApprovalReplayError):
        mgr.verify(t)


def test_expired_token_rejected():
    mgr = ApprovalManager(ttl_seconds=-1, secret=b"s")
    t = _issue(mgr)
    with pytest.raises(ApprovalExpiredError):
        mgr.verify(t)


def test_wrong_operation_rejected(mgr):
    t = _issue(mgr)
    with pytest.raises(ApprovalMismatchError):
        mgr.redeem(t, operation="restart_service", target="checkout-service", params={})


def test_wrong_target_rejected(mgr):
    t = _issue(mgr)
    with pytest.raises(ApprovalMismatchError):
        mgr.redeem(
            t,
            operation="patch_runtime_config",
            target="inventory-service",
            params={"changes": {"inventory_url": "http://x"}, "expected_config_hash": "h"},
        )


def test_wrong_params_rejected(mgr):
    t = _issue(mgr)
    with pytest.raises(ApprovalMismatchError, match="parameters"):
        mgr.redeem(
            t,
            operation="patch_runtime_config",
            target="checkout-service",
            params={"changes": {"inventory_url": "http://EVIL"}, "expected_config_hash": "h"},
        )


def test_forged_signature_rejected(mgr):
    t = _issue(mgr)
    forged = t.model_copy(update={"signature": "0" * 64})
    with pytest.raises(ApprovalInvalidSignatureError):
        mgr.verify(forged)


def test_forged_token_fields_rejected(mgr):
    """Tampering with target invalidates the signature."""
    t = _issue(mgr)
    forged = t.model_copy(update={"target": "inventory-service"})
    with pytest.raises(ApprovalInvalidSignatureError):
        mgr.verify(forged)


def test_params_hash_stable_key_order():
    a = params_hash({"b": 2, "a": 1})
    b = params_hash({"a": 1, "b": 2})
    assert a == b
