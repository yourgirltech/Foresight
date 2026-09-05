#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Phase 1 (claims & billing) proof: bring up Supabase, apply migrations, then
# run the whole test suite for the claims module.
#
#   bash scripts/run_claims_proof.sh
#
# The end-to-end approval path and the seeder's manual_action_required demo
# need ANTHROPIC_API_KEY (in backend/.env or the environment). Without it the
# deterministic parts (rule engine, Commander rule table, agent tenant
# isolation, 06 + routing) still run and pass.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="backend/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "backend venv not found — see backend/README.md"; exit 1; }

echo "==> Checking Docker"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

echo "==> Starting Supabase (idempotent)"
npx --yes supabase start >/dev/null 2>&1 || npx --yes supabase start

echo "==> Applying migrations from a clean slate (supabase db reset)"
npx --yes supabase db reset >/dev/null

echo
echo "==> 06 analyzer — risk formula"
"$PY" tests/rules_test.py

echo
echo "==> 00 Commander — rule table + determinism/invariant fuzz"
"$PY" tests/commander_test.py

echo
echo "==> Agent pipeline — tenant isolation"
"$PY" tests/agent_isolation_test.py

echo
echo "==> End-to-end — one claim through the Commander"
"$PY" tests/e2e_claim_test.py

echo
echo "==> Seeding synthetic claims (real risk distribution)"
"$PY" scripts/seed_claims.py

echo
echo "All claims-module checks complete."
