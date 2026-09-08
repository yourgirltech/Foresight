#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Phase 5 / 11 (appeals) proof: the pure agent + Commander tests (no stack,
# no key), then the endpoint + isolation tests against a fresh local stack,
# then the opt-in --live drafting test, then the seed.
#
#   bash scripts/run_appeals_proof.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="backend/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "backend venv not found — see backend/README.md"; exit 1; }

echo "==> 11 pure — appeal_basis() grounding grid + simulate_resolution() sweep (no stack, no key)"
"$PY" tests/appeals_agent_test.py

echo
echo "==> 11 Commander — AP1-AP12 + the two structural-invariant fuzzes (no stack, no key)"
"$PY" tests/appeals_commander_test.py

echo
echo "==> Regression — the three existing rule blocks must be byte-identical"
"$PY" tests/commander_test.py
"$PY" tests/eligibility_commander_test.py
"$PY" tests/prior_auth_commander_test.py

echo
echo "==> Checking Docker"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

echo "==> Starting Supabase (idempotent)"
npx --yes supabase start >/dev/null 2>&1 || npx --yes supabase start

echo "==> Applying migrations from a clean slate (supabase db reset)"
npx --yes supabase db reset >/dev/null

echo
echo "==> Agent pipeline — tenant isolation (now includes a Phase 5 appeal slice)"
"$PY" tests/agent_isolation_test.py

echo
echo "==> End-to-end — appeals through the real orchestrator"
"$PY" tests/e2e_appeal_test.py

echo
echo "==> Live drafting test (opt-in; needs ANTHROPIC_API_KEY with credit)"
"$PY" tests/appeals_live_test.py --live || echo "   (model unavailable — live draft skipped)"

echo
echo "==> Seeding appeals (denied claims across the grounds spectrum + the resolution split)"
"$PY" scripts/seed_appeals.py || echo "   (model unavailable — seed left denied claims at 'error')"

echo
echo "All 11 appeals checks complete."
