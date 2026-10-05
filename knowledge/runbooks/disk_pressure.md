# Runbook: Disk Pressure

## Symptoms
- `disk usage` above 90–95%.
- Services intermittently fail writes; log append errors.
- Latency degrading without a matching request spike.

## Possible causes
- Unrotated application logs accumulating.
- Large rotated archives never pruned.
- Queue/artifact files growing unboundedly.

## Diagnostic steps
1. `disk usage` — confirm pressure and scope.
2. `logs large <service> --min-mb N` — find which service's logs dominate.
3. `logs read` a sample — confirm they are routine, not evidence of a second
   incident.

## Safe remediations
- `logs rotate <service>` — the allowlisted rotation preserves the active log
  and prunes old archives. Never delete files with ad-hoc commands.
- Rotate the largest offender first; re-measure usage.

## Verification
- `disk usage` drops below the alert threshold.
- The service's current log file still exists and is being appended to.
- Health/ready checks pass after rotation.

## Prohibited
- Deleting arbitrary files or directories outside the log-rotation tool.
- Truncating the *active* log without rotation (destroys evidence).

## Escalate when
- Rotation does not reclaim enough space (growth is in non-log data).
