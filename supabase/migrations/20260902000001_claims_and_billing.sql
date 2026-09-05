-- ============================================================================
-- Foresight — Phase 1: claims & billing module
-- ----------------------------------------------------------------------------
-- The first real feature on top of the Phase 0 multi-tenant foundation.
--
-- Every table here:
--   1. carries  organization_id uuid not null references public.organizations(id)
--   2. is run through  select public.enable_tenant_isolation('public.<table>')
--
-- No hardcoded tenant id anywhere. RLS at the database is the enforcement
-- boundary, exactly as in Phase 0 (see docs/architecture.md §3).
--
-- Agent write paths use the service-role key (background jobs, not user
-- requests — the "batch job" carve-out in architecture.md §3.4) and MUST filter
-- every query by the organization_id taken from the triggering claim. That is
-- proven by tests/agent_isolation_test.py.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------
create type public.claim_status as enum (
  'received',                -- ingested, not yet analyzed
  'analyzed',                -- 06 ran, >=1 issue found
  'cleared',                 -- 06 ran, no issues — nothing to do
  'reasoned',                -- 07 ran, explanation stored
  'awaiting_approval',       -- 08 ran, a recommendation is pending a human
  'executing',               -- an execution agent (09/10) is running
  'actioned',                -- agent execution completed successfully
  'manual_action_required',  -- recommendation approved; a human must perform the action
  'declined',                -- a human declined the recommendation
  'escalated',               -- routed to 12 (error / unrecognised state)
  'denied',                  -- external: payer denied
  'paid',                    -- external: payer paid
  'rejected'                 -- external: clearinghouse/payer rejected pre-adjudication
);

create type public.risk_level as enum ('Low', 'Medium', 'High');

create type public.claim_issue_type as enum (
  'missing_authorization',
  'missing_documentation',
  'code_mismatch',
  'overdue_follow_up'
);

create type public.claim_issue_severity as enum ('low', 'medium', 'high');

create type public.recommendation_action as enum (
  'submit_authorization_request',  -- 09-followup (agent-executed after approval)
  'request_documentation',         -- 09-followup
  'payer_status_follow_up',         -- 10-reminder
  'resubmit_corrected_coding'       -- MANUAL only — never agent-executed (docs/agents/00-commander.md §6.3.1)
);

create type public.confidence_band as enum ('High', 'Medium', 'Low');

create type public.recommendation_approval as enum ('pending', 'approved', 'declined');

create type public.follow_up_kind as enum ('follow_up', 'payer_reminder');

-- ----------------------------------------------------------------------------
-- payers — one row per insurance payer, per clinic. Carries the config the
-- deterministic rule engine (06) checks a claim against.
-- ----------------------------------------------------------------------------
create table public.payers (
  id                      uuid primary key default gen_random_uuid(),
  organization_id         uuid not null references public.organizations (id) on delete cascade,
  name                    text not null check (length(btrim(name)) > 0),
  authorization_required  boolean not null default false,
  documentation_required  boolean not null default false,
  follow_up_threshold_days integer check (follow_up_threshold_days is null or follow_up_threshold_days > 0),
  created_at              timestamptz not null default now(),
  unique (organization_id, name)
);

create index payers_organization_id_idx on public.payers (organization_id);

comment on table public.payers is
  'Insurance payer + the rule-engine config for its claims. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- claims
-- ----------------------------------------------------------------------------
create table public.claims (
  id                    uuid primary key default gen_random_uuid(),
  organization_id       uuid not null references public.organizations (id) on delete cascade,
  claim_id              text not null check (length(btrim(claim_id)) > 0),  -- human-facing, e.g. CLM-2026-00042
  payer_id              uuid not null references public.payers (id) on delete restrict,
  patient_name          text not null,
  patient_member_id     text not null,
  amount                numeric(12, 2) not null check (amount >= 0),
  status                public.claim_status not null default 'received',

  -- written by 06 (deterministic rule engine)
  risk_score            integer not null default 0 check (risk_score between 0 and 100),
  risk_level            public.risk_level,

  -- evidence flags the rule engine reads (see docs/architecture.md §Risk scoring)
  authorization_present boolean not null default false,
  documentation_present boolean not null default false,
  coding_matches        boolean not null default true,
  last_followup_at      timestamptz,

  -- written by 07 (reasoning agent — grounded plain-language explanation)
  reasoning_summary     text,
  reasoning_detail      jsonb,   -- [{issue_type, explanation}, ...]
  reasoning_generated_at timestamptz,

  created_at            timestamptz not null default now(),
  unique (organization_id, claim_id)
);

create index claims_organization_id_idx on public.claims (organization_id);
create index claims_payer_id_idx        on public.claims (payer_id);
create index claims_status_idx          on public.claims (organization_id, status);

-- ----------------------------------------------------------------------------
-- claim_issues — one row per issue 06 finds on a claim
-- ----------------------------------------------------------------------------
create table public.claim_issues (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations (id) on delete cascade,
  claim_id        uuid not null references public.claims (id) on delete cascade,
  issue_type      public.claim_issue_type not null,
  severity        public.claim_issue_severity not null,
  description     text not null,
  evidence        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);

