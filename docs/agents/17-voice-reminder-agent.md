# 17 — Voice Reminder Agent

_Spec. Written before implementation, per the Phase 1–5 discipline. Cross-check
this document — and the §15 addendum to [`00-commander.md`](00-commander.md) —
before any Phase 6 code is written._

_Status: **SPEC — awaiting review (rev. 2, five decisions folded in — §12).**
Companion doc: [`../PHASE-6.md`](../PHASE-6.md) (to be written at build time).
Proposed code: `supabase/migrations/20260909000001_voice_reminders.sql`,
`backend/app/agents/voice_reminder.py`, `commander._decide_voice_reminder`
(VR1–VR14), `orchestrator.handle_voice_reminder`,
`backend/app/routers/voice_reminders.py`, `backend/app/routers/patient_contacts.py`,
`backend/app/routers/webhooks_vapi.py`,
`frontend/src/components/voice-reminder.tsx` on `AppointmentDetailPage`, seed
`scripts/seed_voice_reminders.py`, tests as in §11. `commander_test.py` /
`eligibility_commander_test.py` / `prior_auth_commander_test.py` /
`appeals_commander_test.py` unchanged._

---

## 1. What 17 is

The **Voice Reminder Agent** places an **automated outbound phone call** to a
patient ahead of a scheduled appointment, using the **Vapi assistant that already
exists** (§3), to get one of two answers — _the patient confirms_, or _the
patient wants to reschedule_ — and routes everything that is not a clean
confirmation to a human via 12-escalation, the same way a denied prior-auth (A10)
and an upheld appeal (AP10) are routed.

