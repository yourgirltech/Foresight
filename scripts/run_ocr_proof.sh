#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Phase 4 / 03 (insurance card OCR) proof: bring up Supabase, apply migrations,
# then run the OCR test suite.
#
#   bash scripts/run_ocr_proof.sh
#
# The pure confidence-gate test needs neither Docker nor a key. The end-to-end
# endpoint test needs the local stack; the live vision test additionally needs
# ANTHROPIC_API_KEY (backend/.env or the environment) and is skipped without it.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="backend/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "backend venv not found — see backend/README.md"; exit 1; }

echo "==> 03 confidence gate — classify_extraction() full grid (no stack, no key)"
"$PY" tests/ocr_gate_test.py

echo
echo "==> Checking Docker"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

echo "==> Starting Supabase (idempotent)"
npx --yes supabase start >/dev/null 2>&1 || npx --yes supabase start

echo "==> Applying migrations from a clean slate (supabase db reset)"
npx --yes supabase db reset >/dev/null

echo
echo "==> Agent pipeline — tenant isolation (now includes a card-scan confirm)"
"$PY" tests/agent_isolation_test.py

echo
echo "==> End-to-end — card OCR through the real endpoints"
"$PY" tests/e2e_card_scan_test.py

echo
echo "==> Live vision test (skipped without --live / ANTHROPIC_API_KEY)"
"$PY" tests/ocr_live_test.py --live

echo
echo "==> Seeding synthetic card scans"
"$PY" scripts/seed_card_scans.py --live

echo
echo "All 03 OCR checks complete."