create index claim_issues_organization_id_idx on public.claim_issues (organization_id);
create index claim_issues_claim_id_idx        on public.claim_issues (claim_id);

-- ----------------------------------------------------------------------------
-- recommendations — one row per recommendation 08 produces for a claim
-- ----------------------------------------------------------------------------
create table public.recommendations (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations (id) on delete cascade,
  claim_id         uuid not null references public.claims (id) on delete cascade,
  action_type      public.recommendation_action not null,
  confidence       public.confidence_band not null,
  low_confidence   boolean not null,
  rationale        text not null,
  cited_issue_types public.claim_issue_type[] not null default '{}',
  approval_status  public.recommendation_approval not null default 'pending',
  decided_at       timestamptz,
  decided_by       uuid references auth.users (id) on delete set null,
  created_at       timestamptz not null default now()
);

create index recommendations_organization_id_idx on public.recommendations (organization_id);
create index recommendations_claim_id_idx        on public.recommendations (claim_id);

-- ----------------------------------------------------------------------------
-- follow_ups — the logged record an execution agent writes (09 -> 'follow_up',
-- 10 -> 'payer_reminder'). Phase 1 does a simulated send only; sent_at marks it.
-- ----------------------------------------------------------------------------
create table public.follow_ups (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations (id) on delete cascade,
  claim_id         uuid not null references public.claims (id) on delete cascade,
  kind             public.follow_up_kind not null,
  note             text not null,
  due_at           timestamptz not null,
  originating_agent text not null,
  simulated_send   boolean not null default true,
  sent_at          timestamptz,
  created_at       timestamptz not null default now()
);

create index follow_ups_organization_id_idx on public.follow_ups (organization_id);
create index follow_ups_claim_id_idx        on public.follow_ups (claim_id);

-- ----------------------------------------------------------------------------
-- escalations — 12's output. Also used for approved manual actions
-- (reason_code = 'approved_manual_action').
-- ----------------------------------------------------------------------------
create table public.escalations (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations (id) on delete cascade,
  claim_id         uuid references public.claims (id) on delete cascade,  -- nullable by design
  reason_code      text not null,
  originating_agent text not null,
  context          jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now()
);

create index escalations_organization_id_idx on public.escalations (organization_id);
create index escalations_claim_id_idx        on public.escalations (claim_id);

-- ----------------------------------------------------------------------------
-- activity_log — append-only audit trail. Every Commander decision and every
-- agent action lands here.
-- ----------------------------------------------------------------------------
create table public.activity_log (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations (id) on delete cascade,
  claim_id         uuid references public.claims (id) on delete cascade,  -- nullable by design
  actor            text not null,   -- '00-commander', '06-analyzer', 'human:<uuid>', ...
  action           text not null,   -- reason_code / agent verb
  details          jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now()
);

create index activity_log_organization_id_idx on public.activity_log (organization_id);
create index activity_log_claim_id_idx        on public.activity_log (claim_id, created_at);

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard on every table
-- ============================================================================
select public.enable_tenant_isolation('public.payers');
select public.enable_tenant_isolation('public.claims');
select public.enable_tenant_isolation('public.claim_issues');
select public.enable_tenant_isolation('public.recommendations');
select public.enable_tenant_isolation('public.follow_ups');
select public.enable_tenant_isolation('public.escalations');
select public.enable_tenant_isolation('public.activity_log');

-- ============================================================================
-- API surface: revoke everything from the client roles, then grant back only
-- what the frontend legitimately reads. RLS (above) then narrows every grant
-- to the caller's own tenant.
--
--   * Reads: the claim list / detail pages read these tables directly through
--     PostgREST with the user's JWT.
--   * Writes: all agent writes go through the service role. The ONE client
--     write is a human approving / declining a recommendation — a narrow
--     column grant on recommendations, still gated by tenant RLS. Even a
--     direct PostgREST PATCH there cannot cause an execution: nothing runs
--     until the backend calls the orchestrator, and the Commander re-checks
--     the approval (00-commander.md R7/R8).
-- ============================================================================
revoke all on public.payers          from anon, authenticated;
revoke all on public.claims          from anon, authenticated;
revoke all on public.claim_issues    from anon, authenticated;
revoke all on public.recommendations from anon, authenticated;
revoke all on public.follow_ups      from anon, authenticated;
revoke all on public.escalations     from anon, authenticated;
revoke all on public.activity_log    from anon, authenticated;

grant select on public.payers          to authenticated;
grant select on public.claims          to authenticated;
grant select on public.claim_issues    to authenticated;
grant select on public.recommendations to authenticated;
grant select on public.follow_ups      to authenticated;
grant select on public.escalations     to authenticated;
grant select on public.activity_log    to authenticated;

-- The human approve/decline write. Only these three columns; never action_type,
-- confidence, low_confidence, organization_id, claim_id.
grant update (approval_status, decided_at, decided_by) on public.recommendations to authenticated;
