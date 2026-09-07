#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Phase 4 / 04 (coordination of benefits) proof: the pure rule-engine fuzz
# (no stack, no key), then the endpoint + isolation tests against a fresh
# local stack.
#
#   bash scripts/run_cob_proof.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="backend/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "backend venv not found — see backend/README.md"; exit 1; }

echo "==> 04 rule engine — determine_cob() worked cases + exhaustive pair fuzz (no stack, no key)"
"$PY" tests/cob_test.py

echo
echo "==> Checking Docker"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

echo "==> Starting Supabase (idempotent)"
npx --yes supabase start >/dev/null 2>&1 || npx --yes supabase start

echo "==> Applying migrations from a clean slate (supabase db reset)"
npx --yes supabase db reset >/dev/null

echo
echo "==> Agent pipeline — tenant isolation (now includes a COB slice)"
"$PY" tests/agent_isolation_test.py

echo
echo "==> End-to-end — coordination of benefits through the real endpoints"
"$PY" tests/e2e_cob_test.py

echo
echo "==> Seeding patient coverages (every rule R0-R7)"
"$PY" scripts/seed_coverages.py

echo
echo "All 04 coordination-of-benefits checks complete."