It reacts to a real lifecycle event (a human enrolling an appointment for a
reminder; the reminder's scheduled time arriving), advances a small state machine
behind **two preconditions** (a durable TCPA voice-consent record for the number;
a recorded human authorization), performs the call, and processes a **structured
outcome** returned by Vapi's `end_reminder_call` tool. That shape makes it a
**Commander agent** — the **fifth disjoint family** (VR1–VR14), dispatched by an
early branch in `decide()` before R1, exactly like eligibility (E1–E7), prior-auth
(A1–A11), and appeals (AP1–AP12).

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 17 | **voice-reminder-agent** | trigger + real external executor | **no** (the LLM is Vapi's, hosted there — not called by us) | enrolled appointment → (voice consent + human auth) → real outbound call via the existing Vapi assistant → structured outcome → `confirmed` records and stops; anything else → 12-escalation |

### 1.1 This is the first agent that performs a **real external action**

Every prior phase **simulated** the leg that leaves the building:

| agent | external leg | Phase |
|-------|--------------|-------|
| 09 / 10 | `_simulated_send()` — always succeeds, writes a `follow_ups` row | 1 |
| 01 | `eligibility.simulate()` — deterministic, no clearinghouse | 2 |
| 02 | `prior_auth.simulate_response()` — deterministic payer | 3 |
| 11 | `appeals.simulate_resolution()` — deterministic payer | 5 |
| **17** | **`voice_reminder.place_call()` — a real HTTPS call to `api.vapi.ai` that makes a real phone ring** | **6** |

Three things follow, and they are the reason this phase is spec-first:

1. **The human-in-the-loop invariant is load-bearing here, not ceremonial.**
   `architecture.md` §1.2: _"No agent … may execute a consequential action
   (submitting a claim, **sending a patient communication**, posting an
   adjustment) without a recorded human approval."_ An outbound call to a patient
   is a patient communication. 17 does not place a call until a human has
   **authorized that specific reminder** and that authorization is recorded on
   the row (`authorized_by`, `authorized_at`). This is the structural analogue of
   R7/R8 → R9, A5 → A7, and AP4 → AP6. **Decision §12.2: the per-appointment
   enrollment IS that recorded approval — approved. We do not additionally gate
   each individual call.**

2. **TCPA consent is a legal precondition, not a nicety.** An automated /
   prerecorded call to a patient's phone requires **prior express consent** for
   that number. Consent is a property of the **patient + number**, not of one
   appointment — it must be durable, auditable, and revocable — so Phase 6 stores
   it in a dedicated **`patient_consents` ledger** (§4.1, **decision §12.3**), not
   on the appointment. 17 treats the absence of a current `granted` voice-consent
   record as a hard stop (**fail-closed** — §2.2), the opposite of the emergency
   fail-safe in Phases 2–3 (which fails _open_, toward not blocking care). Consent
   is re-checked against the **live ledger** at call time, so a revocation between
   enrollment and the call stops the call (VR7).

3. **A live call's audio and transcript are high-sensitivity.** **Decision
   §12.4: call recording is disabled entirely.** The Vapi assistant has recording
   turned off, and every `place_call` additionally sends
   `assistantOverrides.artifactPlan.recordingEnabled = false` as defence in depth
   (§6.4). We keep the **structured `end_reminder_call` outcome** and call
   metadata (duration, ended reason, Vapi call id, cost) — and **never** persist
   the transcript text or a recording URL (§4.3). New rows land in
   [`../BEFORE-PHI.md`](../BEFORE-PHI.md): TCPA voice-consent capture + retention,
   the recording-disabled attestation, the Vapi BAA, and `VAPI_WEBHOOK_SECRET` in
   secrets management.

---

## 2. The non-negotiables

### 2.1 Nothing calls a patient without a recorded human authorization

> A `voice_reminders` row does not reach `calling` unless `authorized_by` is set
> — the id of the staff user who enrolled/authorized this reminder through
> `POST /api/appointments/{id}/voice-reminder`. The call-placing step
> (`17.place_call`, reached only via VR4 / VR6) asserts it.

| # | Mechanism | Where |
|---|-----------|-------|
| V1 | **VR4 / VR6 are the only rules that route to `17-voice-reminder`**, and both require `context.can_call` (which folds in `authorized_by`) — §6, §15. | §6.3, §15.5 |
| V2 | **`orchestrator.handle_voice_reminder` hard-`raise`s** if `17.place_call` is about to run and `vr.authorized_by is None` or `vr.consent_snapshot is not True`. | §9 |
| V3 | **The authorization is a dedicated endpoint write** — `POST /api/appointments/{id}/voice-reminder` records `authorized_by` from the verified session, never from a request body. No client write grant on `voice_reminders`. | §10.1 |

### 2.2 No call without a current TCPA voice-consent record — and consent **fails closed**

> `context.can_call` is `True` **only** when, at the moment of the decision, the
> patient's number has a `patient_consents` row for channel `voice` whose latest
> state is `granted` (not `revoked`, not absent), **and** that number is a
> syntactically valid E.164 string. Missing consent, a `revoked` latest state,
> missing number, `NULL`, malformed number, or any ambiguity → `can_call = False`
> → **no call**, and the reminder is recorded `skipped_no_consent` /
> `skipped_no_phone` and routed to a human (VR2 / VR3 / VR7).

| # | Mechanism | Where |
|---|-----------|-------|
| C1 | **`voice_reminder.current_voice_consent()` + `consent_gate()` are pure functions that run BEFORE anything else.** `current_voice_consent()` reduces a contact's `patient_consents` rows to a single current state; `consent_gate()` returns `can_call: bool` + a reason. Their default for missing/None input is `False`. | §6.2, §6.3 |
| C2 | **The orchestrator resolves `context.can_call` fail-closed** at every decision point — a mirror of `_resolve_emergency` but **inverted**: any ambiguity → `False`. At `voice_reminder_due` it re-reads the **live** ledger, not the enrollment snapshot (C5). | §9 |
| C3 | **The authoritative consent state at enrollment is snapshotted onto the `voice_reminders` row** — `consent_snapshot` (bool), `consent_event_id_snapshot` (which `patient_consents` row was authoritative), `consent_source_snapshot`, `patient_phone_snapshot`. Full "why did we believe we could call" auditability. | §4.3 |
| C4 | **V2's hard-`raise`** also covers consent: `17.place_call` cannot run with `consent_snapshot is not True`. | §9 |
| C5 | **Revocation is honoured mid-flight.** A `patient_consents` `revoked` row inserted after enrollment but before the scheduled call: the due-scan fires `voice_reminder_due`, the orchestrator re-resolves `can_call` from the live ledger → `False`, the row is updated to `skipped_no_consent`, and VR7 routes it to a human. The call is never placed. | §6.7, §9, §15.5 |

### 2.3 The webhook never learns tenancy from its payload

> `POST /webhooks/vapi` is the **first unauthenticated write path in the system**
> (no Supabase JWT). It resolves `organization_id` for every write **only** by
> looking up `voice_reminders` on the `vapi_call_id` it stored when it created
> the call. A Vapi event whose `call.id` matches no row is acknowledged (`200`)
> and **ignored** — never processed, never used to create a row. Anything in the
> event body that looks like an org/clinic identifier is treated as untrusted
> display data.

| # | Mechanism | Where |
|---|-----------|-------|
| W1 | **Shared-secret verification.** Every request must carry `X-Vapi-Secret` equal to `settings.vapi_webhook_secret` (constant-time compare). Mismatch / absent → `401`, no row read or written. | §8.1, §10.2 |
| W2 | **`vapi_call_id` is the only join key.** The handler does `db.voice_reminder_by_call_id(call_id)`; `None` → log `vapi_webhook_unmatched_call` and return `200`. | §8.2 |
| W3 | **`organization_id` comes from that row.** Every subsequent `db.*` call is filtered by it, exactly as in Phases 1–5. `tests/agent_isolation_test.py` gains a webhook slice (§11). | §8.2, §9 |
| W4 | **`vapi_call_id` is `unique`** — a replayed event updates the same row idempotently; it can never fan out. | §4.3 |

### 2.4 A reminder never changes a claim or an appointment

> `decision.next_status is None` for **every** VR rule (VR1–VR14). 17 writes only
> `voice_reminders`, `patient_consents` (grant/revoke is human-driven, not
> agent-driven), `activity_log`, and (via 12) `escalations`. Nothing on
> `appointments` or `claims` is ever touched. Same invariant as eligibility
> (§12.3) and prior-auth (§13.3); `orchestrator.handle_voice_reminder`
> hard-`raise`s on any non-None `next_status`.

### 2.5 Not a change to R1–R20 / E1–E7 / A1–A11 / AP1–AP12

The voice-reminder trigger family and rule block (VR1–VR14) are **disjoint**,
dispatched by a fifth early branch in `decide()` before R1 — exactly like the
prior four families. `commander_test.py` / `eligibility_commander_test.py` /
`prior_auth_commander_test.py` / `appeals_commander_test.py` still pass
byte-for-byte.

---

## 3. What already exists in Vapi (do **not** rebuild) vs. what we build

### 3.1 Already built, in Vapi's dashboard — treat as fixed infrastructure

| Thing | Identifier | Notes |
|-------|-----------|-------|
| Published assistant **"Healthcare Appointment Reminder"** | `VAPI_ASSISTANT_ID` = `ec0bcddf-b32e-4afb-982d-fd0fb852c7e4` | Its system prompt, voice, model, and turn-taking are configured **there**. We never send a prompt. |
| Dynamic variables in the prompt | `{{clinic_name}}`, `{{patient_name}}`, `{{appointment_date}}`, `{{appointment_time}}` | Currently empty — no API call has ever supplied them. **Our job is to fill exactly these four**, via `assistantOverrides.variableValues` on the call-create request. |
| Attached tool **`end_reminder_call`** | Tool ID `1629964c-f437-4548-b0b7-f81273e60e92` | Reports a structured outcome ∈ `{confirmed, reschedule_needed, wrong_person, out_of_scope}`. We **read** this; we do not define it. |
| Configured behaviour | automated-call disclosure up front · clinic-only identity (never says "Foresight" or names any platform) · identity check before revealing appointment details · accepts only a confirmation or a reschedule request · ends promptly on confirm / reschedule / wrong-person / out-of-scope · says _"a representative from {{clinic_name}} will reach out"_ for anything out of scope | All of this is **already in the assistant**. Phase 6 adds no conversational logic. If any of it needs to change, that is a dashboard edit, tracked separately. |
| **Recording** | **must be turned OFF on the assistant** (decision §12.4) | We rely on the structured `end_reminder_call` outcome, not audio. `place_call` also sends `recordingEnabled: false` as defence in depth (§6.4). Confirm the assistant's recording toggle is off as part of the Phase 6 checklist; add a BEFORE-PHI attestation row. |
| Credentials | `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_ASSISTANT_ID` in `backend/.env` | Already set. Phase 6 adds **`VAPI_WEBHOOK_SECRET`** (§5) and requires the Vapi **Server URL** to be pointed at `POST /webhooks/vapi` with that secret. |

### 3.2 What Phase 6 builds — our side, none of it exists yet

| # | Piece | File |
|---|-------|------|
| 1 | **Data model** — `patient_contacts` + `patient_consents` (the durable, revocable consent ledger); `voice_reminders` + `voice_reminder_status` enum; `organizations` gains `timezone`; `activity_log` gains `patient_contact_id` + `voice_reminder_id`, `escalations` gains `voice_reminder_id`. | `supabase/migrations/20260909000001_voice_reminders.sql` |
| 2 | **The agent** — pure `resolve_variables()` / `current_voice_consent()` / `consent_gate()` / `classify_outcome()` / `verify_webhook()` + the one real-I/O `place_call()`. | `backend/app/agents/voice_reminder.py` |
| 3 | **Commander** — a fifth disjoint rule block (VR1–VR14), dispatched before R1. | `backend/app/agents/commander.py`, spec §15 |
| 4 | **Orchestrator** — `handle_voice_reminder`, the V2 / consent / next_status hard-`raise`s, live-ledger consent re-resolution, the webhook-driven re-entry. | `backend/app/agents/orchestrator.py` |
| 5 | **Consent endpoints** (authed, RLS-scoped) — resolve a patient's contacts + current consent; create a contact; grant / revoke voice consent. | `backend/app/routers/patient_contacts.py` |
| 6 | **Enrollment / management endpoints** (authed, RLS-scoped). | `backend/app/routers/voice_reminders.py` |
| 7 | **The Vapi webhook receiver** (unauthenticated, shared-secret verified). | `backend/app/routers/webhooks_vapi.py` |
| 8 | **A due-scan** — emits `voice_reminder_due` for `pending` rows whose `scheduled_call_at <= now`; also reconciles stuck `calling` rows (§12.13). | `scripts/run_voice_reminder_due_scan.py` (cron, like the card-image purge) |
| 9 | **UI** — a consent panel + a Voice Reminder card on the appointment detail page + a small work queue. | `frontend/src/components/voice-reminder.tsx`, `patient-consent.tsx`, `AppointmentDetailPage.tsx` |
| 10 | **Seed + tests** — §11. | `scripts/seed_voice_reminders.py`, `tests/voice_reminder_*` |

---

## 4. Data model

New migration `supabase/migrations/20260909000001_voice_reminders.sql`. Same rules
as every prior phase: every new table carries
`organization_id uuid not null references public.organizations(id)` and runs
through `select public.enable_tenant_isolation('public.<table>')`. No hardcoded
tenant id. Agent writes use the service-role key and filter every query by the
`organization_id` resolved from the `voice_reminders` row.

### 4.1 `patient_contacts` + `patient_consents` — the consent ledger (decision §12.3)

Consent is a durable property of a **patient + phone number**, captured and
revoked by staff, and outlives any single appointment. It is modelled as a
contact row plus an **append-only** consent ledger — the same "history is
append-only, current state is the latest row" pattern as `eligibility_checks`
(`previous_check_id`), `prior_authorizations` (`previous_auth_id`), and `appeals`
(`previous_appeal_id`).

```sql
-- one row per (patient soft-key, phone number) within a clinic
create table public.patient_contacts (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations (id) on delete cascade,
  patient_name     text not null,
  patient_dob      date,                          -- nullable; matched when present (codebase soft-key convention)
  phone            text not null,                 -- E.164, e.g. +14155550142
  created_by       uuid references auth.users (id) on delete set null,
  created_at       timestamptz not null default now(),
  unique (organization_id, patient_name, patient_dob, phone)
);

create index patient_contacts_organization_id_idx on public.patient_contacts (organization_id);
create index patient_contacts_lookup_idx on public.patient_contacts (organization_id, patient_name, patient_dob);

comment on table public.patient_contacts is
  'A patient phone number on file at a clinic (soft-keyed by name + dob, like the rest of the codebase). Consent to contact this number lives in patient_consents. Tenant-scoped.';

create type public.consent_channel as enum ('voice');   -- 'sms' / 'email' are later phases
create type public.consent_state   as enum ('granted', 'revoked');

-- APPEND-ONLY. The current consent for (contact, channel) is the row with the
-- greatest recorded_at. No UPDATE, no DELETE in normal operation.
create table public.patient_consents (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  patient_contact_id uuid not null references public.patient_contacts (id) on delete cascade,
  channel            public.consent_channel not null default 'voice',
  state              public.consent_state   not null,
  source             text not null,               -- granted: 'intake_form' | 'patient_portal' | 'verbal_documented'
                                                  -- revoked: 'patient_request' | 'staff_correction' | 'returned_call_opt_out'
  note               text,
  recorded_by        uuid references auth.users (id) on delete set null,   -- the staff member who recorded it
  recorded_at        timestamptz not null default now(),
  created_at         timestamptz not null default now()
);

create index patient_consents_organization_id_idx on public.patient_consents (organization_id);
create index patient_consents_contact_idx on public.patient_consents (patient_contact_id, channel, recorded_at desc);

comment on table public.patient_consents is
  'Append-only TCPA consent ledger. Current consent for (patient_contact, channel) = the row with the greatest recorded_at. A revocation is a new row with state = revoked. 17-voice-reminder-agent will not place a call unless the current voice-channel state is granted (fail-closed, §2.2). Tenant-scoped.';
```

`current_voice_consent(contact_id)` (pure, §6.2) = the latest `patient_consents`
row for `(contact_id, 'voice')`; **granted** iff that row exists and
`state == 'granted'`. No rows → not granted.

### 4.2 `organizations` — a timezone for rendering the call variables (decision §12.5)

```sql
alter table public.organizations
  add column timezone text not null default 'America/New_York';   -- IANA tz for {{appointment_date}} / {{appointment_time}}
```

`{{appointment_date}}` / `{{appointment_time}}` must be spoken in the patient's
local (clinic) time. `appointments.scheduled_at` is `timestamptz` (UTC); without
a clinic tz we would read the appointment time wrong, and **decision §12.5** is
that the cost of a wrong-hour call to a real patient is too high to defer. The
default is an explicit placeholder; org settings expose it in a later phase.
`resolve_variables` **raises** (→ `error` → VR13, not a call) if the stored
timezone is not a valid IANA zone.

### 4.3 Enum + `voice_reminders`

```sql
create type public.voice_reminder_status as enum (
  'pending',              -- enrolled + authorized by a human; not yet placed (waiting for scheduled_call_at)
  'skipped_no_consent',   -- precondition failed: no current granted voice consent — no call placed, routed to a human (VR2/VR7)
  'skipped_no_phone',     -- precondition failed: no usable E.164 number — no call placed, routed to a human (VR3)
  'cancelled',            -- a human cancelled the reminder before it was placed
  'calling',              -- Vapi accepted the call-create; the call is live or ringing
  'confirmed',            -- end_reminder_call outcome = confirmed — the ONLY clean terminal
  'reschedule_requested', -- end_reminder_call outcome = reschedule_needed — routed to a human (VR10)
  'wrong_person',         -- end_reminder_call outcome = wrong_person — routed to a human; contact flagged (VR10)
  'out_of_scope',         -- end_reminder_call outcome = out_of_scope — routed to a human (VR10)
  'no_answer',            -- call ended with no structured outcome (voicemail / no pickup / early hangup) — routed to a human (VR11)
  'call_failed',          -- Vapi could not place or complete the call (VR12)
  'error'                 -- our side failed (bad data, place_call raised, classify raised, bad timezone) (VR13)
);

create table public.voice_reminders (
  id                       uuid primary key default gen_random_uuid(),
  organization_id          uuid not null references public.organizations (id) on delete cascade,
  appointment_id           uuid not null references public.appointments (id) on delete cascade,
  patient_contact_id       uuid not null references public.patient_contacts (id) on delete restrict,

  -- snapshots taken at enrollment — the call decision + the spoken variables read THESE
  patient_name_snapshot    text not null,
  patient_phone_snapshot   text not null,                    -- E.164, copied from the contact at enrollment
  clinic_name_snapshot     text not null,                    -- organizations.name at enrollment == the {{clinic_name}} value
  appointment_at_snapshot  timestamptz not null,             -- the scheduled_at we rendered into the variables
  timezone_snapshot        text not null,                    -- organizations.timezone at enrollment
  consent_snapshot         boolean not null default false,   -- resolved current voice consent AT ENROLLMENT
  consent_event_id_snapshot uuid references public.patient_consents (id) on delete set null,  -- which ledger row was authoritative
  consent_source_snapshot  text,

  status                   public.voice_reminder_status not null default 'pending',
  scheduled_call_at        timestamptz not null,             -- default: appointment - voice_reminder_lead_hours

  -- the recorded human authorization (V1/V3)
  authorized_by            uuid references auth.users (id) on delete set null,
  authorized_at            timestamptz,

  -- Vapi linkage
  vapi_assistant_id        text,                             -- snapshot of VAPI_ASSISTANT_ID used
  vapi_phone_number_id     text,
  vapi_call_id             text,                             -- Vapi's call id — the ONLY join key the webhook trusts (W2/W4)
  variable_values          jsonb not null default '{}'::jsonb,-- exactly the 4 vars we sent, verbatim, for audit

  -- outcome — structured only. NO transcript text, NO recording URL (§1.1 point 3, decision §12.4)
  outcome                  text,                             -- raw end_reminder_call string, or NULL
  outcome_payload          jsonb not null default '{}'::jsonb,-- {outcome, ended_reason, duration_seconds, vapi_call_status, cost}

  placed_at                timestamptz,
  completed_at             timestamptz,
  escalation_id            uuid references public.escalations (id) on delete set null,
  created_at               timestamptz not null default now()
);

create index voice_reminders_organization_id_idx    on public.voice_reminders (organization_id);
create index voice_reminders_appointment_id_idx     on public.voice_reminders (appointment_id, created_at);
create index voice_reminders_status_idx             on public.voice_reminders (organization_id, status);
create index voice_reminders_due_idx                on public.voice_reminders (status, scheduled_call_at);   -- the due-scan
create unique index voice_reminders_vapi_call_id_idx on public.voice_reminders (vapi_call_id) where vapi_call_id is not null;

comment on table public.voice_reminders is
  'One automated outbound appointment-reminder call attempt (Phase 6). Sidecar: appointments has no FK here. A call is NEVER placed without authorized_by set (a human) AND consent_snapshot = true AND a valid E.164 phone (17-voice-reminder-agent.md §2). Consent is re-checked against the live patient_consents ledger at call time. Stores the structured outcome only — never transcript or recording. Tenant-scoped.';
```

### 4.4 Audit trail — `activity_log` / `escalations` gain columns

```sql
alter table public.activity_log
  add column patient_contact_id uuid references public.patient_contacts (id) on delete cascade,
  add column voice_reminder_id  uuid references public.voice_reminders  (id) on delete cascade;
alter table public.escalations
  add column voice_reminder_id  uuid references public.voice_reminders  (id) on delete cascade;

create index activity_log_patient_contact_id_idx on public.activity_log (patient_contact_id, created_at);
create index activity_log_voice_reminder_id_idx  on public.activity_log (voice_reminder_id, created_at);
create index escalations_voice_reminder_id_idx   on public.escalations  (voice_reminder_id);
```

`patient_contact_id` on `activity_log` gives every consent grant / revocation a
first-class audit row (`actor = "human:<uid>"`, `action = "voice_consent_granted"`
/ `"voice_consent_revoked"`). Voice reminders are appointment-scoped and one
appointment can have more than one reminder over time, so `voice_reminder_id`
follows the eligibility / prior-auth FK precedent rather than the appeals
`context.*_id`-only approach (**decision §12.9: add the columns**).
`insert_activity` / `insert_escalation` gain the optional kwargs.

### 4.5 Isolation + grants

```sql
select public.enable_tenant_isolation('public.patient_contacts');
select public.enable_tenant_isolation('public.patient_consents');
select public.enable_tenant_isolation('public.voice_reminders');

revoke all on public.patient_contacts from anon, authenticated;
revoke all on public.patient_consents from anon, authenticated;
revoke all on public.voice_reminders  from anon, authenticated;

grant select on public.patient_contacts to authenticated;   -- UI reads, RLS-scoped
grant select on public.patient_consents to authenticated;   -- UI shows the consent history
grant select on public.voice_reminders  to authenticated;
```

No client write grant on any of the three (Phases 1–5 discipline). Consent
grant/revoke goes through the §10 endpoints (org from the verified session); the
webhook writes `voice_reminders` with the service-role key after W1/W2/W3.

---

## 5. Settings

```python
# app/config.py — all from the environment, never hardcoded
vapi_api_key: str = ""            # VAPI_API_KEY            (already set)
vapi_assistant_id: str = ""       # VAPI_ASSISTANT_ID       (already set — ec0bcddf-...)
vapi_phone_number_id: str = ""    # VAPI_PHONE_NUMBER_ID    (already set)
vapi_webhook_secret: str = ""     # VAPI_WEBHOOK_SECRET     (NEW — shared secret configured on the Vapi Server URL)
vapi_api_base: str = "https://api.vapi.ai"

voice_reminder_lead_hours: int = 24   # place the call this many hours before the appointment
```

Behavioural constants live in `backend/app/agents/voice_reminder.py` as **named
module constants** (Phase 4 discipline):

```python
CONFIRMED_OUTCOME     = "confirmed"
ESCALATING_OUTCOMES   = frozenset({"reschedule_needed", "wrong_person", "out_of_scope"})
OUTCOME_TO_STATUS     = {
    "confirmed":         "confirmed",
    "reschedule_needed": "reschedule_requested",
    "wrong_person":      "wrong_person",
    "out_of_scope":      "out_of_scope",
}
TERMINAL_ENDED_REASONS_NO_OUTCOME = frozenset({           # -> no_answer
    "customer-did-not-answer", "voicemail", "customer-busy",
    "customer-ended-call-before-outcome", "silence-timed-out",
})
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
VAPI_CALL_TIMEOUT_SECONDS = 15.0
NO_AUTORETRY = True   # decision §12.2 — a missed / failed reminder is ALWAYS a human decision, never an auto-retry
```

The exact Vapi `endedReason` strings above are **to be confirmed against a real
end-of-call-report at build time** (§12.10) — the classifier is written so an
unrecognised reason with no structured outcome falls to `no_answer` (→ VR11 →
human), never to `confirmed`.

---

## 6. The contract

### 6.1 Interfaces

```python
# backend/app/agents/voice_reminder.py
from dataclasses import dataclass

class VoiceReminderUnavailable(RuntimeError):
    """place_call could not reach Vapi / Vapi rejected the request. The
    orchestrator records status='call_failed' and emits voice_reminder_call_failed."""

@dataclass(frozen=True)
class ReminderVariables:
    clinic_name: str
    patient_name: str
    appointment_date: str      # e.g. "Tuesday, September 15"
    appointment_time: str      # e.g. "2:30 PM"
    def as_vapi_variable_values(self) -> dict: ...   # {"clinic_name": ..., ... } — exactly the 4 keys

@dataclass(frozen=True)
class VoiceConsent:
    granted: bool
    event_id: str | None       # the authoritative patient_consents row id, or None
    source: str | None
    recorded_at: str | None

@dataclass(frozen=True)
class ConsentDecision:
    can_call: bool
    reason: str                # "" when can_call; else "no_consent" | "consent_revoked" | "no_phone" | "bad_phone_format"

@dataclass(frozen=True)
class VapiCall:
    vapi_call_id: str
    vapi_call_status: str       # Vapi's returned status, e.g. "queued"

@dataclass(frozen=True)
class ReminderOutcome:
    status: str                 # a voice_reminder_status value
    outcome: str | None         # the raw end_reminder_call string, or None
    payload: dict               # {outcome, ended_reason, duration_seconds, vapi_call_status, cost}

def current_voice_consent(consent_rows: list[dict]) -> VoiceConsent: ...                              # PURE
def consent_gate(phone: str | None, consent: VoiceConsent) -> ConsentDecision: ...                    # PURE
def resolve_variables(appointment: dict, organization: dict, *, now=None) -> ReminderVariables: ...   # PURE
def classify_outcome(vapi_event: dict) -> ReminderOutcome: ...                                        # PURE
def verify_webhook(headers: dict, *, secret: str) -> bool: ...                                        # PURE (constant-time)

async def place_call(variables: ReminderVariables, phone_e164: str, *,
                     assistant_id: str, phone_number_id: str) -> VapiCall:
    """The ONE real-I/O function. POST {vapi_api_base}/call with recording OFF.
    Raises VoiceReminderUnavailable on missing key / network error / non-2xx."""
```

### 6.2 `current_voice_consent()` — pure (reduce the ledger to one state)

```
voice_rows = [r for r in consent_rows if r["channel"] == "voice"]
if not voice_rows:            return VoiceConsent(False, None, None, None)
latest = max(voice_rows, key=lambda r: r["recorded_at"])
return VoiceConsent(latest["state"] == "granted", latest["id"], latest["source"], latest["recorded_at"])
```

Append-only ledger → "current" is unambiguous: the most recent `recorded_at`.

### 6.3 `consent_gate()` — pure, fail-closed (C1)

```
phone = (phone or "").strip()
not consent.granted and consent.event_id is None -> ConsentDecision(False, "no_consent")
not consent.granted                              -> ConsentDecision(False, "consent_revoked")
not phone                                        -> ConsentDecision(False, "no_phone")
not E164_RE.match(phone)                         -> ConsentDecision(False, "bad_phone_format")
otherwise                                        -> ConsentDecision(True, "")
```

There is no branch that returns `can_call=True` from missing data. `reason`
distinguishes "never consented" from "consented then revoked" for the escalation
context and the UI.

### 6.4 `place_call()` — the one real external call, recording off

```
POST {vapi_api_base}/call
Authorization: Bearer {vapi_api_key}
{
  "assistantId": "{vapi_assistant_id}",                 # ec0bcddf-... — the existing assistant, unchanged
  "phoneNumberId": "{vapi_phone_number_id}",
  "customer": { "number": "{phone_e164}" },
  "assistantOverrides": {
    "variableValues": { ...the 4 keys from ReminderVariables... },
    "artifactPlan": { "recordingEnabled": false }       # decision §12.4 — defence in depth; the assistant is also set to not record
  }
}
```

- `httpx.AsyncClient`, `timeout = VAPI_CALL_TIMEOUT_SECONDS`, mirroring `db.py`'s
  client style.
- Missing `vapi_api_key` → `VoiceReminderUnavailable` (do not attempt the call).
- Non-2xx or network error → `VoiceReminderUnavailable`. **No retry** (§12.2 /
  §12.12) — a transient failure becomes `call_failed` → VR12 → a human, who may
  re-enroll.
- 2xx → `VapiCall(vapi_call_id=body["id"], vapi_call_status=body.get("status",""))`.
- **No fallback, no simulated send.** Unlike 09/10 there is no "always succeeds"
  stand-in. Tests monkeypatch `place_call` (§11).
- The exact override path for disabling recording (`artifactPlan.recordingEnabled`
  vs. a top-level `recordingEnabled`) is confirmed against Vapi's current API at
  build (§12.10); whichever it is, it is sent `false` on every call.

### 6.5 `classify_outcome()` — pure (maps Vapi's event → a status)

Input: a Vapi **server message** (`end-of-call-report` preferred; a live
`tool-calls` message is also handled — §8). Extraction (exact paths confirmed at
build — §12.10):

```
structured = the end_reminder_call arguments — from message.toolCalls / message.toolWithToolCallList
             / message.analysis.structuredData, first hit wins
raw        = structured.get("outcome")            # confirmed | reschedule_needed | wrong_person | out_of_scope
ended      = message.get("endedReason") or message["call"].get("endedReason")
dur        = message.get("durationSeconds") or derived from startedAt/endedAt

raw in OUTCOME_TO_STATUS                                     -> ReminderOutcome(OUTCOME_TO_STATUS[raw], raw, {...})
raw is None and (ended in TERMINAL_ENDED_REASONS_NO_OUTCOME
                 or any other value)                         -> ("no_answer", None, {...})   # fail toward human
message indicates the call could not be placed/connected     -> ("call_failed", None, {...})
```

`classify_outcome` **never** returns `confirmed` unless `raw == "confirmed"`
literally. Ambiguity → `no_answer` → VR11 → 12. It never reads or returns a
transcript or a recording URL even if present in the payload.

### 6.6 `verify_webhook()` — pure, constant-time (W1)

```
supplied = headers.get("x-vapi-secret") or ""
return bool(secret) and hmac.compare_digest(supplied, secret)
```

If Vapi is later configured to HMAC-sign the body, this also accepts a valid
`X-Vapi-Signature` over the raw body with `vapi_webhook_secret` as the key
(**open decision §12.6**). Absent/empty configured secret → always `False` (the
webhook is effectively closed until the secret is set).

### 6.7 The state machine

| status | set by | next |
|--------|--------|------|
| `pending` | the enroll endpoint (with `authorized_by`), after confirming a current granted voice consent | due-scan emits `voice_reminder_due` → the orchestrator re-checks the **live** ledger → VR6 (still granted) or VR7 (revoked) |
| `skipped_no_consent` | orchestrator, when the live `consent_gate` fails on `no_consent` / `consent_revoked` (C5) | VR2 / VR7 → route 12 |
| `skipped_no_phone` | orchestrator, on `no_phone` / `bad_phone_format` | VR3 → route 12 |
| `cancelled` | the cancel endpoint | terminal — no trigger |
| `calling` | orchestrator, after `place_call` returns a `vapi_call_id` | `voice_reminder_call_placed` → VR8 → `no_action`; then the webhook |
| `confirmed` | orchestrator, from `classify_outcome` | `voice_reminder_outcome_received` → VR9 → `no_action` (terminal, clean) |
| `reschedule_requested` / `wrong_person` / `out_of_scope` | orchestrator, from `classify_outcome` | `voice_reminder_outcome_received` → VR10 → route 12 |
| `no_answer` | orchestrator, from `classify_outcome` | VR11 → route 12 |
| `call_failed` | orchestrator, on `VoiceReminderUnavailable` or a Vapi failure event | `voice_reminder_call_failed` → VR12 → route 12 |
| `error` | orchestrator, on any other exception in the run (bad timezone, bad appointment data) | `voice_reminder_error` → VR13 → route 12 |

---

## 7. Where 17 sits

```
staff capture consent for a number      POST /api/patient-contacts  +  /consent  (state=granted, source, recorded_by)
   (durable, revocable — patient_consents ledger; NOT tied to an appointment)
        │
a human enrolls an appointment for a reminder
   POST /api/appointments/{id}/voice-reminder  { patient_contact_id, scheduled_call_at? }
        │  409 unless the contact has a CURRENT granted voice consent   (fail fast, clean UX)
        │  creates voice_reminders (pending): snapshots name/phone/clinic/appt-time/tz + consent state + consent_event_id
        │  records authorized_by = session user            (the HITL approval — §2.1)
        │
        ├─ within the lead window now  → trigger: voice_reminder_due
        └─ earlier                     → trigger: voice_reminder_enrolled → VR5 no_action (row waits)
        │
  ── cron: run_voice_reminder_due_scan.py  → voice_reminder_due for each pending row past scheduled_call_at
        │
   00 ── VR6 ──► orchestrator re-reads the LIVE patient_consents ledger  →  consent_gate()  [pure, fail-closed]
        │
        ├─ consent revoked since enrollment → status skipped_no_consent → 00 ── VR7 ──► 12   (C5 — no call)
        ├─ no phone / malformed             → status skipped_no_phone   → 00 ── VR3 ──► 12
        │
        └─ can_call → 17.place_call()  [REAL POST api.vapi.ai/call, existing assistant + our 4 vars, recording OFF]
                      status = calling, vapi_call_id stored
                      trigger: voice_reminder_call_placed → 00 ── VR8 ──► no_action
        │
   ── Vapi runs the call (its assistant, its LLM, its disclosure + identity check) ──
        │
   POST /webhooks/vapi   (X-Vapi-Secret verified; row found by vapi_call_id; org from the row)
        │  classify_outcome()  [pure]  — structured outcome only, no transcript/recording
        │  status ∈ {confirmed | reschedule_requested | wrong_person | out_of_scope | no_answer | call_failed}
        │  trigger: voice_reminder_outcome_received
        │
        ├─ confirmed          → 00 ── VR9  ──► no_action   (green "Patient confirmed" on the appointment)
        └─ anything else      → 00 ── VR10/VR11 ──► 12  (one escalation; NO auto-retry — decision §12.2)
```

No arrow writes `appointments` or `claims`. A revocation at any time is a new
`patient_consents` row; the next decision point re-reads it.

---

## 8. The webhook (`POST /webhooks/vapi`)

### 8.1 Request handling

1. Read raw body + headers. `verify_webhook(headers, secret=settings.vapi_webhook_secret)` → `False` ⇒ `401 {"detail":"bad signature"}`, nothing else runs (W1).
2. Parse JSON. Vapi wraps the payload as `{"message": {...}}`. Branch on `message.type`:
   - `end-of-call-report` — the terminal event. `classify_outcome(message)` → update the row + drive the Commander (§8.2).
   - `tool-calls` (a live `end_reminder_call` invocation) — record `outcome` on the row if not already terminal, and **respond** with `{"results": [{"toolCallId": <id>, "result": "acknowledged"}]}`. Do **not** drive the Commander from this message — wait for `end-of-call-report` so duration / ended-reason are known.
   - `status-update`, `hang`, anything else — `200 {"ok": true}`, no-op (optionally note `status-update` transitions in `activity_log`).
3. Any handler exception after verification → log, `200` (so Vapi does not hammer retries), leave the row for the reconcile pass (§12.13). A `500` is only returned if we cannot even parse.

### 8.2 The matched-row path (W2 / W3)

```
call_id = message["call"]["id"]  (or message["callId"] — confirm at build)
vr = await db.voice_reminder_by_call_id(call_id)          # service-role, unfiltered lookup by the unique key
if vr is None:
    log "vapi_webhook_unmatched_call", return 200         # W2 — never processed
org_id = vr["organization_id"]                            # W3 — the ONLY source of tenancy
outcome = voice_reminder.classify_outcome(message)
await db.update_voice_reminder(org_id, vr["id"], {
    "status": outcome.status, "outcome": outcome.outcome,
    "outcome_payload": outcome.payload, "completed_at": now,
})
await db.insert_activity(org_id, None, actor="17-voice-reminder", action="outcome",
                         appointment_id=vr["appointment_id"], voice_reminder_id=vr["id"],
                         details={"status": outcome.status, "outcome": outcome.outcome})
await orchestrator.handle_voice_reminder(vr["id"], {"type": "voice_reminder_outcome_received",
                                                    "payload": {"source": "webhook"}})
return 200
```

Nothing in `message` other than `call.id` influences a write. A test feeds a
payload carrying a foreign `orgId` / `clinicName` and asserts the write lands on
`vr["organization_id"]` (§11, W3 slice).

### 8.3 Registration

`webhooks_vapi.router` is included in `main.py` **without** any auth dependency —
the only such router. Mounted at `/webhooks/vapi`. Document loudly in the router
docstring that this is the first unauthenticated write path and why it is safe
(W1–W4).

---

## 9. The orchestrator (`handle_voice_reminder`)

`backend/app/agents/orchestrator.py` gains `handle_voice_reminder(vr_id, trigger)`,
a mirror of `handle_prior_auth`, reusing `MAX_INVOCATIONS` and the existing
machinery.

1. Load the `voice_reminders` row → resolve `organization_id` **once** from it
   (`db.get_voice_reminder`, the `get_claim` analogue). Load the appointment, the
   organization, and — **for `voice_reminder_due` / `voice_reminder_enrolled`** —
   the **live** `patient_consents` rows for `vr["patient_contact_id"]`.
2. Resolve `context.can_call` **fail-closed**:
   ```
   consent = voice_reminder.current_voice_consent(live_consent_rows)   # for due/enrolled
             # for post-call triggers, consent is not re-read — the call already happened
   gate    = voice_reminder.consent_gate(vr["patient_phone_snapshot"], consent)
   can_call = gate.can_call and vr.get("authorized_by") is not None
   ```
   If the live state differs from `vr["consent_snapshot"]`, update the snapshot +
   `consent_event_id_snapshot` on the row (C5) before deciding, and log a
   `voice_consent_changed_since_enrollment` activity row.
3. Assemble `vr_state` (§15.4). `decision = commander.decide(vr_state, trigger)` →
   `_decide_voice_reminder`.
4. `insert_activity(actor="00-commander", action=decision.reason_code,
   appointment_id=vr["appointment_id"], voice_reminder_id=vr_id, details={...})`.
5. **Hard-`raise`s** (mirrors of the eligibility / prior-auth / appeals guards):
   - `decision.next_status is not None` — **always forbidden** (§2.4).
   - `decision.action == "route"` **and** `decision.route_to == "17-voice-reminder"`
     **and** (`vr["consent_snapshot"] is not True` **or** `vr.get("authorized_by") is None`) — V2.
6. Dispatch:

| decision | dispatch |
|----------|----------|
| `route` → `17-voice-reminder` (VR4 / VR6, `reason_code == "voice_reminder_place_call"`) | `_run_place_call(...)`; on success emit `voice_reminder_call_placed` and re-enter; on `VoiceReminderUnavailable` write `call_failed` + emit `voice_reminder_call_failed` and re-enter. |
| `route` → `12-escalation` (VR2 / VR3 / VR7 / VR10 / VR11 / VR12 / VR13 / VR14) | `escalation.escalate(org_id, claim_pk=None, appointment_id=vr["appointment_id"], voice_reminder_id=vr_id, reason_code=decision.reason_code, context={...})`; store `escalation_id` on the row; return. |
| `no_action` (VR1 / VR5 / VR8 / VR9) | return. |

Before the `skipped_no_consent` / `skipped_no_phone` route, the orchestrator
writes that status onto the row so VR2 / VR3 / VR7 match on the next
`handle_voice_reminder` pass — the same "orchestrator sets the terminal status,
then the Commander records the disposition" shape as `_run_eligibility_agent`.

### 9.1 `_run_place_call`

```
1. vars = voice_reminder.resolve_variables(appointment, organization)   # raises on a bad tz -> caught -> error -> VR13
2. update_voice_reminder(org_id, vr_id, {"status": "calling", "placed_at": now,
       "variable_values": vars.as_vapi_variable_values(),
       "vapi_assistant_id": settings.vapi_assistant_id,
       "vapi_phone_number_id": settings.vapi_phone_number_id})
3. call = await voice_reminder.place_call(vars, vr["patient_phone_snapshot"],
       assistant_id=settings.vapi_assistant_id, phone_number_id=settings.vapi_phone_number_id)
4. update_voice_reminder(org_id, vr_id, {"vapi_call_id": call.vapi_call_id,
       "outcome_payload": {"vapi_call_status": call.vapi_call_status}})
5. insert_activity(actor="17-voice-reminder", action="call_placed",
       appointment_id=..., voice_reminder_id=vr_id, details={"vapi_call_id": call.vapi_call_id})
6. return {"type": "voice_reminder_call_placed"}
```

Step 4 stores `vapi_call_id` **before** the webhook can fire (Vapi call-create
returns synchronously; the phone rings after). A crash between 3 and 4 leaves an
orphan Vapi call with a `calling` row and no `vapi_call_id`; the reconcile pass
(§12.13) picks it up.

Any exception in 1–2 → caught, row → `error`, emit `voice_reminder_error` → VR13.

---

## 10. Backend + UI

### 10.1 Authed endpoints

**Consent (`backend/app/routers/patient_contacts.py`)**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/patient-contacts?patient_name=&patient_dob=` | the caller-clinic contacts for a patient (RLS-scoped) + each one's current voice-consent state and its history |
| `POST` | `/api/patient-contacts` | create a contact `{patient_name, patient_dob?, phone}`; org from the session; `409` on the unique key |
| `POST` | `/api/patient-contacts/{id}/consent` | **grant** voice consent — body `{source, note?}`. Appends a `patient_consents` row (`state='granted'`, `recorded_by = session user`). Logs `voice_consent_granted`. Idempotent-ish: a second grant is a new ledger row, harmless. |
| `POST` | `/api/patient-contacts/{id}/consent/revoke` | **revoke** — body `{source, note?}`. Appends `state='revoked'`. Logs `voice_consent_revoked`. Any `pending` `voice_reminders` for this contact are left as-is; VR7 catches them at due time (C5). |

**Reminders (`backend/app/routers/voice_reminders.py`)**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/appointments/{id}/voice-reminder` | the latest reminder for the appointment + its history + the reminder-scoped `activity_log` slice |
| `GET` | `/api/voice-reminders?status=` | the reminder work queue (RLS-scoped); `?status=` filter |
| `POST` | `/api/appointments/{id}/voice-reminder` | **enroll + authorize.** Body: `{patient_contact_id, scheduled_call_at?}`. Verifies the contact is visible to the caller **and** its `(patient_name, patient_dob)` matches the appointment's patient (soft key). Reads the contact's current voice consent — **`409` if not `granted`** ("capture consent first"). Creates the `pending` `voice_reminders` row with all snapshots + `consent_event_id_snapshot` + `authorized_by = ctx.user_id`, `authorized_at = now`. Emits `voice_reminder_due` if `now >= scheduled_call_at` else `voice_reminder_enrolled`. `409` if the appointment `scheduled_at` is in the past, or an active (`pending`/`calling`) reminder already exists. |
| `POST` | `/api/voice-reminders/{id}/cancel` | a human cancels a `pending` reminder. `status='cancelled'`. `409` unless `pending`. |
| `GET` | `/api/appointments/{id}` **(extended)** | gains `voice_reminder` (latest + history) and `patient_contacts` (with consent state), the way it gained `coverages` + `cob` in Phase 4. |

The reminder `POST` is the **HITL record** (V1/V3). It never trusts a body
`organization_id`; org comes from `require_organization`.

### 10.2 The webhook (`backend/app/routers/webhooks_vapi.py`) — §8. Unauthenticated, `X-Vapi-Secret` verified.

### 10.3 UI

- **On `AppointmentDetailPage`** — a **Voice Reminder** card next to the
  eligibility / prior-auth cards:
  - **consent panel** — the patient's number(s) with a consent badge
    (`Granted 12 Aug · intake form` / `Revoked 3 Sep · patient request` /
    `No consent on file`). A **Capture consent** control (phone + source
    dropdown + an "I have the patient's express consent to place automated
    reminder calls to this number" affirmation) and a **Revoke** control. The
    consent history is expandable (the append-only ledger).
  - **no reminder, appointment in the future, consent granted** — an **Enroll
    reminder** button (opens a small form: pick the consented number, an optional
    call time defaulting to `appointment − lead_hours`). Disabled with a "capture
    consent first" hint when there is no granted consent.
  - **`pending`** — "Reminder call scheduled for &lt;time&gt;", a **Cancel** button.
  - **`calling`** — "Calling the patient now…".
  - **`confirmed`** — a green "Patient confirmed this appointment" with the call
    time and duration.
  - **`skipped_no_consent` / `skipped_no_phone`** — "Couldn't place a reminder
    call: consent was revoked / no phone number." + a link to the escalation +
    next step.
  - **`reschedule_requested` / `wrong_person` / `out_of_scope` / `no_answer` /
    `call_failed`** — the outcome, plainly stated, + a link to the escalation.
    **No "retry" button** (decision §12.2) — the human decides the next contact.
- **A small work queue** — fold into the existing **Tasks** surface rather than a
  new nav item (**decision §12.14**): a "Reminders need follow-up" group
  (`reschedule_requested` / `wrong_person` / `out_of_scope` / `no_answer` /
  `call_failed` / `skipped_*`), a "Confirmed" group, a "Scheduled" group.
- Types → `frontend/src/lib/types.ts` (`PatientContact`, `VoiceConsentEvent`,
  `VoiceReminder`, `VoiceReminderStatus`, `VoiceReminderDetail`). Components →
  `frontend/src/components/voice-reminder.tsx`, `patient-consent.tsx`.

---

## 11. Test plan

Same standard as Phases 1–5. **No `ANTHROPIC_API_KEY` anywhere** (17 calls no
Claude model). **No real Vapi call anywhere** except the explicitly opt-in
`--live-vapi` test.

### `tests/voice_reminder_agent_test.py` (pure, no stack, no key, no network)

- **`current_voice_consent()`** — over ledgers built from `{[], [granted],
  [granted, revoked], [revoked, granted], [granted, revoked, granted],
  mixed channels}`: `granted` is `True` **iff** the latest `voice` row is
  `granted`; `event_id` points at that row; an SMS-only ledger → not granted;
  deterministic.
- **`consent_gate()` fail-closed grid** — `{consent: never / granted / revoked} ×
  {phone: valid E.164 / local-format / empty / None}`: `can_call` is `True`
  **iff** `granted` **and** the phone matches `E164_RE`; `reason` is `no_consent`
  vs `consent_revoked` vs `no_phone` vs `bad_phone_format` correctly; every
  `None` / missing combination → `False`. Named
  `test_revoked_consent_never_permits_a_call`,
  `test_missing_consent_never_permits_a_call`,
  `test_non_e164_phone_never_permits_a_call`.
- **`resolve_variables()`** — over appointments × org timezones: each of the four
  strings is a verbatim field or a deterministic strftime of `scheduled_at` in
  the org tz; a DST-boundary appointment renders the right local time; an invalid
  IANA `timezone` **raises**; deterministic.
- **`classify_outcome()`** — fixture Vapi `end-of-call-report` shapes: each of
  `confirmed / reschedule_needed / wrong_person / out_of_scope` maps to its
  status; no structured outcome + a voicemail `endedReason` → `no_answer`; an
  unrecognised `endedReason` with no outcome → `no_answer` (never `confirmed`); a
  "could not connect" shape → `call_failed`; a payload that includes a
  `transcript` / `recordingUrl` → the returned `payload` contains **neither**.
  Named `test_classify_never_confirms_without_the_literal_string`,
  `test_classify_never_surfaces_transcript_or_recording`.
- **`verify_webhook()`** — correct secret → `True`; wrong / missing header /
  empty configured secret → `False`; uses `hmac.compare_digest`.

### `tests/voice_reminder_commander_test.py` (pure, no stack, no key)

- one case per **VR1–VR14**;
- a re-run of representative **R / E / A / AP** cases proving the four existing
  blocks are unchanged; assert `EXECUTABLE_ACTIONS` / `MANUAL_ACTIONS` /
  `ELIGIBILITY_TRIGGERS` / `PRIOR_AUTH_TRIGGERS` / `APPEAL_TRIGGERS` untouched;
- **the consent-gate fuzz** — over
  `trigger × vr.status × context.can_call × authorized_by{set,None}`, assert on
  every result: determinism; **`decision.next_status is None`**;
  `route_to == "17-voice-reminder"` ⟹ `reason_code == "voice_reminder_place_call"`
  **and** `context.can_call is True` **and** `trigger.type in
  {"voice_reminder_due", "voice_reminder_enrolled"}`;
  `context.can_call is not True` ⟹ `route_to != "17-voice-reminder"`;
- named `test_a_reminder_never_calls_without_consent_and_authorization`,
  `test_voice_reminder_never_touches_claim_or_appointment_status`,
  `test_due_with_revoked_consent_routes_to_human_not_a_call`.

### `tests/voice_reminder_webhook_test.py` (local stack, no key)

- bad / missing `X-Vapi-Secret` → `401`, and **no** row is read or written;
- unknown `call.id` → `200`, ignored, one `vapi_webhook_unmatched_call` log line,
  no row touched;
- a genuine `end_reminder_call: confirmed` for a real `calling` row → row
  `confirmed`, `completed_at` set, **no** escalation;
- `reschedule_needed` / `wrong_person` / `out_of_scope` → row set accordingly +
  **exactly one** `escalations` row each (`voice_reminder_outcome_needs_human`),
  org-scoped, `voice_reminder_id` + `appointment_id` set;
- an `end-of-call-report` with no structured outcome + a voicemail `endedReason`
  → `no_answer` + one escalation;
- a payload that includes a `transcript` and a `recordingUrl` → the stored
  `voice_reminders` row has neither anywhere in `outcome_payload`;
- **W3**: feed an event whose body carries `orgId` / `clinicName` for Clinic B
  against a Clinic A `calling` row — the write lands on Clinic A, no Clinic B row
  is created or read;
- a replayed identical event → idempotent (same terminal row, no second
  escalation).

### `tests/voice_reminder_orchestrator_test.py` (local stack, no key; `place_call` monkeypatched)

- monkeypatch `place_call` → fake `VapiCall`: capture consent → enroll →
  `voice_reminder_due` → VR6 → `_run_place_call` → row `calling` with the fake
  `vapi_call_id`, `variable_values` populated with the four keys, one
  `call_placed` activity row;
- **the revocation-before-call path (C5)**: capture consent → enroll (`pending`,
  `consent_snapshot = true`) → **revoke consent** → `voice_reminder_due` → the
  orchestrator re-reads the live ledger → row → `skipped_no_consent` → VR7 → one
  escalation; **`place_call` call count is 0**; the snapshot on the row was
  updated and a `voice_consent_changed_since_enrollment` activity row exists;
- monkeypatch `place_call` to raise `VoiceReminderUnavailable` → row
  `call_failed` → VR12 → one escalation; `place_call` attempted exactly once (no
  retry);
- the V2 hard-`raise`: craft a state with `route_to == "17-voice-reminder"` but
  `consent_snapshot = false` → `handle_voice_reminder` raises `RuntimeError`;
- a bad `organizations.timezone` → `_run_place_call` catches, row → `error` → VR13;
- every `CommanderDecision` seen had `next_status is None`.

### `tests/e2e_voice_reminder_test.py` (local stack; `place_call` stubbed — real end to end otherwise)

| # | setup | asserted |
|---|-------|----------|
| 1 | contact + granted consent, future appt → enroll → due-scan → stubbed place → webhook `confirmed` | row `confirmed`; appointment + claims untouched; activity chain complete; all rows org-scoped |
| 2 | as 1 but webhook returns `reschedule_needed` | row `reschedule_requested`; one escalation, org-scoped |
| 3 | contact, **consent then revoked** before the due-scan | row `skipped_no_consent`; one escalation; the stub `place_call` recorded zero calls |
| 4 | contact, granted consent, but the stored number is `"415-555-0142"` | enroll `409` (bad phone) — or, if forced, row `skipped_no_phone` + one escalation; zero calls |
| 5 | stub `place_call` raises | row `call_failed`; one escalation `voice_reminder_call_failed`; no retry |
| 6 | second org re-runs scenario 1 | Clinic A sees no new rows; the webhook for B's call never reads an A row |
| 7 | collected across 1–6 | every `CommanderDecision` had `next_status is None`; every `voice_reminders` / `patient_consents` / `activity_log` / `escalations` row carried the right `organization_id`; no row anywhere holds a transcript or recording URL |

### `tests/voice_reminder_live_test.py` (opt-in `--live-vapi`; needs `VAPI_API_KEY` + `VAPI_LIVE_TEST_NUMBER`)

**Off by default. It rings a real phone and costs money.** Places one real call
via `place_call` to `VAPI_LIVE_TEST_NUMBER` with obviously-synthetic variables,
asserts a `vapi_call_id` comes back, `GET /call/{id}` is retrievable, and the
call's `artifactPlan.recordingEnabled` (or equivalent) reads `false`. Documented
in `PHASE-6.md` as a manual gate, like the `--live` appeal draft test.

### `tests/agent_isolation_test.py` (extended)

A reminder run over Clinic A's appointment never reads or writes a Clinic B
`patient_contacts` / `patient_consents` / `voice_reminders` / `activity_log` /
`escalations` row; the webhook path, driven only by `vapi_call_id`, stays
A-scoped even when the event body names B.

### `scripts/seed_voice_reminders.py`

Reuses the two seed clinics and their appointments. Sets
`organizations.timezone`. Creates `patient_contacts` + `patient_consents` across a
spectrum (most `granted`, one `revoked`, one with no consent row, one with a
malformed number), enrolls the consented ones with a `scheduled_call_at` in the
**past** so a single `run_voice_reminder_due_scan.py` pass fires them, and
**stubs `place_call`** (deterministic fake `vapi_call_id`) unless `--live-vapi`.
Feeds synthetic `end-of-call-report` payloads through the webhook for a realistic
mix (mostly `confirmed`, a couple `reschedule_needed`, one `no_answer`). Includes
a **revoke-after-enroll** demo that ends `skipped_no_consent`. Prints the
distribution like `seed_prior_auth.py`.

### `scripts/run_voice_reminder_proof.sh`

Pure agent + Commander tests (no stack) → the stack tests against a fresh
`supabase db reset` → the seed. `--live-vapi` additionally runs
`voice_reminder_live_test.py`.

---

## 12. Decisions

### Resolved at review (2026-09-09)

1. **VR block: a disjoint family vs. extending R1–R20.** → **disjoint fifth
   family**, dispatched before R1 like E / A / AP. The four existing Commander
   suites stay byte-for-byte.
2. **The human-in-the-loop model for the call itself.** → **RESOLVED: the
   per-appointment enrollment IS the recorded approval.** `POST
   /api/appointments/{id}/voice-reminder` records `authorized_by`; the call fires
   automatically at `scheduled_call_at`. No per-call approval queue. **Corollary,
   also resolved:** a missed / failed / non-confirmed reminder is **always** a
   human decision — **no automatic retry** anywhere in Phase 6 (an auto-retry
   without human review is a TCPA-adjacent risk). `NO_AUTORETRY = True` is a named
   constant; `place_call` is attempted exactly once per reminder row.
3. **Consent storage.** → **RESOLVED: a dedicated `patient_contacts` +
   `patient_consents` ledger** (§4.1), append-only, keyed by patient soft-key +
   phone, with grant/revoke events, `recorded_by`, and source. **Not** snapshot
   fields on `appointments`. Consent belongs to the patient + number, is durable
   across appointments, is auditable (the full ledger), and is revocable (a new
   `revoked` row). The `voice_reminders` row still snapshots the *resolved* state
   (`consent_snapshot`, `consent_event_id_snapshot`) for the fail-closed call
   decision and audit, and the orchestrator re-reads the **live** ledger at due
   time (C5).
4. **Call recording.** → **RESOLVED: disabled entirely.** Recording is turned off
   on the Vapi assistant, and every `place_call` sends `recordingEnabled: false`
   as defence in depth. We keep only the structured `end_reminder_call` outcome
   and call metadata. No recording URL and no transcript text is ever persisted
   (§4.3, §6.5). BEFORE-PHI gets a recording-disabled attestation row.
5. **`organizations.timezone`.** → **RESOLVED: add the column now**, default
   `America/New_York` as an explicit placeholder. A wrong-hour call to a real
   patient is too costly to defer. `resolve_variables` raises on an invalid IANA
   zone (→ `error` → VR13, never a mis-timed call).

### Still open — flag if you disagree; recommendations stand

6. **Webhook authentication.** → recommend **shared secret `X-Vapi-Secret`**
   (constant-time compare) as the baseline — matches Vapi's Server-URL-secret
   model, one env var. Add **HMAC signature verification** over the raw body
   if/when Vapi signs; note **source-IP allowlisting** as defence-in-depth.
   Confirm the exact header name against Vapi's current docs at build.
7. **The due-scan mechanism.** → recommend a **cron-invoked script**
   (`run_voice_reminder_due_scan.py`), same operational shape as the card-image
   purge. Alternatives: `pg_cron` + NOTIFY; an in-process scheduler (rejected —
   dies with the process, breaks under multiple workers).
8. **Vapi payload shapes.** The exact JSON paths for the structured outcome, the
   ended reason, the call id, and the recording-disable override are **pinned
   against a real captured payload** before `classify_outcome` / `place_call` are
   finalised. Both functions fail safe on anything unrecognised (`no_answer`
   never `confirmed`; recording override always sent `false`).
9. **`activity_log` / `escalations` new columns.** → recommend **add
   `voice_reminder_id` to both and `patient_contact_id` to `activity_log`** —
   appointment-scoped family, plus a first-class consent audit trail. Follows the
   eligibility / prior-auth FK precedent.
10. **`{{patient_name}}` — full name vs. first name.** → recommend **first name
    only** ("Hi, is this Maria?"), `patient_name.split()[0]`. Confirm.
11. **Reconcile pass for lost webhooks.** → recommend the due-scan also sweeps
    `calling` rows older than ~1h with no terminal outcome and pulls
    `GET /call/{id}` from Vapi to backfill. Confirm scope.
12. **UI placement.** → recommend **folding the queue into Tasks** rather than a
    new nav item. Confirm.
13. **Agent number 17.** 11 went to appeals (Phase 5); 13–16 are left unassigned
    (13 was pencilled as a claims-pipeline `auth-submitter` alternative; 14–16
    held for other work). 17 is the voice agent. Confirm, or renumber.
14. **Contact ↔ appointment patient matching.** The enroll endpoint matches the
    `patient_contact` to the appointment by `(patient_name, patient_dob)` soft
    key. If `patient_dob` is null on the appointment, matching is by name only —
    confirm that is acceptable, or require a dob on the contact.
15. **Real external action — explicit review gate.** This is the first
    non-simulation. Confirm the review is comfortable that: (a) the call is
    disclosed + clinic-identified in Vapi; (b) `authorized_by` + a live
    fail-closed consent ledger + recording-off are the guardrails; (c) no
    transcript/recording is stored; (d) the unauthenticated webhook's W1–W4
    controls are sufficient — before code starts.

`00-commander.md` §2 / §5 / §15 and `architecture.md` §6 / §7 to be updated to
match once §6–§15 are resolved.
