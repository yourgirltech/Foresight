-- ============================================================================
-- Foresight — Phase 1: auth wiring + onboarding / invite RPCs
-- ----------------------------------------------------------------------------
-- The client never writes organization_id or role directly. Those fields are
-- assigned only inside the SECURITY DEFINER functions below, each of which
-- re-derives the caller (auth.uid()) and their tenant server-side.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Every new auth.users row gets a matching profiles row (tenant unassigned).
-- ----------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, lower(new.email))
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ----------------------------------------------------------------------------
-- 2. Defense in depth: even though `authenticated` has no column grant on
--    profiles.role / profiles.organization_id, this BEFORE UPDATE trigger
--    hard-fails any attempt by the API roles to change privileged fields.
--    It runs as INVOKER, so current_user reflects PostgREST's role switch
--    ('authenticated' / 'anon'); the SECURITY DEFINER RPCs run as the table
--    owner and are unaffected.
-- ----------------------------------------------------------------------------
create or replace function public.protect_profile_privileged_columns()
returns trigger
language plpgsql
as $$
begin
  if current_user in ('authenticated', 'anon') then
    if new.organization_id is distinct from old.organization_id then
      raise exception 'organization_id cannot be changed directly'
        using errcode = '42501';
    end if;
    if new.role is distinct from old.role then
      raise exception 'role cannot be changed directly'
        using errcode = '42501';
    end if;
    if new.id is distinct from old.id then
      raise exception 'id is immutable' using errcode = '42501';
    end if;
  end if;
  new.updated_at := now();
  return new;
end;
$$;

create trigger profiles_protect_privileged
  before update on public.profiles
  for each row execute function public.protect_profile_privileged_columns();

-- ----------------------------------------------------------------------------
-- 3. bootstrap_organization — signup -> "create your clinic" -> become its admin
-- ----------------------------------------------------------------------------
create or replace function public.bootstrap_organization(org_name text)
returns public.organizations
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid          uuid := (select auth.uid());
  existing_org uuid;
  new_org      public.organizations;
begin
  if uid is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;

  if org_name is null or length(btrim(org_name)) = 0 then
    raise exception 'organization name is required' using errcode = '22023';
  end if;

  select organization_id into existing_org
  from public.profiles where id = uid;

  if existing_org is not null then
    raise exception 'user already belongs to an organization'
      using errcode = '42501';
  end if;

  insert into public.organizations (name)
  values (btrim(org_name))
  returning * into new_org;

  update public.profiles
     set organization_id = new_org.id,
         role            = 'clinic_admin'
   where id = uid;

  return new_org;
end;
$$;

-- ----------------------------------------------------------------------------
-- 4. create_invitation — clinic_admin invites a teammate (staff or clinic_admin)
-- ----------------------------------------------------------------------------
create or replace function public.create_invitation(
  invitee_email text,
  invitee_role  text default 'staff'
)
returns public.invitations
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid         uuid := (select auth.uid());
  caller      public.profiles;
  target_role public.user_role;
  inv         public.invitations;
begin
  select * into caller from public.profiles where id = uid;

  if caller.id is null or caller.organization_id is null then
    raise exception 'must belong to an organization' using errcode = '42501';
  end if;
  if caller.role <> 'clinic_admin' then
    raise exception 'only clinic_admin can invite teammates' using errcode = '42501';
  end if;
  if invitee_email is null or length(btrim(invitee_email)) = 0 then
    raise exception 'invitee email is required' using errcode = '22023';
  end if;

  begin
    target_role := lower(btrim(invitee_role))::public.user_role;
  exception when invalid_text_representation then
    raise exception 'invalid role: %', invitee_role using errcode = '22023';
  end;

  if target_role = 'platform_admin' then
    raise exception 'cannot invite a platform_admin' using errcode = '42501';
  end if;

  -- supersede any still-pending invite for the same address in this org
  update public.invitations
     set status = 'revoked'
   where organization_id = caller.organization_id
     and lower(email) = lower(btrim(invitee_email))
     and status = 'pending';

  insert into public.invitations (organization_id, email, role, invited_by)
  values (caller.organization_id, lower(btrim(invitee_email)), target_role, uid)
  returning * into inv;

  return inv;
