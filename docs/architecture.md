# Foresight — Architecture

_Last updated: 2026-09-02 · Phase 0 (foundation) + Phase 1 (claims & billing)_

> **Phase numbering.** Phase 0 = the multi-tenant foundation
> ([`PHASE-0.md`](PHASE-0.md)). Phase 1 = the claims & billing module
> ([`PHASE-1.md`](PHASE-1.md)), the first real feature and the first agents.
> Some prose below still says "Phase 1" where it means the foundation — treated
> as "Phase 0" until fully swept.

## 1. What Foresight is

An AI-powered patient access & revenue cycle platform for healthcare providers,
delivered as multi-clinic SaaS. Multiple independent clinics ("organizations")
use one deployment. They will eventually store real patient and insurance data,
so **tenant isolation is a first-class architectural requirement from day one.**

### The human-in-the-loop principle

> AI recommends or drafts. A human approves anything consequential. Automation
> executes only *after* approval.

This is a system-wide invariant. No agent added later may execute a consequential
action (submitting a claim, sending a patient communication, posting an
adjustment) without a recorded human approval. Phase 0 contained no agents; the
Phase 1 Commander (§6) is where this invariant is now enforced structurally.

## 2. Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | React + TypeScript + Vite + Tailwind | standard, fast iteration |
| Backend | Python + FastAPI | typed, async, good fit for later AI/agent orchestration |
| DB + Auth | Supabase (Postgres + Supabase Auth + Row Level Security) | RLS gives us tenant isolation *in the database*, not in app code; team has production experience with it |

**We do not build a custom auth system.** Supabase Auth issues the identities;
Postgres RLS is the enforcement boundary.

## 3. The multi-tenant model

### 3.1 Tenancy primitives

```
auth.users (Supabase-managed)
    │ 1:1
    ▼
public.profiles ──────────────► public.organizations
  id (= auth.users.id)            id
  organization_id  ──────────────┘
  role  ∈ {platform_admin, clinic_admin, staff}
  email, full_name
```

- **`organizations`** — one row per clinic. This is *the* tenant boundary.
- **`profiles`** — one row per user, created automatically by an
  `on_auth_user_created` trigger. Carries the user's `organization_id` and
  `role`. A freshly signed-up user has **both null** until they either create a
  clinic or accept an invitation.
- **Every future data table** (`claims`, `patients`, `appointments`, …) MUST:
  1. carry `organization_id uuid not null references public.organizations(id)`
  2. call `select public.enable_tenant_isolation('public.<table>');` in its
     migration.

  That helper installs the standard four policies (select/insert/update/delete),
  each of which reduces to: _your `organization_id` must equal the caller's, or
  the caller is `platform_admin`._

### 3.2 How RLS enforces isolation

Every request to Postgres — whether from the browser via PostgREST, or from the
FastAPI backend — arrives as one of the API roles (`anon`, `authenticated`,
`service_role`) with the user's JWT attached. RLS policies are evaluated for
**every row** of **every query**, using three `SECURITY DEFINER` helper
functions that read the caller's own profile:

| Function | Returns |
|----------|---------|
| `public.current_org_id()` | the caller's `organization_id` |
| `public.current_user_role()` | the caller's `role` |
| `public.is_platform_admin()` | `true` iff the caller's role is `platform_admin` |

They are `SECURITY DEFINER` and owned by a role with `BYPASSRLS` **on purpose**:
they must read `profiles` to answer "who is asking?" without triggering the very
policies that call them (which would recurse).

Policy shape, using `organizations` as the concrete example:

```sql
create policy "organizations_select_own_or_platform_admin"
  on public.organizations for select to authenticated
  using (id = public.current_org_id() or public.is_platform_admin());
```

Signed in as Clinic A, `SELECT * FROM organizations` returns exactly one row —
Clinic A's — no matter what `WHERE` clause the client adds. `WHERE id = '<clinic
B>'` returns zero rows. There is no query the client can write to widen this.

### 3.3 Writes go through vetted functions, not raw table access

The API roles are granted the **minimum** needed:

```sql
revoke all on public.organizations from anon, authenticated;
grant  select        on public.organizations to authenticated;
grant  update (name) on public.organizations to authenticated;   -- + RLS: clinic_admin, own org

revoke all on public.profiles from anon, authenticated;
grant  select            on public.profiles to authenticated;
grant  update (full_name) on public.profiles to authenticated;   -- role / org NOT grantable here
```

