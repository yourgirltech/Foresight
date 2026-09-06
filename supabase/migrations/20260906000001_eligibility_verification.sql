-- ============================================================================
-- Foresight — Phase 2: eligibility verification (01-eligibility-agent)
-- ----------------------------------------------------------------------------
-- Simulated eligibility verification. No real clearinghouse call yet — a
-- deterministic simulation against seeded payer config (docs/agents/
-- 01-eligibility-agent.md §7), the same way 06 is a deterministic engine.
--
-- THE NON-NEGOTIABLE (EMTALA-shaped): eligibility verification must NEVER gate
-- or delay emergency care. This is enforced structurally, not by comment:
--   * eligibility_checks is a SIDECAR table — no care object has a not-null FK
--     to it, and no care workflow has a state meaning "waiting on eligibility";
--   * eligibility_checks.is_emergency is snapshotted onto every row so any query
--     or test can select `where is_emergency` and prove those checks gated
--     nothing;
--   * the Commander's next_status is NULL for every eligibility rule (E1-E7) —
--     see docs/agents/00-commander.md §12 and tests/eligibility_commander_test.py.
--
-- Same tenancy rules as Phase 1: every table carries
--   organization_id uuid not null references public.organizations(id)
-- and is run through  select public.enable_tenant_isolation('public.<table>').
-- No hardcoded tenant id anywhere. Agent writes use the service-role key
-- (batch-job carve-out, architecture.md §3.4) and filter every query by the
-- organization_id resolved from the triggering row.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Enum
-- ----------------------------------------------------------------------------
create type public.eligibility_status as enum (
  'pending',            -- created, 01 has not run yet
  'verified_active',    -- simulation: active coverage with this payer
  'verified_inactive',  -- simulation: coverage found but not active (lapsed / termed)
  'insufficient_info',  -- not enough identity to check — EXPECTED for unidentified patients, NOT an error
  'check_failed'        -- the (simulated) verification channel could not complete the check
);

-- ----------------------------------------------------------------------------
-- appointments — a scheduled patient encounter. An emergency / unscheduled
-- registration does NOT create a row here (per the Phase 2 review): it creates
-- an eligibility_checks row directly with appointment_id = null.
-- ----------------------------------------------------------------------------
create table public.appointments (
  id                uuid primary key default gen_random_uuid(),
  organization_id   uuid not null references public.organizations (id) on delete cascade,
  patient_name      text not null,
  patient_member_id text not null default '',       -- '' / 'UNKNOWN' allowed
  patient_dob       date,
  payer_id          uuid references public.payers (id) on delete restrict,   -- nullable
  scheduled_at      timestamptz,                     -- NULL => unscheduled
  is_emergency      boolean not null default false,
  created_at        timestamptz not null default now()
);

create index appointments_organization_id_idx on public.appointments (organization_id);
create index appointments_scheduled_at_idx    on public.appointments (organization_id, scheduled_at);

comment on table public.appointments is
  'A scheduled patient encounter. Eligibility is verified ahead of time and only informs staff — it never gates the visit. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- eligibility_checks — one row per verification attempt. A re-check APPENDS a
-- new row (history, like recommendations); previous_check_id chains them.
--
-- SIDECAR: appointment_id is nullable BY DESIGN (an ER registration has no
-- appointment). Nothing in the schema requires an eligibility_checks row to
-- exist, or to be in any particular status, for care to proceed.
-- ----------------------------------------------------------------------------
create table public.eligibility_checks (
  id                uuid primary key default gen_random_uuid(),
  organization_id   uuid not null references public.organizations (id) on delete cascade,
  appointment_id    uuid references public.appointments (id) on delete cascade,          -- nullable BY DESIGN
  previous_check_id uuid references public.eligibility_checks (id) on delete set null,   -- append-only re-check chain
  patient_name      text not null,                   -- snapshot at check time
  patient_member_id text not null default '',        -- snapshot ('' if unknown)
  payer_id          uuid references public.payers (id) on delete restrict,               -- nullable
  payer_name        text,                            -- snapshot
  is_emergency      boolean not null default false,  -- care-context snapshot — drives the safety invariant
  status            public.eligibility_status not null default 'pending',
  result_payload    jsonb not null default '{}'::jsonb,
  checked_at        timestamptz,                     -- set when 01 completes
  created_at        timestamptz not null default now()
);

create index eligibility_checks_organization_id_idx   on public.eligibility_checks (organization_id);
create index eligibility_checks_appointment_id_idx    on public.eligibility_checks (appointment_id, created_at);
create index eligibility_checks_previous_check_id_idx on public.eligibility_checks (previous_check_id);
create index eligibility_checks_emergency_idx         on public.eligibility_checks (organization_id, is_emergency);

comment on table public.eligibility_checks is
  'One eligibility verification attempt (simulated in Phase 2). Sidecar to care: appointment_id nullable, never a precondition for anything. Re-checks append new rows. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- payers — two new simulation knobs (the eligibility equivalent of
-- authorization_required / follow_up_threshold_days for 06). Documented in
-- docs/agents/01-eligibility-agent.md §7.
-- ----------------------------------------------------------------------------
alter table public.payers
  add column eligibility_verification_supported boolean not null default true,
  add column eligibility_active_threshold       integer not null default 85
    check (eligibility_active_threshold between 0 and 100);

comment on column public.payers.eligibility_verification_supported is
  'Simulation: false => this payer is not on the real-time eligibility network; every check returns check_failed.';
comment on column public.payers.eligibility_active_threshold is
  'Simulation: a member hashes to a stable bucket 0..99; bucket < threshold => verified_active, else verified_inactive.';

-- ----------------------------------------------------------------------------
-- audit trail — activity_log / escalations gain nullable references to the
-- Phase 2 objects. Phase 1 rows keep claim_id; Phase 2 rows use these; all
-- three are nullable.
-- ----------------------------------------------------------------------------
alter table public.activity_log
  add column appointment_id       uuid references public.appointments (id)      on delete cascade,
  add column eligibility_check_id uuid references public.eligibility_checks (id) on delete cascade;

alter table public.escalations
  add column appointment_id       uuid references public.appointments (id)      on delete cascade,
  add column eligibility_check_id uuid references public.eligibility_checks (id) on delete cascade;

create index activity_log_eligibility_check_id_idx on public.activity_log (eligibility_check_id, created_at);
create index escalations_eligibility_check_id_idx  on public.escalations  (eligibility_check_id);

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.appointments');
select public.enable_tenant_isolation('public.eligibility_checks');

-- ============================================================================
-- API surface: revoke from client roles, grant back only reads. RLS narrows
-- every grant to the caller's own tenant. All eligibility writes go through the
-- service role (agent path); appointments are created by the seed and a backend
-- endpoint that re-derives the caller's org server-side.
-- ============================================================================
revoke all on public.appointments       from anon, authenticated;
revoke all on public.eligibility_checks from anon, authenticated;

grant select on public.appointments       to authenticated;
grant select on public.eligibility_checks to authenticated;
