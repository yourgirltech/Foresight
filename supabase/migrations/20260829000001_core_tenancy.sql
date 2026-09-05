-- ============================================================================
-- Foresight — Phase 1: core multi-tenant foundation
-- ----------------------------------------------------------------------------
-- This migration defines the tenancy primitives and the Row Level Security
-- (RLS) policies that are THE enforcement boundary between clinics.
--
-- Non-negotiable rule for this codebase: application code is never the only
-- thing standing between one clinic and another clinic's data. RLS at the
-- database is the real guarantee. Every tenant-scoped table gets RLS, FORCEd,
-- before it is exposed to the API roles.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------
create type public.user_role as enum ('platform_admin', 'clinic_admin', 'staff');

create type public.invitation_status as enum ('pending', 'accepted', 'revoked');

-- ----------------------------------------------------------------------------
-- organizations  (a clinic / customer tenant)
-- ----------------------------------------------------------------------------
create table public.organizations (
  id         uuid primary key default gen_random_uuid(),
  name       text not null check (length(btrim(name)) > 0),
  created_at timestamptz not null default now()
);

comment on table public.organizations is
  'A clinic. The tenant boundary. Every tenant-scoped row points back here via organization_id.';

-- ----------------------------------------------------------------------------
-- profiles  (one per auth.users row; carries the user's tenant + role)
-- ----------------------------------------------------------------------------
create table public.profiles (
  id              uuid primary key references auth.users (id) on delete cascade,
  organization_id uuid references public.organizations (id) on delete restrict,
  role            public.user_role,
  email           text not null,
  full_name       text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

comment on table public.profiles is
  'Extends auth.users. organization_id + role are assigned ONLY through the '
  'SECURITY DEFINER onboarding/invite RPCs, never by direct client writes.';

-- A freshly signed-up user has a profile with NULL organization_id and NULL
-- role until they either create a clinic (bootstrap_organization) or accept an
-- invitation (accept_invitation).

create index profiles_organization_id_idx on public.profiles (organization_id);

-- ----------------------------------------------------------------------------
-- invitations  (clinic_admin invites a teammate by email)
-- ----------------------------------------------------------------------------
create table public.invitations (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations (id) on delete cascade,
  email           text not null check (length(btrim(email)) > 0),
  role            public.user_role not null default 'staff',
  status          public.invitation_status not null default 'pending',
  token           uuid not null unique default gen_random_uuid(),
  invited_by      uuid references auth.users (id) on delete set null,
  accepted_by     uuid references auth.users (id) on delete set null,
  created_at      timestamptz not null default now(),
  accepted_at     timestamptz,
  constraint invitations_role_not_platform check (role <> 'platform_admin')
);

create index invitations_organization_id_idx on public.invitations (organization_id);
create index invitations_email_idx on public.invitations (lower(email));
create unique index invitations_one_pending_per_email_per_org
  on public.invitations (organization_id, lower(email))
  where status = 'pending';

-- ============================================================================
-- Session helpers
-- ----------------------------------------------------------------------------
-- All SECURITY DEFINER and owned by the migration role, which has BYPASSRLS.
-- That is deliberate: these read `profiles` to answer "who is the caller and
-- which tenant are they in?" without tripping the very RLS policies that
-- depend on them (which would recurse).
-- search_path is pinned to '' so every reference must be schema-qualified.
-- ============================================================================
create or replace function public.current_org_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select organization_id from public.profiles where id = (select auth.uid());
$$;

create or replace function public.current_user_role()
returns public.user_role
language sql
stable
security definer
set search_path = ''
as $$
  select role from public.profiles where id = (select auth.uid());
$$;

create or replace function public.is_platform_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select role = 'platform_admin' from public.profiles where id = (select auth.uid())),
    false
  );
$$;

comment on function public.is_platform_admin() is
  'platform_admin is the ONLY role that reads across tenants — for platform '
  'support. It is assigned out-of-band (service role / SQL), never via the '
  'client-facing RPCs.';

-- ============================================================================
-- Reusable tenant-isolation policy pack for FUTURE data tables
-- ----------------------------------------------------------------------------
-- Every future data table (claims, patients, appointments, ...) MUST:
--   1. carry  organization_id uuid not null references public.organizations(id)
--   2. call   select public.enable_tenant_isolation('public.<table>');
--
-- That single call is the standard tenant guard: a user sees/writes only rows
-- whose organization_id matches their own; platform_admin sees all.
-- ============================================================================
create or replace function public.enable_tenant_isolation(target_table regclass)
returns void
language plpgsql
as $$
declare
  tbl text := target_table::text;
