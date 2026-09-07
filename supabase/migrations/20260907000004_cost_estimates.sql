-- ============================================================================
-- Foresight — Phase 4: cost estimate / Good Faith Estimate (05-cost-estimate-agent)
-- ----------------------------------------------------------------------------
-- A Good Faith Estimate under the No Surprises Act (45 CFR §149.610) for an
-- UNINSURED / SELF-PAY patient: price the planned procedures, phrase the total
-- in plain language, and render the NSA-mandated disclaimer verbatim.
--
-- THE NON-NEGOTIABLE (agent doc §2): 05 refuses to generate an estimate unless
-- BOTH gate conditions hold — staff affirmed `self_pay: true` AND the patient
-- has no active `patient_coverages` row (04's data). Enforced structurally:
--   * `cost_estimates.self_pay_confirmed` is snapshotted on every row — a query
--     can prove every stored estimate was gated;
--   * the number is computed by price() (pure) BEFORE the AI is called; the AI
--     response is never parsed for a dollar amount;
--   * the disclaimer is a versioned constant rendered verbatim, not model
--     output (`disclaimer_text` + `disclaimer_version` snapshotted per row);
--   * 05 bills nothing and commits nothing — it produces a document.
--   * 05 is NOT a Commander agent. commander.py is untouched by Phase 4.
--
-- Same tenancy rule as every prior phase: organization_id not null +
-- select public.enable_tenant_isolation(...).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- procedure_prices — one flat self-pay base price per procedure code.
-- Insurance-adjusted / contract pricing is a future refinement, not Phase 4.
-- ----------------------------------------------------------------------------
create table public.procedure_prices (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations (id) on delete cascade,
  procedure_code  text not null,
  description     text not null,
  base_price      numeric(12,2) not null check (base_price >= 0),
  active          boolean not null default true,
  updated_at      timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  unique (organization_id, procedure_code)
);
create index procedure_prices_organization_id_idx on public.procedure_prices (organization_id);

comment on table public.procedure_prices is
  'Flat self-pay base price per procedure code (Phase 4). 05-cost-estimate-agent sums these; the AI never computes or adjusts a number. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- cost_estimates — one stored Good Faith Estimate document.
--
-- SIDECAR: appointment_id is nullable. Nothing requires an estimate to exist.
-- Every row is gated: self_pay_confirmed is always true for a stored row.
-- ----------------------------------------------------------------------------
create table public.cost_estimates (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  appointment_id     uuid references public.appointments (id) on delete set null,
  patient_name       text not null,
  patient_dob        date,
  line_items         jsonb not null default '[]'::jsonb,   -- [{procedure_code, description, base_price}]
  subtotal           numeric(12,2) not null,               -- Σ base_price — computed, NEVER from the model
  currency           text not null default 'USD',
  patient_summary    text not null default '',             -- the AI plain-language paragraph (or the plain template)
  disclaimer_text    text not null,                        -- the NSA disclaimer, snapshotted verbatim
  disclaimer_version text not null,                        -- e.g. "nsa-gfe-2026-01" — which constant was used
  self_pay_confirmed boolean not null,                     -- E2 — always true for a stored row
  model              text,                                 -- which model phrased it (null if phrasing skipped/failed)
  generated_by       uuid references auth.users (id) on delete set null,
  created_at         timestamptz not null default now()
);
create index cost_estimates_organization_id_idx on public.cost_estimates (organization_id);
create index cost_estimates_appointment_id_idx  on public.cost_estimates (appointment_id, created_at);

comment on table public.cost_estimates is
  'One stored No Surprises Act Good Faith Estimate (Phase 4). Gated: self_pay_confirmed is always true. subtotal is computed by price(), never from the model. disclaimer_text is a versioned constant snapshotted verbatim. 05 bills nothing. Tenant-scoped.';
comment on column public.cost_estimates.disclaimer_version is
  'The NSA_GFE_DISCLAIMER_VERSION in force when this estimate was generated. Lets the disclaimer constant be revised (after legal sign-off) without rewriting stored estimates.';

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.procedure_prices');
select public.enable_tenant_isolation('public.cost_estimates');

-- ============================================================================
-- API surface: reads only for client roles; every write is backend-mediated
-- (Phase 2-4 discipline). RLS narrows the grant to the caller's own tenant.
-- ============================================================================
revoke all on public.procedure_prices from anon, authenticated;
grant  select on public.procedure_prices to authenticated;

revoke all on public.cost_estimates from anon, authenticated;
grant  select on public.cost_estimates to authenticated;