`organization_id` and `role` on `profiles` are assigned **only** inside
`SECURITY DEFINER` RPCs, each of which re-derives the caller server-side:

| RPC | Who may call | What it does |
|-----|--------------|--------------|
| `bootstrap_organization(org_name)` | any user with no org yet | create org, make caller its `clinic_admin` |
| `create_invitation(email, role)` | `clinic_admin` | create a pending invite scoped to caller's org (`role` may not be `platform_admin`) |
| `preview_invitation(token)` | any authed user | read-only "you've been invited to X as Y" |
| `accept_invitation(token)` | user with no org yet, email matches invite | join the inviting org with the invited role |
| `revoke_invitation(id)` | `clinic_admin` of that org | mark a pending invite revoked |

A defense-in-depth trigger (`protect_profile_privileged_columns`) additionally
hard-fails any attempt by the `authenticated`/`anon` roles to change
`profiles.role`, `profiles.organization_id`, or `profiles.id` — so even a future
mis-added column grant cannot become a privilege-escalation path.

### 3.4 The non-negotiable principle

> **Application code is never the only thing enforcing tenant isolation.**
> RLS at the database is the real guarantee.

Concretely:

- The FastAPI backend forwards the **user's own JWT** to PostgREST. It never
  swaps in the `service_role` key to serve a user request. If every line of
  backend scoping logic were deleted, Postgres would still refuse to return
  another clinic's rows.
- The frontend's Supabase client is the `anon` key + the user's session. Same
  guarantee.
- `service_role` (which *does* bypass RLS) is used only for out-of-band
  operations: creating the initial `platform_admin`, support tooling, batch
  jobs. It never touches a request path driven by end-user input.
- Tenant identity is **always derived from the verified session**, never read
  from a request body, query string, or header. Endpoints that accept an
  `organization_id` parameter ignore it.

This is validated on every change by `tests/isolation_test.py` (foundation) and
`tests/agent_isolation_test.py` (the agent pipeline) — see [`PHASE-0.md`](PHASE-0.md)
and [`PHASE-1.md`](PHASE-1.md).

## 4. Roles — what each can see and do

| Capability | `staff` | `clinic_admin` | `platform_admin` |
|---|---|---|---|
| See rows for **their own** organization | ✅ | ✅ | ✅ |
| See rows for **any** organization | ❌ | ❌ | ✅ (platform support) |
| Edit own `profiles.full_name` | ✅ | ✅ | ✅ |
| Rename their organization | ❌ | ✅ | ✅ (any org) |
| Invite / revoke teammates | ❌ | ✅ | — (out of band) |
| Change anyone's `role` / `organization_id` | ❌ | ❌ | ✅ via service role |
| Approve / decline a claim recommendation | ✅ | ✅ | ✅ |
| Created by | accepting an invite | `bootstrap_organization`, or invited as admin | service role / SQL only |

- **`staff`** — the default teammate. Day-to-day product user within one clinic.
- **`clinic_admin`** — runs one clinic's account: invites teammates, renames the
  org. Still fully tenant-scoped — a `clinic_admin` sees nothing outside their
  own organization.
- **`platform_admin`** — Foresight's own staff. The **only** role that reads
  across tenants, and only for platform support. Assigned deliberately and
  out-of-band; never obtainable through any client-facing flow.

## 5. Frontend gating

`AuthProvider` resolves a `status`: `loading → signedOut → noOrg → ready`. The
app shell (sidebar + dashboard) mounts **only** at `ready` — a real session with
a resolved `organization_id`. `signedOut` allows only `/login` and `/signup`;
`noOrg` allows only `/onboarding` and `/accept-invite`. No app data renders on a
half-resolved auth state.

This is a UX guard, not a security guard — the security guard is RLS (§3.2).

## 6. The agent system (Phase 1)

A single **Commander** (`00`) — a pure decision node, no tools, no content
generation — reads a claim's current state plus a triggering event and returns a
routing decision from an ordered rule table (terminal/safety guards first, then
a hard human-approval guard, then the analysis pipeline, then a
fallthrough-to-escalation). It routes to numbered specialist agents that each do
one job and return control:

