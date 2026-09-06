-- ============================================================================
-- Foresight — public marketing: demo requests
-- ----------------------------------------------------------------------------
-- A pre-signup sales lead from the public marketing site. There is NO
-- organization yet, so this table is deliberately OUTSIDE the multi-tenant
-- RLS pattern (no organization_id, no enable_tenant_isolation).
--
-- It is locked down instead: RLS on with NO policies for anon/authenticated,
-- and all client grants revoked. The ONLY writer is the FastAPI endpoint
-- POST /api/demo-requests, which runs with the service-role key (bypasses RLS)
-- and validates every field server-side before inserting.
-- ============================================================================

create type public.demo_org_type as enum ('clinic', 'hospital', 'medical_group');

create table public.demo_requests (
  id                uuid primary key default gen_random_uuid(),
  organization_name text not null check (length(btrim(organization_name)) between 1 and 200),
  work_email        text not null check (length(work_email) between 3 and 320),
  organization_type public.demo_org_type not null,
  provider_count    integer check (provider_count is null or provider_count between 0 and 100000),
  goals             text check (goals is null or length(goals) <= 4000),
  source            text,                       -- e.g. 'landing_hero', 'nav'
  user_agent        text,
  created_at        timestamptz not null default now()
);

create index demo_requests_created_at_idx on public.demo_requests (created_at desc);

comment on table public.demo_requests is
  'Public marketing-site demo leads. NOT tenant-scoped (no org yet). Written only by the service-role via POST /api/demo-requests.';

-- Lock it down: RLS enabled, no policies, no client grants. service_role bypasses RLS.
alter table public.demo_requests enable row level security;
alter table public.demo_requests force row level security;
revoke all on public.demo_requests from anon, authenticated;
