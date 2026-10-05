# Runbook: Service Process Down

## Symptoms
- `service status` reports STOPPED or a dead pid.
- Health checks time out; port not listening.
- Restart loop: `restart_count` climbing quickly.

## Possible causes
- Crash on startup (bad config, missing file, port conflict).
- OOM or external kill.
- Supervisor deliberately stopped it.

## Diagnostic steps
1. `service status` — is a pid recorded? Is it alive?
2. `logs read --min-level ERROR` — look for the fatal startup line.
3. Read console log tail if present (`<service>.console.log` alongside JSONL).
4. `config read` — did a recent config change precede the crash?

## Safe remediations
- `service start` for a cleanly stopped service.
- `service restart` for a wedged one (counts as a restart; watch restart_count).
- If a recent config change correlates with the crash: restore the backup.

## Verification
- `service status` shows RUNNING and readiness true.
- Health endpoints return 200.
- `restart_count` is stable, not climbing.

## Rollback / escalation
- More than 2 failed restarts → escalate with logs attached.
- Never mask a crash loop by looping restarts without diagnosis.
