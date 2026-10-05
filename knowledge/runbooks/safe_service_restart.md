# Runbook: Safe Service Restart

## When to restart vs reload
- `reload` — config-only change, service supports in-place reload.
- `restart` — wedged worker, stale connection pools, reload unsupported.
- `start` — process is stopped entirely.

## Procedure
1. `service status` — record pid, uptime, restart_count.
2. Restart or reload only the affected service — never platform-wide.
3. Poll `check health` until ready (bounded; do not loop forever).
4. Run the scenario's functional check (e.g. synthetic checkout).
5. `metrics get` — confirm error rates return to baseline.

## Cautions
- `restart_count` increments on every restart — a climbing count after your
  action means the fix did not hold; treat as verification failure.
- A restart that makes things worse → follow the plan's rollback and escalate.

## Escalate when
- Two restarts do not restore readiness.
- Readiness passes but functional checks still fail.
