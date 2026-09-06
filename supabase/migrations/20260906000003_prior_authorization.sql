-- ============================================================================
-- Foresight — Phase 3: prior authorization (02-prior-auth-agent)
-- ----------------------------------------------------------------------------
-- Simulated prior authorization. No real payer integration yet — a deterministic
-- simulation over seeded payer config (docs/agents/02-prior-auth-agent.md §7),
-- the same way 06 is a deterministic engine and 01 simulates the clearinghouse.
--
-- THE NON-NEGOTIABLE (EMTALA-shaped): prior authorization must NEVER gate or
-- delay emergency / urgent care. Emergency services are exempt from prior auth
-- by law and by every payer contract. Prior auth MAY gate an *elective* service
-- (that is what it is for) — but Foresight never *sets* a gating state; it only
-- surfaces status. Enforced structurally, not by comment:
--   * prior_authorizations is a SIDECAR table — no care object has a not-null FK
--     to it, and no care workflow has a state meaning "waiting on authorization";
--   * prior_authorizations.is_emergency + place_of_service are snapshotted onto
--     every row so any query or test can select `where is_emergency` and prove
--     those rows produced no draft, no submission, no escalation;
--   * the Commander's next_status is NULL for every prior-auth rule (A1-A11) —
--     see docs/agents/00-commander.md §13 and tests/prior_auth_commander_test.py.
--
-- Same tenancy rules as Phases 1-2: every table carries
--   organization_id uuid not null references public.organizations(id)
-- and is run through  select public.enable_tenant_isolation('public.<table>').
-- No hardcoded tenant id anywhere. Agent writes use the service-role key
-- (batch-job carve-out, architecture.md §3.4) and filter every query by the
-- organization_id resolved from the triggering row.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Enum
-- ----------------------------------------------------------------------------
create type public.prior_auth_status as enum (
  'pending',            -- created; 02 has not run the determination yet
  'not_required',       -- determination: this payer + procedure needs no prior auth
  'emergency_exempt',   -- determination: emergency / urgent service — prior auth does not apply
  'insufficient_info',  -- not enough on the appointment to determine (missing procedure code / payer)
  'required_draft',     -- determination: auth IS required; a request packet is drafted, awaiting a human
  'submission_declined',-- a human reviewed the draft and chose not to submit it
  'submitting',         -- a human approved; 02.submit is running (simulated send)
  'submitted',          -- request sent to the (simulated) payer, awaiting their determination
  'auth_approved',      -- (simulated) payer approved — the service is authorized
  'info_needed',        -- (simulated) payer needs more clinical information — a human owns the resubmit
  'auth_denied'         -- (simulated) payer denied — a human owns the peer-to-peer / appeal
);

-- ----------------------------------------------------------------------------
-- prior_authorizations — one row per authorization attempt. A resubmit APPENDS
-- a new row (history, like eligibility_checks / recommendations); previous_auth_id
-- chains them.
--
-- SIDECAR: appointment_id is nullable BY DESIGN. Nothing in the schema requires
-- a prior_authorizations row to exist, or to be in any particular status, for
-- care to proceed.
-- ----------------------------------------------------------------------------
create table public.prior_authorizations (
  id                    uuid primary key default gen_random_uuid(),
  organization_id       uuid not null references public.organizations (id) on delete cascade,
  appointment_id        uuid references public.appointments (id) on delete cascade,           -- nullable BY DESIGN
  previous_auth_id      uuid references public.prior_authorizations (id) on delete set null,  -- append-only resubmit chain
  patient_name          text not null,                    -- snapshot at determination time
  patient_member_id     text not null default '',         -- snapshot ('' if unknown)
  payer_id              uuid references public.payers (id) on delete restrict,                -- nullable
  payer_name            text,                             -- snapshot
  procedure_code        text not null default '',         -- CPT / HCPCS; '' if unknown
  procedure_description  text not null default '',
  place_of_service      text not null default 'office'
    check (place_of_service in ('office', 'outpatient', 'inpatient', 'emergency')),
  is_emergency          boolean not null default false,   -- care-context snapshot — drives the safety invariant
  status                public.prior_auth_status not null default 'pending',
  determination_payload jsonb not null default '{}'::jsonb,  -- why auth is / isn't required (agent doc §7.4)
  request_payload       jsonb not null default '{}'::jsonb,   -- the drafted PA request packet (agent doc §7.5)
  response_payload      jsonb not null default '{}'::jsonb,   -- the (simulated) payer response (agent doc §7.6)
  authorization_number  text,                             -- set on auth_approved
  determined_at         timestamptz,                      -- set when 02.determine completes
  submitted_at          timestamptz,                      -- set when 02.submit completes
  resolved_at           timestamptz,                      -- set on any terminal status
  decided_by            uuid references auth.users (id) on delete set null,  -- the human who approved / declined submission
  created_at            timestamptz not null default now()
);

