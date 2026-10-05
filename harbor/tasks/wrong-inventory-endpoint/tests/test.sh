#!/bin/bash
# Verifier entry. Runs inside the task environment after the agent phase.
# verify.py emits /logs/verifier/reward.json with per-check sub-scores.
set -u

mkdir -p /logs/verifier
python /tests/verify.py
echo "verify exit=$?"