| # | Agent | LLM? | Job |
|---|-------|------|-----|
| 06 | analyzer | no | deterministic rule engine — find issues, score risk (§6.1) |
| 07 | reasoning | **yes** | plain-language explanation of the issue list, strictly grounded |
| 08 | recommendation | no | issue list → action + confidence band + `low_confidence` |
| 09 | followup | no | execute an approved follow-up action (simulated send + logged record) |
| 10 | reminder | no | execute an approved payer-reminder action |
| 12 | escalation | no | safety net — log full context, flag for a human |

Full spec and rule table: [`agents/00-commander.md`](agents/00-commander.md).
Code: `backend/app/agents/`.

**The human-in-the-loop invariant is enforced here structurally.** Exactly one
rule (R9) routes to an execution agent, behind two guards (R7: an `approved`
recommendation must exist on an `awaiting_approval` claim; R8: it must not be
low-confidence). The one action that resubmits a claim to a payer —
`resubmit_corrected_coding` — has **no** agent-execution path: on approval it is
handed to a human (`manual_action_required`).

Agent processing is a **background job**, not a user request, so it runs with the
service-role key (§3.4's "batch job" carve-out). The compensating control: the
orchestrator resolves `organization_id` once from the triggering claim row and
threads it into every query — no hardcoded tenant id — and
`tests/agent_isolation_test.py` proves a run over one clinic's claim never reads
or writes another's rows.

### 6.1 Risk scoring (06 — analyzer-agent)

Pure function, no LLM. Documented here in prose; implemented in
`backend/app/agents/rules.py`. **Keep the two in lockstep.**

Each check applies only if the payer's config enables it. Each issue it finds
has a fixed severity:

| issue | fires when | severity | weight |
|-------|-----------|----------|--------|
| `missing_authorization` | `payer.authorization_required` **and not** `claim.authorization_present` | high | 50 |
| `missing_documentation` | `payer.documentation_required` **and not** `claim.documentation_present` | high | 50 |
| `code_mismatch` | **not** `claim.coding_matches` | medium | 30 |
| `overdue_follow_up` | `payer.follow_up_threshold_days` is set **and** the age since `claim.last_followup_at` (or `claim.created_at` if never followed up) exceeds it | low | 10 |

```
severity weights:  low = 10   medium = 30   high = 50
risk_score = min(100, Σ weights of the issues found)
risk_level = High    if risk_score >= 70
             Medium  if 40 <= risk_score <= 69
             Low     if risk_score < 40
```

Worked: `missing_authorization` + `missing_documentation` = 100 → High.
`missing_authorization` + `code_mismatch` = 80 → High. `code_mismatch` +
`overdue_follow_up` = 40 → Medium. `code_mismatch` alone = 30 → Low.

### 6.2 Recommendation confidence (08)

Also deterministic (no LLM). The highest-priority issue present drives the
action; the confidence band reflects how unambiguous that call is:

```
n = number of issues, driving = the top-priority issue
  n >= 3                                   -> Low     (compound problems; one action won't fix it)
  n == 2                                   -> Medium
  n == 1 and driving severity in {high,medium} -> High
  n == 1 and driving severity == low            -> Medium
low_confidence = (band == "Low")
```

Priority order: `missing_authorization` → `missing_documentation` →
`code_mismatch` → `overdue_follow_up`, mapping to
`submit_authorization_request` → `request_documentation` →
`resubmit_corrected_coding` → `payer_status_follow_up`.

## 7. Still not built

- Eligibility, prior-auth, patient comms, voice — agent numbers 01–05, 11 are
  reserved for these.
- Real external delivery — 09/10 do a **simulated** send in Phase 1.
- Payer adjudication sync (`denied`/`paid`/`rejected` are set only by the seed).
- A "mark manual action complete" flow out of `manual_action_required`.
- Role management UI, SSO, audit-log surfacing, org deletion.

## 8. Acceptance

- **Phase 0:** a user can sign up, create a clinic, invite a teammate, log in,
  and see a correctly-scoped dashboard — proven with two test clinics that
  cannot see each other's data. [`PHASE-0.md`](PHASE-0.md).
- **Phase 1:** a claim is analyzed, explained, and recommended on; a human
  approves or declines; the right agent (or human hand-off) executes; every row
  is tenant-scoped. [`PHASE-1.md`](PHASE-1.md).
