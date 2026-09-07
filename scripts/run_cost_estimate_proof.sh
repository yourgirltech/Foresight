#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Phase 4 / 05 (cost estimate / Good Faith Estimate) proof: the pure pricing +
# gate + disclaimer checks (no stack, no key), then the endpoint + isolation
# tests against a fresh local stack, then the opt-in live phrasing check.
#
#   bash scripts/run_cost_estimate_proof.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="backend/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "backend venv not found — see backend/README.md"; exit 1; }

echo "==> 05 pure contract — price() + NSA gate grid + disclaimer constant (no stack, no key)"
"$PY" tests/cost_estimate_test.py

echo
echo "==> Checking Docker"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

echo "==> Starting Supabase (idempotent)"
npx --yes supabase start >/dev/null 2>&1 || npx --yes supabase start

echo "==> Applying migrations from a clean slate (supabase db reset)"
npx --yes supabase db reset >/dev/null

echo
echo "==> Agent pipeline — tenant isolation (now includes a cost-estimate slice)"
"$PY" tests/agent_isolation_test.py

echo
echo "==> End-to-end — Good Faith Estimate through the real endpoints"
"$PY" tests/e2e_cost_estimate_test.py

echo
echo "==> Live phrasing test (skipped without --live / ANTHROPIC_API_KEY)"
"$PY" tests/cost_estimate_live_test.py --live

echo
echo "==> Seeding the self-pay price list"
"$PY" scripts/seed_procedure_prices.py

echo
echo "All 05 cost-estimate checks complete."
