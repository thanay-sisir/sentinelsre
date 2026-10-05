# Runbook: Queue Backlog

## Symptoms
- `queue_depth` metric increasing monotonically.
- Checkout succeeds at the API but orders stay in "processing".
- Worker heartbeat stale or missing.

## Possible causes
- Worker process stopped or crashed.
- Worker throughput lower than arrival rate (batch/poll misconfigured).
- Poison message blocking the head of the queue.

## Diagnostic steps
1. `metrics get order-worker` — read queue_depth and processed_total.
2. `service status order-worker` — running? fresh heartbeat?
3. `logs read order-worker` — errors on specific orders?
4. Re-check queue_depth after a short interval — is it shrinking?

## Safe remediations
- `service start` / `service restart` on the worker only — never the API tier
  for a worker-side backlog.
- If processing is too slow but correct, a `batch_size`/`poll_interval_ms`
  tuning patch may be justified with evidence.

## Verification
- queue_depth decreases across two consecutive metric reads.
- Worker heartbeat timestamp is fresh.

## Escalate when
- Queue grows even after a healthy worker restart.
- Specific orders repeatedly fail processing (data problem, not ops).
