#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One command: bring up Supabase + the backend, then run the two-clinic
# data-isolation proof. Safe to re-run. Requires Docker Desktop running.
#
#   bash scripts/run_isolation_proof.sh              # full run (PostgREST + backend)
#   bash scripts/run_isolation_proof.sh --skip-backend
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_BACKEND="${1:-}"
BACKEND_PID=""

cleanup() {
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> Checking Docker"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

echo "==> Starting Supabase (idempotent)"
npx --yes supabase start >/dev/null 2>&1 || npx --yes supabase start

echo "==> Applying migrations from a clean slate (supabase db reset)"
npx --yes supabase db reset >/dev/null

# Pull connection details straight from the running stack.
STATUS_JSON="$(npx --yes supabase status -o json)"
export SUPABASE_URL="$(node -e "process.stdout.write(JSON.parse(process.argv[1]).API_URL)" "$STATUS_JSON")"
export SUPABASE_ANON_KEY="$(node -e "process.stdout.write(JSON.parse(process.argv[1]).ANON_KEY)" "$STATUS_JSON")"
export SUPABASE_SERVICE_ROLE_KEY="$(node -e "process.stdout.write(JSON.parse(process.argv[1]).SERVICE_ROLE_KEY)" "$STATUS_JSON")"
export SUPABASE_JWT_SECRET="$(node -e "process.stdout.write(JSON.parse(process.argv[1]).JWT_SECRET)" "$STATUS_JSON")"
export FRONTEND_ORIGIN="http://localhost:5173"

if [[ "$SKIP_BACKEND" != "--skip-backend" ]]; then
  echo "==> Starting FastAPI backend on :8000"
  cd backend
  if [[ ! -d .venv ]]; then
    python -m venv .venv
  fi
  # shellcheck disable=SC1091
  if [[ -f .venv/Scripts/activate ]]; then source .venv/Scripts/activate; else source .venv/bin/activate; fi
  pip install -q -r requirements.txt
  uvicorn app.main:app --port 8000 --log-level warning &
  BACKEND_PID=$!
  cd "$ROOT"

  echo "==> Waiting for backend /health"
  for _ in $(seq 1 30); do
    if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then break; fi
    sleep 1
  done
  curl -fsS http://localhost:8000/health >/dev/null || { echo "backend did not come up"; exit 1; }
fi

echo "==> Running isolation test"
echo
python tests/isolation_test.py ${SKIP_BACKEND:+--skip-backend}
