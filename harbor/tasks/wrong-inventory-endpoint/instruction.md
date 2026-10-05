# Incident: checkout-service outage

The demo e-commerce platform at `/opt/sentinelsre` is live. Checkout
transactions are failing: the synthetic `checkout` check returns HTTP 503.

## Your task

1. Investigate using ONLY the `sre-ops` CLI (`python -m demo_platform.ops_cli.cli …`).
   Useful surfaces: `services list`, `service status`, `logs read`, `metrics get`,
   `deps status`, `config read`, `check health`, `check synthetic`,
   `runbooks search`, `disk usage`.
2. Identify the root cause and apply the *smallest* safe remediation.
3. Verify recovery: `check synthetic checkout` must pass and
   `check health checkout-service` must be ready.

## Constraints

- You may only mutate via `sre-ops` subcommands (`service start|restart|reload`,
  `config patch|restore`, `logs rotate`). No raw shell edits to config, no
  killing processes directly, no installing packages.
- `config patch` requires `--patch-file <json>` and `--expected-hash <sha>`
  (optimistic concurrency). Read the hash with `config hash` first.
- Do not read anything under `runtime/.verifier/` — it holds hidden ground
  truth for grading.
- Prefer reversible changes; `config patch` auto-creates a backup.

## Environment layout

- `/opt/sentinelsre/demo_platform/` — service code (read-only reference)
- `/opt/sentinelsre/configs/services.yaml` — service registry
- `/opt/sentinelsre/knowledge/runbooks/` — operational runbooks
- `/opt/sentinelsre/runtime/` — live config, logs, state, audit log
