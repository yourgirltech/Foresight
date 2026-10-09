-- ============================================================================
-- Foresight — Phase 6 hardening: pre-dial consent authorization
-- ----------------------------------------------------------------------------
-- Before this migration the last consent check before a Vapi call was /due,
-- and the post-dial check was mark-calling (which needs the Vapi call id, so it
-- necessarily runs AFTER the call exists). A revocation between /due and the
-- dial could therefore reach the patient's phone.
--
-- This adds a single-use, atomic DIAL AUTHORIZATION that n8n must obtain
-- immediately before it calls Vapi (POST /api/automation/voice-reminders/{id}/
-- authorize-dial), and that mark-calling requires.
--
-- public.voice_authorize_dial() runs as ONE statement, so:
--   * it row-locks the reminder (FOR UPDATE) — serialised against every other
--     status transition on that row;
--   * it reads the consent ledger with a fresh statement snapshot — every
--     revocation COMMITTED before the call is seen;
--   * it fails closed on a recorded_at tie (any revoked row at the latest
--     recorded_at => revoked);
--   * it is single-use (dial_authorized_at must be NULL) — a replayed or
--     concurrent authorize can never authorize a second dial;
--   * it refuses an expired lease (the lost-dispatch sweep owns that row).
--
-- The precise guarantee this buys (docs/agents/17-voice-reminder-agent.md §2.2):
-- no call is dialled unless voice consent was GRANTED in the committed ledger
-- at the authorization instant, which is the last step before the Vapi request.
-- A revocation committed AFTER that instant is caught by mark-calling's live
-- re-check, and n8n then ends the call via Vapi Live Call Control.
--
-- Same tenancy rule as every prior phase: no client grant. Only the
-- service_role (the backend's automation path) may execute it.
-- ============================================================================

alter table public.voice_reminders
  add column dial_authorized_at timestamptz;

comment on column public.voice_reminders.dial_authorized_at is
  'Set exactly once by public.voice_authorize_dial() — the pre-dial consent gate. mark-calling refuses a reminder without it.';

create or replace function public.voice_authorize_dial(
  p_reminder_id  uuid,
  p_lease_cutoff timestamptz
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  r           public.voice_reminders%rowtype;
  latest_at   timestamptz;
  all_granted boolean;
begin
  select * into r from public.voice_reminders where id = p_reminder_id for update;
  if not found then
    return jsonb_build_object('authorized', false, 'reason', 'not_found');
  end if;

  if r.status <> 'dispatching' then
    return jsonb_build_object('authorized', false, 'reason', 'not_dispatching',
                              'status', r.status, 'organization_id', r.organization_id);
  end if;
  if r.dial_authorized_at is not null then
    return jsonb_build_object('authorized', false, 'reason', 'already_authorized',
                              'organization_id', r.organization_id);
  end if;
  if r.dispatched_at is null or r.dispatched_at < p_lease_cutoff then
    return jsonb_build_object('authorized', false, 'reason', 'lease_expired',
                              'organization_id', r.organization_id);
  end if;
  if r.authorized_by is null then
    return jsonb_build_object('authorized', false, 'reason', 'not_authorized',
                              'organization_id', r.organization_id);
  end if;

  -- live ledger, fail-closed: no row => no_consent; any revoked row at the
  -- latest recorded_at => consent_revoked.
  select max(recorded_at) into latest_at
    from public.patient_consents
   where organization_id = r.organization_id
     and patient_contact_id = r.patient_contact_id
     and channel = 'voice';
  if latest_at is null then
    return jsonb_build_object('authorized', false, 'reason', 'no_consent',
                              'organization_id', r.organization_id);
  end if;
  select bool_and(state = 'granted') into all_granted
    from public.patient_consents
   where organization_id = r.organization_id
     and patient_contact_id = r.patient_contact_id
     and channel = 'voice'
     and recorded_at = latest_at;
  if not coalesce(all_granted, false) then
    return jsonb_build_object('authorized', false, 'reason', 'consent_revoked',
                              'organization_id', r.organization_id);
  end if;

  if r.patient_phone_snapshot is null
     or r.patient_phone_snapshot !~ '^\+[1-9][0-9]{7,14}$' then
    return jsonb_build_object('authorized', false, 'reason', 'bad_phone_format',
                              'organization_id', r.organization_id);
  end if;

  update public.voice_reminders
     set dial_authorized_at = now()
   where id = p_reminder_id;

  return jsonb_build_object('authorized', true, 'reason', '',
                            'organization_id', r.organization_id);
end;
$$;

comment on function public.voice_authorize_dial(uuid, timestamptz) is
  'Phase 6 pre-dial consent gate. Atomic + single-use. service_role only. See docs/agents/17-voice-reminder-agent.md §2.2.';

revoke all on function public.voice_authorize_dial(uuid, timestamptz) from public, anon, authenticated;
grant execute on function public.voice_authorize_dial(uuid, timestamptz) to service_role;