end;
$$;

-- ----------------------------------------------------------------------------
-- 5. preview_invitation — the invite landing page: "You've been invited to X"
-- ----------------------------------------------------------------------------
create or replace function public.preview_invitation(invitation_token uuid)
returns table (
  organization_name text,
  role              public.user_role,
  email             text,
  status            public.invitation_status
)
language sql
stable
security definer
set search_path = ''
as $$
  select o.name, i.role, i.email, i.status
  from public.invitations i
  join public.organizations o on o.id = i.organization_id
  where i.token = invitation_token;
$$;

-- ----------------------------------------------------------------------------
-- 6. accept_invitation — invitee joins the inviting org, scoped to that org
-- ----------------------------------------------------------------------------
create or replace function public.accept_invitation(invitation_token uuid)
returns public.organizations
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid        uuid := (select auth.uid());
  user_email text;
  caller_org uuid;
  inv        public.invitations;
  org        public.organizations;
begin
  if uid is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;

  select organization_id into caller_org
  from public.profiles where id = uid;

  if caller_org is not null then
    raise exception 'user already belongs to an organization'
      using errcode = '42501';
  end if;

  select lower(email) into user_email from auth.users where id = uid;

  select * into inv
  from public.invitations
  where token = invitation_token
  for update;

  if inv.id is null then
    raise exception 'invitation not found' using errcode = 'P0002';
  end if;
  if inv.status <> 'pending' then
    raise exception 'invitation is no longer valid' using errcode = '42501';
  end if;
  if lower(inv.email) <> user_email then
    raise exception 'invitation was issued to a different email address'
      using errcode = '42501';
  end if;

  update public.profiles
     set organization_id = inv.organization_id,
         role            = inv.role
   where id = uid;

  update public.invitations
     set status      = 'accepted',
         accepted_at = now(),
         accepted_by = uid
   where id = inv.id;

  select * into org from public.organizations where id = inv.organization_id;
  return org;
end;
$$;

-- ----------------------------------------------------------------------------
-- 7. revoke_invitation — clinic_admin cancels a pending invite in their org
-- ----------------------------------------------------------------------------
create or replace function public.revoke_invitation(invitation_id uuid)
returns public.invitations
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid    uuid := (select auth.uid());
  caller public.profiles;
  inv    public.invitations;
begin
  select * into caller from public.profiles where id = uid;
  select * into inv from public.invitations where id = invitation_id;

  if inv.id is null then
    raise exception 'invitation not found' using errcode = 'P0002';
  end if;
  if caller.role <> 'clinic_admin' or caller.organization_id <> inv.organization_id then
    raise exception 'not authorized to revoke this invitation' using errcode = '42501';
  end if;

  update public.invitations
     set status = 'revoked'
   where id = inv.id
  returning * into inv;

  return inv;
end;
$$;

-- ----------------------------------------------------------------------------
-- Grants: the client-facing RPCs are callable by authenticated users only.
-- Each function re-checks the caller's tenant + role internally.
-- ----------------------------------------------------------------------------
revoke all on function public.bootstrap_organization(text)        from public, anon;
revoke all on function public.create_invitation(text, text)       from public, anon;
revoke all on function public.preview_invitation(uuid)            from public, anon;
revoke all on function public.accept_invitation(uuid)             from public, anon;
revoke all on function public.revoke_invitation(uuid)             from public, anon;

grant execute on function public.bootstrap_organization(text)     to authenticated;
grant execute on function public.create_invitation(text, text)    to authenticated;
grant execute on function public.preview_invitation(uuid)         to authenticated;
grant execute on function public.accept_invitation(uuid)          to authenticated;
grant execute on function public.revoke_invitation(uuid)          to authenticated;

-- enable_tenant_isolation is a schema tool, not a client RPC.
revoke all on function public.enable_tenant_isolation(regclass) from public, anon, authenticated;
