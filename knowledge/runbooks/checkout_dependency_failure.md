# Runbook: Checkout Dependency Failure

## Symptoms
- Elevated HTTP 5xx on checkout endpoints.
- `dependency_request_failed` or timeout events in checkout-service logs.
- Readiness probe failing while liveness (`/health`) still passes.

## Possible causes
- Upstream dependency is down or unhealthy.
- Checkout is configured to reach the dependency at a wrong host/port/path.
- Network timeouts caused by an undersized request timeout.
- Dependency is overloaded and rejecting connections.

## Diagnostic steps
1. Check `service status` and `check health` for the dependency itself —
   is it actually up?
2. Search checkout logs for `dependency_request_failed`; note the URL and
   error class (connect refused vs timeout vs DNS).
3. `deps status` — compare the configured dependency URL with the URL the
   service registry advertises.
4. `config read` the calling service — confirm which endpoint is configured.
5. `metrics get` — look at `dependency_failure_count` growth, not just errors.

## Safe remediations
- If the configured endpoint differs from the registered/expected endpoint:
  patch only that config field, then reload or restart the caller.
- If the dependency is down: restart the *dependency*, not the caller.
- If timeouts are suspicious: consider `request_timeout_ms` within policy.

## Verification
- `/ready` returns 200 on the calling service.
- The synthetic checkout transaction succeeds.
- `dependency_failure_count` stops increasing.

## Rollback
- Restore the config backup taken before the patch; restart/reload again.

## Escalate when
- Both endpoints are correct but calls still fail (network-layer issue).
- The dependency itself crashes repeatedly on start.
