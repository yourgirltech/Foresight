# Phase 0 — Foundation

> Earlier drafts numbered this "Phase 1". Renumbered 2026-09-02: the foundation
> is **Phase 0**, the claims & billing module is **Phase 1**
> ([`PHASE-1.md`](PHASE-1.md)).

**Goal:** a user can sign up, create a clinic, invite a teammate, log in, and see
an empty but correctly-scoped dashboard shell — with two test clinics proven
unable to see each other's data. No product features (eligibility, claims,
voice).

## Delivered

| # | Item | Location |
|---|------|----------|
| 1 | Monorepo skeleton (`frontend/ backend/ supabase/ docs/ tests/ scripts/`) | repo root |
| 2 | Schema: `organizations`, `profiles`, `invitations` + enums + indexes | `supabase/migrations/20260829000001_core_tenancy.sql` |
| 2 | RLS policies (FORCEd) + least-privilege column grants | same file |
| 2 | `enable_tenant_isolation()` — the standard guard for every future table | same file |
| 3 | Auth trigger + onboarding / invite RPCs (all `SECURITY DEFINER`) | `supabase/migrations/20260829000002_auth_and_onboarding.sql` |
| 3 | React auth flow: signup, login, onboarding, invite, accept-invite | `frontend/src/pages/*`, `frontend/src/auth/*` |
| 3 | App shell renders only at `status === "ready"` | `frontend/src/components/ProtectedRoute.tsx` |
| 4 | FastAPI: verify Supabase JWT, derive tenant server-side, forward user JWT to PostgREST | `backend/app/*` |
| 5 | Multi-tenant model documented | `docs/architecture.md` |
| 6 | Two-clinic isolation proof | `tests/isolation_test.py`, `scripts/run_isolation_proof.sh` |

## RLS policies (summary)

Full SQL: `supabase/migrations/20260829000001_core_tenancy.sql`.

| Table | Command | Policy |
|-------|---------|--------|
| `organizations` | SELECT | `id = current_org_id() OR is_platform_admin()` |
| `organizations` | UPDATE | `(id = current_org_id() AND current_user_role() = 'clinic_admin') OR is_platform_admin()` |
| `organizations` | INSERT / DELETE | _no policy_ → denied; creation only via `bootstrap_organization()` |
| `profiles` | SELECT | `id = auth.uid() OR organization_id = current_org_id() OR is_platform_admin()` |
| `profiles` | UPDATE | `id = auth.uid() OR is_platform_admin()` — **and** only the `full_name` column is grant-writable to `authenticated` |
| `profiles` | INSERT | _trigger only_ · DELETE — _cascade from `auth.users`_ |
| `invitations` | SELECT | `organization_id = current_org_id() OR is_platform_admin()` |
| `invitations` | INSERT / UPDATE / DELETE | _no policy_ → all mutation via `create/accept/revoke_invitation()` RPCs |

All three tables: `ENABLE` + `FORCE ROW LEVEL SECURITY`. `anon` and
`authenticated` have `REVOKE ALL`, then narrow `GRANT`s re-added.

## Running the proof

```bash
# Requires Docker Desktop running.
bash scripts/run_isolation_proof.sh
```

The script: `supabase start` → `supabase db reset` (clean migration apply) →
start FastAPI on `:8000` → `python tests/isolation_test.py`.

### What the test does

1. **Onboarding, for real.** Creates users through Supabase Auth, then:
   - `a_admin` calls `bootstrap_organization("Clinic A — Riverside")` → becomes its `clinic_admin`.
   - `b_admin` calls `bootstrap_organization("Clinic B — Lakeside")`.
   - `a_admin` calls `create_invitation(a_staff, "staff")`; `a_staff` calls
     `accept_invitation(token)` → joins Clinic A as `staff`.
   - `platform` is promoted to `platform_admin` via the service role (out of band).

2. **Isolation — signed in as Clinic A, reach for Clinic B** (all expected to
   return nothing / be rejected):

   | Check | Expectation |
   |-------|-------------|
   | `GET organizations` | only Clinic A |
   | `GET organizations?id=eq.<B>` | `[]` |
   | `GET profiles` | only Clinic A's 2 members; Clinic B's admin absent |
   | `GET profiles?organization_id=eq.<B>` | `[]` |
   | `GET invitations` | only Clinic A's |
   | `PATCH organizations?id=eq.<B>` set name | 0 rows; B's name unchanged (verified via service role) |
   | `POST organizations` (direct insert) | `401/403` |
   | `accept_invitation(<random>)` while already in an org | rejected |
   | backend `GET /api/organizations?organization_id=<B>` | spoofed id ignored; returns only Clinic A |
   | backend `GET /api/organizations/<B>` | `404` |
   | backend `GET /api/team` | only Clinic A members |

3. **Privilege escalation — signed in as Clinic A `staff`:**

   | Check | Expectation |
   |-------|-------------|
   | `PATCH profiles` set own `role = clinic_admin` | blocked; role still `staff` |
   | `PATCH profiles` set own `organization_id = <B>` | blocked; org unchanged |
   | `create_invitation(...)` | rejected (not `clinic_admin`) |
   | `PATCH` a Clinic B profile | 0 rows |

4. **Symmetric:** signed in as Clinic B, only Clinic B rows are visible.

5. **`platform_admin`:** sees organizations and profiles from *both* clinics.

6. **Anonymous** (anon key, no session): no organizations, no profiles.

### Expected output

```
ALL 29 CHECKS PASSED — cross-tenant isolation holds.
```

## Manual end-to-end (optional, matches the automated flow)

1. `npx supabase start`; run backend and frontend (`README.md` quick start).
2. `http://localhost:5173/signup` → sign up as `alice@clinic-a.test`.
3. Onboarding → "Clinic A" → lands on the empty dashboard scoped to Clinic A.
4. Team → invite `bob@clinic-a.test` → copy the invite link.
5. Incognito window → open the invite link → sign up as `bob@clinic-a.test` →
   accept → Bob lands on Clinic A's dashboard as `staff`.
6. Third window → sign up `carol@clinic-b.test` → onboarding → "Clinic B".
7. Carol's dashboard and Team page show **only** Clinic B. Alice's show only
   Clinic A. Supabase Studio (`http://localhost:54323`) shows all rows for both
   (service role) — the isolation is per-session, enforced by RLS.
