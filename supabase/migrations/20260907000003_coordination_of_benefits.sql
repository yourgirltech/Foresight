-- ============================================================================
-- Foresight — Phase 4: coordination of benefits (04-cob-agent)
-- ----------------------------------------------------------------------------
-- When a patient has 2+ active insurance coverages, which pays first? That is a
-- pure rules question — the NAIC model regulation spells out an ordered list of
-- tie-breakers and the answer is a mechanical walk down it. 04 is `rules.py`
-- (06) in a new domain: `backend/app/agents/cob.py::determine_cob()` is a pure
-- function, `docs/agents/04-cob-agent.md` §6 is the contract, keep them in
-- lockstep.
--
-- 04 REPORTS, it does not act (agent doc §2):
--   * patient_coverages is a PLAIN DATA TABLE — 04 reads it and computes; it
--     writes nothing back. Add / edit / remove a coverage is a human action
--     through an endpoint.
--   * nothing downstream is triggered by a COB result. A human reads the
--     insurance summary and can override the order (manual_order_override) when
--     they know something the data does not (a court decree, a card that says
--     otherwise).
--   * 04 is NOT a Commander agent — synchronous, invoked while rendering an
--     insurance summary. commander.py is untouched by Phase 4.
--
-- Same tenancy rule as every prior phase: organization_id not null +
-- select public.enable_tenant_isolation('public.patient_coverages').
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------
create type public.coverage_relationship as enum ('self', 'spouse', 'child', 'other');

create type public.coverage_type as enum (
  'employer_active',   -- group plan through a currently-employed subscriber
  'employer_retiree',  -- group plan through a retired subscriber
  'cobra',             -- COBRA continuation
  'individual',        -- individually purchased / marketplace
  'medicare',
  'medicaid',
  'tricare',
  'other'
);

-- ----------------------------------------------------------------------------
-- patient_coverages — one row per plan a patient is covered under.
--
-- Patient identity is the codebase's soft key: patient_name + patient_dob
-- within an org (same as appointments / eligibility / prior-auth). No patients
-- table yet — that is a cross-cutting refactor, out of scope for Phase 4.
--
-- "Active" is NOT stored — it is computed (effective_date <= today AND
-- (termination_date IS NULL OR termination_date >= today)) in the query and the
-- pure function. COB is computed WITHIN one plan_kind — medical and dental
-- coverages do not coordinate with each other.
-- ----------------------------------------------------------------------------
create table public.patient_coverages (
  id                         uuid primary key default gen_random_uuid(),
  organization_id            uuid not null references public.organizations (id) on delete cascade,
  appointment_id             uuid references public.appointments (id) on delete set null,   -- optional link
  patient_name               text not null,
  patient_dob                date,                              -- the PATIENT's dob (soft identity key)
  payer_id                   uuid references public.payers (id) on delete set null,         -- nullable: a coverage may name a payer not in the directory
  payer_name                 text not null,                     -- snapshot
  member_id                  text not null default '',
  group_number               text not null default '',
  plan_kind                  text not null default 'medical',   -- medical | dental | vision | rx (COB computed per kind)
  coverage_type              public.coverage_type not null default 'employer_active',
  relationship_to_subscriber public.coverage_relationship not null default 'self',
  is_dependent               boolean not null default false,    -- patient is covered as someone's dependent
  subscriber_name            text not null default '',
  subscriber_dob             date,                              -- needed for the birthday rule (R3)
  effective_date             date not null,
  termination_date           date,                              -- null => open
  manual_order_override      smallint,                          -- a human pins 1/2/3; NULL => let 04 decide (R0)
  created_at                 timestamptz not null default now()
);

create index patient_coverages_organization_id_idx on public.patient_coverages (organization_id);
create index patient_coverages_patient_idx         on public.patient_coverages (organization_id, patient_name, patient_dob);
create index patient_coverages_appointment_id_idx  on public.patient_coverages (appointment_id);

comment on table public.patient_coverages is
  'One insurance plan a patient is covered under (Phase 4). 04-cob-agent reads these and computes primary/secondary/tertiary per plan_kind with the deciding NAIC rule cited; it writes nothing back. Plain data — add/edit/remove is a human action. Tenant-scoped.';
comment on column public.patient_coverages.manual_order_override is
  'A human pins this coverage to position 1/2/3. NULL => determine_cob() decides. A pinned coverage is cited as rule "R0 set by staff".';
comment on column public.patient_coverages.subscriber_dob is
  'The subscriber''s date of birth. Only month + day are used (year ignored) — the NAIC birthday rule (R3) for a dependent child on two parents'' plans.';

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.patient_coverages');

-- ============================================================================
-- API surface: revoke from client roles, grant back only reads. RLS narrows the
-- grant to the caller's own tenant. Every write goes through a backend endpoint
-- that re-derives the caller's org server-side (Phase 2-4 discipline).
-- ============================================================================
revoke all on public.patient_coverages from anon, authenticated;
grant  select on public.patient_coverages to authenticated;