create index prior_authorizations_organization_id_idx  on public.prior_authorizations (organization_id);
create index prior_authorizations_appointment_id_idx   on public.prior_authorizations (appointment_id, created_at);
create index prior_authorizations_previous_auth_id_idx on public.prior_authorizations (previous_auth_id);
create index prior_authorizations_status_idx           on public.prior_authorizations (organization_id, status);
create index prior_authorizations_emergency_idx        on public.prior_authorizations (organization_id, is_emergency);

comment on table public.prior_authorizations is
  'One prior-authorization attempt (simulated in Phase 3). Sidecar to care: appointment_id nullable, never a precondition for anything; emergency services are emergency_exempt. Resubmits append new rows. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- payers — three new simulation knobs (the prior-auth equivalent of
-- eligibility_verification_supported / eligibility_active_threshold for 01).
-- Documented in docs/agents/02-prior-auth-agent.md §7.
-- ----------------------------------------------------------------------------
alter table public.payers
  add column prior_auth_supported          boolean not null default true,
  add column prior_auth_required_default   boolean not null default false,
  add column prior_auth_approval_threshold integer not null default 80
    check (prior_auth_approval_threshold between 0 and 100);

comment on column public.payers.prior_auth_supported is
  'Simulation: false => this payer has no electronic PA channel; drafts are flagged channel=manual_fax. Does NOT change whether auth is required.';
comment on column public.payers.prior_auth_required_default is
  'Simulation: true => this payer also requires auth for the ELECTIVE_AUTH_PROCEDURES set, on top of the always-auth set.';
comment on column public.payers.prior_auth_approval_threshold is
  'Simulation: a submitted request hashes to a stable bucket 0..99; bucket < threshold => approved, < threshold+12 => info_needed, else denied.';

-- ----------------------------------------------------------------------------
-- audit trail — activity_log / escalations gain a nullable reference to the
-- Phase 3 object. Phase 1 rows keep claim_id; Phase 2 rows use appointment_id /
-- eligibility_check_id; Phase 3 rows use prior_authorization_id. All nullable.
-- ----------------------------------------------------------------------------
alter table public.activity_log add column prior_authorization_id
  uuid references public.prior_authorizations (id) on delete cascade;
alter table public.escalations  add column prior_authorization_id
  uuid references public.prior_authorizations (id) on delete cascade;

create index activity_log_prior_authorization_id_idx on public.activity_log (prior_authorization_id, created_at);
create index escalations_prior_authorization_id_idx  on public.escalations  (prior_authorization_id);

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.prior_authorizations');

-- ============================================================================
-- API surface: revoke from client roles, grant back only reads. RLS narrows
-- every grant to the caller's own tenant. All prior-auth writes go through the
-- service role (agent path) or a backend endpoint that re-derives the caller's
-- org server-side — NO client write grants (Phase 2 discipline).
-- ============================================================================
revoke all on public.prior_authorizations from anon, authenticated;
grant  select on public.prior_authorizations to authenticated;