begin
  execute format('alter table %s enable row level security', tbl);
  execute format('alter table %s force  row level security', tbl);

  execute format($p$
    create policy "tenant_isolation_select" on %s
      for select to authenticated
      using (organization_id = public.current_org_id() or public.is_platform_admin())
  $p$, tbl);

  execute format($p$
    create policy "tenant_isolation_insert" on %s
      for insert to authenticated
      with check (organization_id = public.current_org_id())
  $p$, tbl);

  execute format($p$
    create policy "tenant_isolation_update" on %s
      for update to authenticated
      using      (organization_id = public.current_org_id() or public.is_platform_admin())
      with check (organization_id = public.current_org_id() or public.is_platform_admin())
  $p$, tbl);

  execute format($p$
    create policy "tenant_isolation_delete" on %s
      for delete to authenticated
      using (organization_id = public.current_org_id() or public.is_platform_admin())
  $p$, tbl);
end;
$$;

-- ============================================================================
-- Lock down the API surface: revoke everything from the anon/authenticated
-- roles, then grant back only the precise columns the app needs. RLS then
-- narrows those grants to the caller's own tenant.
-- service_role is intentionally left untouched (it bypasses RLS and is used
-- only server-side for admin/support operations).
-- ============================================================================
revoke all on public.organizations from anon, authenticated;
revoke all on public.profiles      from anon, authenticated;
revoke all on public.invitations   from anon, authenticated;

grant select            on public.organizations to authenticated;
grant update (name)      on public.organizations to authenticated;   -- + RLS: clinic_admin, own org only

grant select             on public.profiles to authenticated;
grant update (full_name)  on public.profiles to authenticated;       -- role/org are NOT grantable here

grant select on public.invitations to authenticated;                 -- writes go through RPCs only

grant execute on function public.current_org_id()      to authenticated;
grant execute on function public.current_user_role()   to authenticated;
grant execute on function public.is_platform_admin()   to authenticated;

-- ============================================================================
-- RLS — organizations
-- ============================================================================
alter table public.organizations enable row level security;
alter table public.organizations force  row level security;

create policy "organizations_select_own_or_platform_admin"
  on public.organizations
  for select to authenticated
  using (id = public.current_org_id() or public.is_platform_admin());

create policy "organizations_update_clinic_admin_or_platform_admin"
  on public.organizations
  for update to authenticated
  using (
    (id = public.current_org_id() and public.current_user_role() = 'clinic_admin')
    or public.is_platform_admin()
  )
  with check (
    (id = public.current_org_id() and public.current_user_role() = 'clinic_admin')
    or public.is_platform_admin()
  );

-- No INSERT / DELETE policy: organizations are created only by
-- public.bootstrap_organization() (SECURITY DEFINER). A direct client INSERT
-- is denied.

-- ============================================================================
-- RLS — profiles
-- ============================================================================
alter table public.profiles enable row level security;
alter table public.profiles force  row level security;

create policy "profiles_select_self_same_org_or_platform_admin"
  on public.profiles
  for select to authenticated
  using (
    id = (select auth.uid())
    or organization_id = public.current_org_id()
    or public.is_platform_admin()
  );

create policy "profiles_update_self_or_platform_admin"
  on public.profiles
  for update to authenticated
  using (id = (select auth.uid()) or public.is_platform_admin())
  with check (id = (select auth.uid()) or public.is_platform_admin());

-- No INSERT policy: the profile row is created by the on_auth_user_created
-- trigger. No DELETE policy: profiles are removed by cascade from auth.users.

-- ============================================================================
-- RLS — invitations
-- ============================================================================
alter table public.invitations enable row level security;
alter table public.invitations force  row level security;

create policy "invitations_select_within_org_or_platform_admin"
  on public.invitations
  for select to authenticated
  using (organization_id = public.current_org_id() or public.is_platform_admin());

-- No write policies: every mutation goes through a SECURITY DEFINER RPC
-- (create_invitation / accept_invitation / revoke_invitation) that re-derives
-- the caller's tenant and role server-side.
