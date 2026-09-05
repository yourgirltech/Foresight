# Foresight

AI-powered **patient access & revenue cycle** platform for healthcare providers.
Multi-clinic SaaS from day one.

> **Core principle (non-negotiable):** AI recommends or drafts. A human approves
> anything consequential. Automation executes only *after* approval. This applies
> to every agent in the system.

Two phases so far:

- **Phase 0** — the multi-tenant foundation: Supabase Auth + Postgres RLS, signup
  / onboarding / invites, tenant isolation proven with two test clinics.
  [`docs/PHASE-0.md`](docs/PHASE-0.md).
- **Phase 1** — the claims & billing module: a deterministic rule engine scores
  each claim, an agent explains it, another recommends an action, a human
  approves, and only then does an agent (or a human hand-off) execute.
  [`docs/PHASE-1.md`](docs/PHASE-1.md).

See [`docs/architecture.md`](docs/architecture.md) for the tenancy model and the
agent system, and [`docs/agents/00-commander.md`](docs/agents/00-commander.md) for
the Commander rule table.

## Layout

```
foresight/
├── frontend/     React + TypeScript + Vite + Tailwind. Auth, onboarding, invites, claims review UI.
├── backend/      Python + FastAPI. Verifies the Supabase JWT; hosts the agent system (app/agents/).
├── supabase/     Postgres schema + Row Level Security policies + RPCs (migrations).
├── tests/        Two-clinic data-isolation proof, rule-engine + Commander unit tests, agent isolation, e2e.
├── scripts/      Isolation-proof orchestration; synthetic claim seeder.
└── docs/         Architecture, phase notes, agent specs.
```

## Prerequisites

| Tool | Version used | Notes |
|------|--------------|-------|
| Node | 20+ (tested on 24) | frontend |
| Python | 3.11+ (tested on 3.14) | backend + tests |
| Docker Desktop | any recent | required by the local Supabase stack |
| Supabase CLI | 2.x | invoked via `npx supabase` — no global install needed |

## Quick start (local)

```bash
# 1. Start the local Supabase stack (Postgres + Auth + PostgREST + Studio).
#    Applies everything in supabase/migrations automatically.
npx supabase start

# 2. Backend
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env            # fill from `npx supabase status`; add ANTHROPIC_API_KEY for agent 07
uvicorn app.main:app --reload --port 8000

# 3. Frontend
cd frontend
npm install
cp .env.example .env            # fill from `npx supabase status`
npm run dev                     # http://localhost:5173

# 4. Seed synthetic claims (prints the real risk distribution)
python scripts/seed_claims.py   # log in as the printed seed admin, open /claims
```

## Prove tenant isolation

```bash
# From the repo root, with Docker running:
bash scripts/run_isolation_proof.sh
```

This spins up Supabase, applies migrations, starts the backend, then runs
`tests/isolation_test.py`, which:

1. Creates **Clinic A** (admin + invited staff) and **Clinic B** (admin).
2. Exercises signup → create-clinic → invite-teammate → accept-invite.
3. Logged in as Clinic A, tries every way to read or mutate Clinic B's data —
   directly against PostgREST *and* through the FastAPI backend with a spoofed
   `organization_id` — and asserts every attempt returns nothing / is rejected.
4. Confirms a `platform_admin` can see across both clinics.

Expected tail of output:

```
ALL 29 CHECKS PASSED — cross-tenant isolation holds.
```

## Test the claims module

```bash
python tests/rules_test.py              # 06 risk formula
python tests/commander_test.py           # 00 Commander rule table + invariant fuzz
python tests/agent_isolation_test.py     # agent pipeline never crosses a tenant boundary
python tests/e2e_claim_test.py           # end-to-end; full approval path needs ANTHROPIC_API_KEY
```
