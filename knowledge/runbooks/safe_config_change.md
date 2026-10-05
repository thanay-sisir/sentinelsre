# Runbook: Safe Configuration Change

## Principles
- Patch the smallest possible set of fields.
- Always capture `config hash` first; pass it as `expected_hash` so a
  concurrent change is detected instead of silently overwritten.
- A successful patch only edits the file — the service picks it up via
  `service reload` (in-place) or `service restart`.

## Procedure
1. `config read <svc>` — record current values and content_hash.
2. Confirm with independent evidence what the correct value should be
   (service registry, dependency status, runbook).
3. `config patch <svc>` with only the changed fields and the expected hash.
   The tool takes a timestamped backup automatically — record `backup_id`.
4. `service reload <svc>` (preferred) or `service restart <svc>`.
5. Run verification checks; if they fail, `config restore <svc>
   --backup-id <id>` then reload again.

## Evidence bar
- Require at least two independent observations supporting the new value —
  e.g. a failing endpoint in logs AND a registry entry showing the right one.

## Prohibited
- Patching fields outside the service's `patchable_fields` allowlist.
- Editing secret-bearing fields through this tool.
