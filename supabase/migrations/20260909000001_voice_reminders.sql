-- ============================================================================
-- Foresight — Phase 6: voice appointment reminders (agent 17, an n8n workflow)
-- ----------------------------------------------------------------------------
-- Agent 17 places an automated outbound reminder call ahead of an appointment,
-- via a Vapi assistant that already exists, and routes anything that is not a
-- clean "confirmed" to a human by creating an `escalations` row (the same table
-- 12-escalation-agent writes).
--
-- 17 is NOT a Commander agent. It is an n8n workflow that calls a narrow set of
-- FastAPI automation endpoints (/api/automation/voice-reminders/*). commander.py
-- and orchestrator.py are unchanged. Full spec:
-- docs/agents/17-voice-reminder-agent.md.
--
-- THE NON-NEGOTIABLES (17-voice-reminder-agent.md §2), enforced by the endpoints:
--   * a call is never placed without voice_reminders.authorized_by set (a human
--     enrolled it) AND consent_snapshot = true AND a valid E.164 phone;
--   * TCPA voice consent is a DURABLE, REVOCABLE, append-only ledger
--     (patient_consents) — re-checked against the LIVE ledger at /due and at
--     mark-calling; a revocation before the dial stops the call;
--   * call recording is disabled (assistant + recordingEnabled:false on every
--     call); only the structured end_reminder_call outcome + metadata is stored,
--     never transcript text or a recording URL;
--   * every write is org-scoped from the voice_reminders row; n8n holds no
--     Supabase credential.
--
-- Same tenancy rule as every prior phase: organization_id not null +
-- select public.enable_tenant_isolation('public.<table>'). No hardcoded tenant
-- id. No client write grant.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- organizations.timezone — the IANA zone the reminder call speaks the
-- appointment date/time in. Wrong zone => a mis-timed call to a real patient,
-- so this is added now, not deferred (17-voice-reminder-agent.md §11.5).
-- ----------------------------------------------------------------------------
alter table public.organizations
  add column timezone text not null default 'America/New_York';

comment on column public.organizations.timezone is
  'IANA timezone for rendering {{appointment_date}} / {{appointment_time}} in Phase 6 voice reminders. Default is an explicit placeholder; org settings will expose it later.';

-- ----------------------------------------------------------------------------
-- appointments.patient_phone — the number captured at scheduling. The voice
-- reminder enrollment flow copies it into a patient_contacts row (consent then
-- lives on the ledger, not here). Nullable.
-- ----------------------------------------------------------------------------
alter table public.appointments
  add column patient_phone text;   -- E.164, e.g. +14155550142

comment on column public.appointments.patient_phone is
  'Patient phone captured at scheduling (E.164). Phase 6 enrollment copies it into a patient_contacts row; TCPA consent for it lives in patient_consents, never here.';

-- ----------------------------------------------------------------------------
-- patient_contacts — one row per (patient soft-key + phone number) at a clinic.
-- Soft-keyed by name + dob, like the rest of the codebase.
-- ----------------------------------------------------------------------------
create table public.patient_contacts (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations (id) on delete cascade,
  patient_name     text not null,
  patient_dob      date,                          -- nullable; matched when present
  phone            text not null,                 -- E.164
  created_by       uuid references auth.users (id) on delete set null,
  created_at       timestamptz not null default now(),
  unique (organization_id, patient_name, patient_dob, phone)
);

create index patient_contacts_organization_id_idx on public.patient_contacts (organization_id);
create index patient_contacts_lookup_idx on public.patient_contacts (organization_id, patient_name, patient_dob);

comment on table public.patient_contacts is
  'A patient phone number on file at a clinic (soft-keyed by name + dob). Consent to contact it lives in patient_consents. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- patient_consents — APPEND-ONLY TCPA consent ledger.
-- Current consent for (contact, channel) = the row with the greatest recorded_at.
-- A revocation is a new row with state = 'revoked'.
-- ----------------------------------------------------------------------------
create type public.consent_channel as enum ('voice');            -- 'sms' / 'email' later
create type public.consent_state   as enum ('granted', 'revoked');

create table public.patient_consents (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  patient_contact_id uuid not null references public.patient_contacts (id) on delete cascade,
  channel            public.consent_channel not null default 'voice',
  state              public.consent_state   not null,
  source             text not null,               -- granted: 'intake_form'|'patient_portal'|'verbal_documented'
                                                  -- revoked: 'patient_request'|'staff_correction'|'returned_call_opt_out'
  note               text,
  recorded_by        uuid references auth.users (id) on delete set null,
  recorded_at        timestamptz not null default now(),
  created_at         timestamptz not null default now()
);

create index patient_consents_organization_id_idx on public.patient_consents (organization_id);
create index patient_consents_contact_idx on public.patient_consents (patient_contact_id, channel, recorded_at desc);

comment on table public.patient_consents is
  'Append-only TCPA consent ledger. Current consent for (contact, channel) = greatest recorded_at; a revocation is a new state=revoked row. Phase 6 voice reminders will not place a call unless the current voice-channel state is granted (fail-closed). Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- voice_reminders — one automated reminder-call attempt.
-- ----------------------------------------------------------------------------
create type public.voice_reminder_status as enum (
  'pending',              -- enrolled + authorized by a human; not yet dispatched
  'skipped_no_consent',   -- no current granted voice consent — no call, escalated
  'skipped_no_phone',     -- no usable E.164 number — no call, escalated
  'cancelled',            -- a human cancelled before dispatch
  'dispatching',          -- returned to n8n by /due, lease held; awaiting mark-calling
  'calling',              -- n8n placed the Vapi call; vapi_call_id recorded
  'confirmed',            -- end_reminder_call outcome = confirmed — the ONLY clean terminal
  'reschedule_requested', -- outcome = reschedule_needed — escalated
  'wrong_person',         -- outcome = wrong_person — escalated
  'out_of_scope',         -- outcome = out_of_scope — escalated
  'no_answer',            -- call ended with no structured outcome — escalated
  'call_failed',          -- n8n could not place the call, or Vapi reported failure — escalated
  'error'                 -- our side failed (bad tz, bad data, classify error) — escalated
);

create table public.voice_reminders (
  id                        uuid primary key default gen_random_uuid(),
  organization_id           uuid not null references public.organizations (id) on delete cascade,
  appointment_id            uuid not null references public.appointments (id) on delete cascade,
  patient_contact_id        uuid not null references public.patient_contacts (id) on delete restrict,

  -- snapshots at enrollment — the call decision + the spoken variables read THESE
  patient_name_snapshot     text not null,
  patient_phone_snapshot    text not null,          -- E.164, from the contact at enrollment
  clinic_name_snapshot      text not null,          -- organizations.name at enrollment == the {{clinic_name}} value
  appointment_at_snapshot   timestamptz not null,
  timezone_snapshot         text not null,          -- organizations.timezone at enrollment
  consent_snapshot          boolean not null default false,   -- resolved voice consent AT ENROLLMENT
  consent_event_id_snapshot uuid references public.patient_consents (id) on delete set null,
  consent_source_snapshot   text,

  status                    public.voice_reminder_status not null default 'pending',
  scheduled_call_at         timestamptz not null,   -- default: appointment - voice_reminder_lead_hours
  dispatched_at             timestamptz,            -- set when /due leases the row to 'dispatching'

  authorized_by             uuid references auth.users (id) on delete set null,   -- the HITL approval
  authorized_at             timestamptz,

  vapi_assistant_id         text,
  vapi_phone_number_id      text,
  vapi_call_id              text,                   -- the ONLY join key the outcome path trusts
  variable_values           jsonb not null default '{}'::jsonb,   -- exactly the 4 vars, verbatim, for audit

  outcome                   text,                   -- raw end_reminder_call string, or NULL
  outcome_payload           jsonb not null default '{}'::jsonb,   -- structured only; NO transcript / recording

  placed_at                 timestamptz,
  completed_at              timestamptz,
  escalation_id             uuid references public.escalations (id) on delete set null,
  created_at                timestamptz not null default now()
);

create index voice_reminders_organization_id_idx    on public.voice_reminders (organization_id);
create index voice_reminders_appointment_id_idx     on public.voice_reminders (appointment_id, created_at);
create index voice_reminders_status_idx             on public.voice_reminders (organization_id, status);
create index voice_reminders_due_idx                on public.voice_reminders (status, scheduled_call_at);
create index voice_reminders_dispatch_idx           on public.voice_reminders (status, dispatched_at);
create unique index voice_reminders_vapi_call_id_idx on public.voice_reminders (vapi_call_id) where vapi_call_id is not null;

comment on table public.voice_reminders is
  'One automated outbound appointment-reminder call attempt (Phase 6, an n8n workflow). A call is NEVER placed without authorized_by (a human), consent_snapshot=true, and a valid E.164 phone; consent is re-checked against the live patient_consents ledger at /due and mark-calling. Stores the structured outcome only. Tenant-scoped.';

-- ----------------------------------------------------------------------------
-- audit trail — activity_log / escalations gain nullable references
-- ----------------------------------------------------------------------------
alter table public.activity_log
  add column patient_contact_id uuid references public.patient_contacts (id) on delete cascade,
  add column voice_reminder_id  uuid references public.voice_reminders  (id) on delete cascade;
alter table public.escalations
  add column voice_reminder_id  uuid references public.voice_reminders  (id) on delete cascade;

create index activity_log_patient_contact_id_idx on public.activity_log (patient_contact_id, created_at);
create index activity_log_voice_reminder_id_idx  on public.activity_log (voice_reminder_id, created_at);
create index escalations_voice_reminder_id_idx   on public.escalations  (voice_reminder_id);

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.patient_contacts');
select public.enable_tenant_isolation('public.patient_consents');
select public.enable_tenant_isolation('public.voice_reminders');

-- ============================================================================
-- API surface: reads only for client roles; every write is backend-mediated.
-- n8n gets NO grant of any kind here — it never touches Postgres, only the
-- /api/automation/voice-reminders/* endpoints.
-- ============================================================================
revoke all on public.patient_contacts from anon, authenticated;
revoke all on public.patient_consents from anon, authenticated;
revoke all on public.voice_reminders  from anon, authenticated;

grant select on public.patient_contacts to authenticated;
grant select on public.patient_consents to authenticated;
grant select on public.voice_reminders  to authenticated;
