#!/bin/bash
# Oracle solution: the minimal remediation for wrong-inventory-endpoint.
# Proves the task is solvable through the sre-ops surface alone.
set -euo pipefail

cd /opt/sentinelsre
OPS="python -m demo_platform.ops_cli.cli"

# Platform may not be up yet in oracle runs (agent setup is skipped).
$OPS platform up

# Read ground truth is NOT allowed — derive the fix from the registry:
# deps status reveals expected endpoint vs configured endpoint.
EXPECTED=$($OPS deps status checkout-service | python -c "import json,sys; d=json.load(sys.stdin); print(d['dependencies'][0]['expected_url'])")
HASH=$($OPS config hash checkout-service | python -c "import json,sys; print(json.load(sys.stdin)['content_hash'])")

printf '{"inventory_url": "%s"}' "$EXPECTED" > runtime/fix.json
$OPS config patch checkout-service --patch-file runtime/fix.json --expected-hash "$HASH"
$OPS service reload checkout-service

# Deterministic verification
$OPS check health checkout-service
$OPS check synthetic checkout
