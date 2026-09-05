# Foresight backend

FastAPI service. Its only Phase 1 job: **verify the caller's Supabase session
and make sure every query it runs is scoped to that caller's organization —
server-side, never from client input.**

## How tenant scoping is enforced

1. `app/auth.py` verifies the `Authorization: Bearer <jwt>` token against
   `SUPABASE_JWT_SECRET` (HS256, audience `authenticated`). A bad or expired
   token → `401`.
2. The user id comes from the **verified token**. The service then loads that
   user's `profiles` row to resolve `organization_id` + `role`.
3. Every downstream query goes through `app/supabase_rest.py`, which calls
   PostgREST **with the user's own JWT**. Postgres RLS then applies exactly as
   it does for the frontend. The service-role key is never used to serve a
   user request.
4. Endpoints that accept an `organization_id` (e.g. `GET /api/organizations`)
   **ignore it** and use the session's org instead.

## Run

```bash
python -m venv .venv
source .venv/Scripts/activate     # Windows Git Bash;  .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env               # fill SUPABASE_ANON_KEY / SUPABASE_JWT_SECRET from `npx supabase status`
uvicorn app.main:app --port 8000
# add --reload for dev autoreload (also: pip install watchfiles)
```

> Runs on Python 3.14. Requirement versions are floors, not pins — the
> Rust-backed wheels (pydantic-core) only ship for 3.14 from recent releases.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | liveness |
| `GET` | `/api/me` | resolved identity + `organization_id` (drives onboarding vs. app-shell) |
| `GET` | `/api/organizations` | caller's org only; a spoofed `?organization_id=` is ignored |
| `GET` | `/api/organizations/{id}` | one org by id, via RLS scope — another clinic's id → `404` |
| `GET` | `/api/team` | profiles in the caller's clinic only |
| `GET` | `/api/claims` | claims in the caller's clinic (RLS-scoped) |
| `GET` | `/api/claims/{id}` | one claim: findings, reasoning, recommendation, activity, escalations |
| `POST` | `/api/claims/{id}/approve` | approve the pending recommendation → hand off to the agent orchestrator |
| `POST` | `/api/claims/{id}/decline` | decline the pending recommendation |
| `POST` | `/api/claims/{id}/reanalyze` | re-run the pipeline from 06 |

## Agents (Phase 1)

`app/agents/` — a pure Commander (`commander.py`) routing to specialist agents
via an ordered rule table (`docs/agents/00-commander.md`). The orchestrator
(`orchestrator.py`) runs with the **service-role key** (background job, not a
user request — `docs/architecture.md` §3.4) and threads the `organization_id`
resolved from the triggering claim into every query. `07-reasoning` calls the
Claude API — set `ANTHROPIC_API_KEY` in `.env` (never hardcoded).
