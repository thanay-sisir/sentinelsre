# Runbook: When to Escalate

Escalation is a correct outcome, not a failure. Escalate (with evidence ids)
when ANY of these hold:

- Confidence in every hypothesis stays below the action threshold after a
  bounded investigation.
- The only fix requires a prohibited or non-allowlisted operation.
- Verification fails after remediation AND rollback completes — the system is
  back to the pre-change broken state and needs human judgment.
- Rollback itself fails — the environment is now inconsistent.
- Evidence contradicts itself in ways you cannot resolve with read-only tools.
- A tool or budget limit is reached before root cause is confirmed.
- Attempting the remediation would breach the safety policy.

## What to include
- The strongest hypothesis and its confidence.
- Evidence ids supporting and contradicting it.
- What was tried, what happened, what is still unknown.
- A concrete recommendation for the human operator.

## Never
- Guess a fix when confidence is low.
- Retry the same mutating action hoping for a different result.
- Hide a failed or partial remediation — record it in the report.
