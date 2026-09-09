# 17 — Voice Reminder Agent

_Spec. Written before implementation, per the Phase 1–5 discipline. Cross-check
this document — and the §15 addendum to [`00-commander.md`](00-commander.md) —
before any Phase 6 code is written._

_Status: **SPEC — awaiting review.** Companion doc: [`../PHASE-6.md`](../PHASE-6.md)
(to be written at build time). Proposed code:
`supabase/migrations/20260909000001_voice_reminders.sql`,
`backend/app/agents/voice_reminder.py`, `commander._decide_voice_reminder`
(VR1–VR14), `orchestrator.handle_voice_reminder`,
`backend/app/routers/voice_reminders.py` + `backend/app/routers/webhooks_vapi.py`,
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
behind **two preconditions** (TCPA consent on file; a recorded human
authorization), performs the call, and processes a **structured outcome**
returned by Vapi's `end_reminder_call` tool. That shape makes it a **Commander
agent** — the **fifth disjoint family** (VR1–VR14), dispatched by an early branch
in `decide()` before R1, exactly like eligibility (E1–E7), prior-auth (A1–A11),
and appeals (AP1–AP12).

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 17 | **voice-reminder-agent** | trigger + real external executor | **no** (the LLM is Vapi's, hosted there — not called by us) | enrolled appointment → (consent + human auth) → real outbound call via the existing Vapi assistant → structured outcome → `confirmed` records and stops; anything else → 12-escalation |

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
   R7/R8 → R9, A5 → A7, and AP4 → AP6.

2. **TCPA consent is a legal precondition, not a nicety.** An automated /
   prerecorded call to a patient's phone requires **prior express consent** for
   that number. 17 treats a missing or ambiguous consent record as a hard stop
   (**fail-closed** — §2.2), the opposite of the emergency fail-safe in Phases
   2–3 (which fails _open_, toward not blocking care).

3. **A live call's transcript and recording are PHI.** Phase 6 persists **only**
   the structured outcome and call metadata (duration, ended reason, Vapi call
   id) — **never** the transcript text or the recording URL. New rows land in
   [`../BEFORE-PHI.md`](../BEFORE-PHI.md): TCPA consent capture + retention, the
   call-recording decision (disable on the assistant, or purge like card images),
   the Vapi BAA, and `VAPI_WEBHOOK_SECRET` in secrets management.

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

### 2.2 No call is placed without TCPA consent — and consent **fails closed**

> `context.can_call` is `True` **only** when the appointment (snapshotted onto the
> `voice_reminders` row) carries `reminder_consent = true` **and** a
> syntactically valid E.164 `patient_phone`. Missing consent, missing phone,
> `NULL`, or any ambiguity → `can_call = False` → **no call**, and the reminder
> is recorded `skipped_no_consent` / `skipped_no_phone` and routed to a human
> (VR2 / VR3 / VR7).

| # | Mechanism | Where |
|---|-----------|-------|
| C1 | **`voice_reminder.consent_gate()` is a pure function that runs BEFORE anything else.** It returns `can_call: bool` + a reason. Its default for a missing/None input is `False`. | §7.2 |
| C2 | **The orchestrator resolves `context.can_call` fail-closed** — mirror of `_resolve_emergency` but inverted: any ambiguity → `False`. §9. | §9 |
| C3 | **Consent is snapshotted onto the `voice_reminders` row at enrollment** (`consent_snapshot`, `consent_source_snapshot`, `patient_phone_snapshot`). The call decision reads the snapshot, not a value that could change under it — same pattern as `eligibility_checks.is_emergency` / `prior_authorizations.place_of_service`. | §4.3 |
| C4 | **V2's hard-`raise`** also covers consent: `17.place_call` cannot run with `consent_snapshot is not True`. | §9 |

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
| W2 | **`vapi_call_id` is the only join key.** The handler does `db.voice_reminder_by_call_id(call_id)`; `None` → log `vapi_webhook_unmatched_call` to stderr and return `200`. | §8.2 |
| W3 | **`organization_id` comes from that row.** Every subsequent `db.*` call is filtered by it, exactly as in Phases 1–5. `tests/agent_isolation_test.py` gains a webhook slice (§11). | §8.2, §9 |
| W4 | **`vapi_call_id` is `unique`** — a replayed event updates the same row idempotently; it can never fan out. | §4.3 |

### 2.4 A reminder never changes a claim or an appointment

> `decision.next_status is None` for **every** VR rule (VR1–VR14). 17 writes only
> `voice_reminders`, `activity_log`, and (via 12) `escalations`. Nothing on
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
| Credentials | `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_ASSISTANT_ID` in `backend/.env` | Already set. Phase 6 adds **`VAPI_WEBHOOK_SECRET`** (§5). |

### 3.2 What Phase 6 builds — our side, none of it exists yet

| # | Piece | File |
|---|-------|------|
| 1 | **Data model** — `voice_reminders` + `voice_reminder_status` enum; `appointments` gains `patient_phone` + consent fields; `organizations` gains `timezone`; `activity_log` / `escalations` gain `voice_reminder_id`. | `supabase/migrations/20260909000001_voice_reminders.sql` |
| 2 | **The agent** — pure `resolve_variables()` / `consent_gate()` / `classify_outcome()` / `verify_webhook()` + the one real-I/O `place_call()`. | `backend/app/agents/voice_reminder.py` |
| 3 | **Commander** — a fifth disjoint rule block (VR1–VR14), dispatched before R1. | `backend/app/agents/commander.py`, spec §15 |
| 4 | **Orchestrator** — `handle_voice_reminder`, the V2 / consent / next_status hard-`raise`s, the webhook-driven re-entry. | `backend/app/agents/orchestrator.py` |
| 5 | **Enrollment / management endpoints** (authed, RLS-scoped). | `backend/app/routers/voice_reminders.py` |
| 6 | **The Vapi webhook receiver** (unauthenticated, shared-secret verified). | `backend/app/routers/webhooks_vapi.py` |
| 7 | **A due-scan** — emits `voice_reminder_due` for `pending` rows whose `scheduled_call_at <= now`. | `scripts/run_voice_reminder_due_scan.py` (cron, like the card-image purge) |
| 8 | **UI** — a Voice Reminder card on the appointment detail page + a small work queue. | `frontend/src/components/voice-reminder.tsx`, `AppointmentDetailPage.tsx` |
| 9 | **Seed + tests** — §11. | `scripts/seed_voice_reminders.py`, `tests/voice_reminder_*` |

---

## 4. Data model

New migration `supabase/migrations/20260909000001_voice_reminders.sql`. Same rules
as every prior phase: every new table carries
`organization_id uuid not null references public.organizations(id)` and runs
through `select public.enable_tenant_isolation('public.<table>')`. No hardcoded
tenant id. Agent writes use the service-role key and filter every query by the
`organization_id` resolved from the `voice_reminders` row.

### 4.1 `appointments` — patient contact + TCPA consent

```sql
alter table public.appointments
  add column patient_phone                  text,     -- E.164, e.g. +14155550142; NULL allowed
  add column reminder_consent               boolean not null default false,
  add column reminder_consent_recorded_at   timestamptz,
  add column reminder_consent_source        text;     -- 'intake_form' | 'patient_portal' | 'verbal_documented' | ...

comment on column public.appointments.reminder_consent is
  'TCPA prior express consent to place an automated appointment-reminder call to patient_phone. FALSE / NULL => 17-voice-reminder-agent will not call (fail-closed, 17-voice-reminder-agent.md §2.2). Set only through a backend endpoint that re-derives the caller org.';
```

Consent is really a property of a `(patient, phone)` pair, but this codebase has
no `patients` table and keys patients softly by name (+ dob). Snapshotting consent
onto the appointment — and again onto the `voice_reminders` row — is exactly how
`eligibility_checks` / `prior_authorizations` snapshot `is_emergency` /
`place_of_service`. A dedicated `patient_contacts` consent table is **open
decision §12.3**.

### 4.2 `organizations` — a timezone for rendering the call variables

```sql
alter table public.organizations
  add column timezone text not null default 'America/New_York';   -- IANA tz for {{appointment_date}} / {{appointment_time}}
```

`{{appointment_date}}` / `{{appointment_time}}` must be spoken in the patient's
local (clinic) time. `appointments.scheduled_at` is `timestamptz` (UTC); without
a clinic tz we would read the appointment time wrong. Default is a placeholder —
**open decision §12.8**.

### 4.3 Enum + `voice_reminders`

```sql
create type public.voice_reminder_status as enum (
  'pending',              -- enrolled + authorized by a human; not yet placed (waiting for scheduled_call_at)
  'skipped_no_consent',   -- precondition failed: no TCPA consent on file — no call placed, routed to a human (VR2/VR7)
  'skipped_no_phone',     -- precondition failed: no usable E.164 number — no call placed, routed to a human (VR3)
  'cancelled',            -- a human cancelled the reminder before it was placed
  'calling',              -- Vapi accepted the call-create; the call is live or ringing
  'confirmed',            -- end_reminder_call outcome = confirmed — the ONLY clean terminal
  'reschedule_requested', -- end_reminder_call outcome = reschedule_needed — routed to a human (VR10)
  'wrong_person',         -- end_reminder_call outcome = wrong_person — routed to a human; consent/number flagged (VR10)
  'out_of_scope',         -- end_reminder_call outcome = out_of_scope — routed to a human (VR10)
  'no_answer',            -- call ended with no structured outcome (voicemail / no pickup / early hangup) — routed to a human (VR11)
  'call_failed',          -- Vapi could not place or complete the call (VR12)
  'error'                 -- our side failed (bad data, place_call raised, classify raised) (VR13)
);

create table public.voice_reminders (
  id                      uuid primary key default gen_random_uuid(),
  organization_id         uuid not null references public.organizations (id) on delete cascade,
  appointment_id          uuid not null references public.appointments (id) on delete cascade,

  -- snapshots taken at enrollment — the call decision + the spoken variables read THESE, never the live rows
  patient_name_snapshot   text not null,
  patient_phone_snapshot  text,                              -- E.164, or NULL
  clinic_name_snapshot    text not null,                     -- organizations.name at enrollment == the {{clinic_name}} value
  appointment_at_snapshot timestamptz not null,              -- the scheduled_at we rendered into the variables
  timezone_snapshot       text not null,                     -- organizations.timezone at enrollment
  consent_snapshot        boolean not null default false,
  consent_source_snapshot text,

  status                  public.voice_reminder_status not null default 'pending',
  scheduled_call_at       timestamptz not null,              -- when the call should be placed (default: appointment - lead_hours)

  -- the recorded human authorization (V1/V3) — this reminder does not call without it
  authorized_by           uuid references auth.users (id) on delete set null,
  authorized_at           timestamptz,

  -- Vapi linkage
  vapi_assistant_id       text,                              -- snapshot of VAPI_ASSISTANT_ID used (audit if it is ever swapped)
  vapi_phone_number_id    text,
  vapi_call_id            text unique,                        -- Vapi's call id — the ONLY join key the webhook trusts (W2/W4)
  variable_values         jsonb not null default '{}'::jsonb, -- exactly the 4 vars we sent, verbatim, for audit

  -- outcome — structured only. NO transcript text, NO recording URL (§1.1 point 3)
  outcome                 text,                              -- raw end_reminder_call string, or NULL
  outcome_payload         jsonb not null default '{}'::jsonb, -- {outcome, ended_reason, duration_seconds, vapi_call_status, cost, ...}

  placed_at               timestamptz,
  completed_at            timestamptz,
  escalation_id           uuid references public.escalations (id) on delete set null,
  created_at              timestamptz not null default now()
);

create index voice_reminders_organization_id_idx   on public.voice_reminders (organization_id);
create index voice_reminders_appointment_id_idx    on public.voice_reminders (appointment_id, created_at);
create index voice_reminders_status_idx            on public.voice_reminders (organization_id, status);
create index voice_reminders_due_idx               on public.voice_reminders (status, scheduled_call_at);   -- the due-scan
create unique index voice_reminders_vapi_call_id_idx on public.voice_reminders (vapi_call_id) where vapi_call_id is not null;

comment on table public.voice_reminders is
  'One automated outbound appointment-reminder call attempt (Phase 6). Sidecar: appointments has no FK here. A call is NEVER placed without authorized_by set (a human) AND consent_snapshot = true AND a valid E.164 phone (17-voice-reminder-agent.md §2). Stores the structured outcome only — never transcript or recording (PHI). Tenant-scoped.';
```

### 4.4 Audit trail — `activity_log` / `escalations` gain `voice_reminder_id`

```sql
alter table public.activity_log add column voice_reminder_id
  uuid references public.voice_reminders (id) on delete cascade;
alter table public.escalations  add column voice_reminder_id
  uuid references public.voice_reminders (id) on delete cascade;

create index activity_log_voice_reminder_id_idx on public.activity_log (voice_reminder_id, created_at);
create index escalations_voice_reminder_id_idx  on public.escalations  (voice_reminder_id);
```

Voice reminders are **appointment-scoped** (like eligibility / prior-auth), and
one appointment can have more than one reminder row over time, so `appointment_id`
alone is ambiguous — a dedicated FK column follows the eligibility / prior-auth
precedent rather than the appeals `context.*_id`-only approach. **Open decision
§12.9.** `insert_activity` / `insert_escalation` gain an optional
`voice_reminder_id` kwarg.

### 4.5 Isolation + grants

```sql
select public.enable_tenant_isolation('public.voice_reminders');

revoke all on public.voice_reminders from anon, authenticated;
grant  select on public.voice_reminders to authenticated;   -- UI reads, RLS-scoped; every write is backend-mediated
```

No client write grant (Phases 1–5 discipline). The webhook writes with the
service-role key after the W1/W2/W3 checks.

---

## 5. Settings

```python
# app/config.py — all from the environment, never hardcoded
vapi_api_key: str = ""            # VAPI_API_KEY            (already set)
vapi_assistant_id: str = ""       # VAPI_ASSISTANT_ID       (already set — ec0bcddf-...)
vapi_phone_number_id: str = ""    # VAPI_PHONE_NUMBER_ID    (already set)
vapi_webhook_secret: str = ""     # VAPI_WEBHOOK_SECRET     (NEW — shared secret configured on the Vapi server URL)
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
class ConsentDecision:
    can_call: bool
    reason: str                # "" when can_call; else "no_consent" | "no_phone" | "bad_phone_format"

@dataclass(frozen=True)
class VapiCall:
    vapi_call_id: str
    vapi_call_status: str       # Vapi's returned status, e.g. "queued"

@dataclass(frozen=True)
class ReminderOutcome:
    status: str                 # a voice_reminder_status value: confirmed | reschedule_requested | wrong_person | out_of_scope | no_answer | call_failed
    outcome: str | None         # the raw end_reminder_call string, or None
    payload: dict               # {outcome, ended_reason, duration_seconds, vapi_call_status, cost}

def resolve_variables(appointment: dict, organization: dict, *, now=None) -> ReminderVariables: ...   # PURE
def consent_gate(appointment_or_snapshot: dict) -> ConsentDecision: ...                               # PURE
def classify_outcome(vapi_event: dict) -> ReminderOutcome: ...                                        # PURE
def verify_webhook(headers: dict, *, secret: str) -> bool: ...                                        # PURE (constant-time)

async def place_call(variables: ReminderVariables, phone_e164: str, *,
                     assistant_id: str, phone_number_id: str) -> VapiCall:
    """The ONE real-I/O function. POST {vapi_api_base}/call. Raises
    VoiceReminderUnavailable on missing key / network error / non-2xx."""
```

### 6.2 `resolve_variables()` — pure (fills exactly the four Vapi variables)

```
clinic_name      = organization["name"].strip()
patient_name     = appointment["patient_name"].strip()          # first name only is an option — §12.11
local            = appointment["scheduled_at"] rendered in organization["timezone"] (IANA, zoneinfo)
appointment_date = local.strftime("%A, %B %-d")                 # "Tuesday, September 15"
appointment_time = local.strftime("%-I:%M %p")                  # "2:30 PM"
```

Deterministic given `(appointment, organization)`. No free-form text ever reaches
the variable map — the assistant's prompt is fixed in Vapi and only interpolates
these four strings. The §11 test asserts every value is a substring-or-format of
a real appointment / organization field.

### 6.3 `consent_gate()` — pure, fail-closed (C1)

```
phone   = (row.get("patient_phone") or row.get("patient_phone_snapshot") or "").strip()
consent = row.get("reminder_consent") or row.get("consent_snapshot")

not consent            -> ConsentDecision(False, "no_consent")
not phone              -> ConsentDecision(False, "no_phone")
not E164_RE.match(phone) -> ConsentDecision(False, "bad_phone_format")
otherwise              -> ConsentDecision(True, "")
```

A `None`/absent input for either field yields `False`. There is no branch that
returns `can_call=True` from missing data.

### 6.4 `place_call()` — the one real external call

```
POST {vapi_api_base}/call
Authorization: Bearer {vapi_api_key}
{
  "assistantId": "{vapi_assistant_id}",                 # ec0bcddf-... — the existing assistant, unchanged
  "phoneNumberId": "{vapi_phone_number_id}",
  "customer": { "number": "{phone_e164}" },
  "assistantOverrides": { "variableValues": { ...the 4 keys from ReminderVariables... } }
}
```

- `httpx.AsyncClient`, `timeout = VAPI_CALL_TIMEOUT_SECONDS`, mirroring `db.py`'s
  client style.
- Missing `vapi_api_key` → `VoiceReminderUnavailable` (do not attempt the call).
- Non-2xx or network error → `VoiceReminderUnavailable` (no retry in Phase 6 —
  **open decision §12.12**; a transient failure becomes `call_failed` → VR12 →
  human, who can re-enroll).
- 2xx → `VapiCall(vapi_call_id=body["id"], vapi_call_status=body.get("status",""))`.
- **No fallback, no simulated send.** Unlike 09/10, there is no "always succeeds"
  stand-in — the whole point of the phase is the real call. Tests monkeypatch
  `place_call` (§11).

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

raw in OUTCOME_TO_STATUS        -> ReminderOutcome(OUTCOME_TO_STATUS[raw], raw, {...})
raw is None and ended in TERMINAL_ENDED_REASONS_NO_OUTCOME  -> ("no_answer", None, {...})
raw is None, any other ended reason                          -> ("no_answer", None, {...})   # fail toward human
message indicates the call could not be placed/connected     -> ("call_failed", None, {...})
```

`classify_outcome` **never** returns `confirmed` unless `raw == "confirmed"`
literally. Ambiguity → `no_answer` → VR11 → 12.

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
| `pending` | the enroll endpoint (with `authorized_by`), or a resubmit-style re-enroll | due-scan emits `voice_reminder_due` → VR6 → route 17 (place) |
| `skipped_no_consent` | orchestrator, when `consent_gate` fails on `no_consent` | `voice_reminder_enrolled`/`_due` → VR2 / VR7 → route 12 |
| `skipped_no_phone` | orchestrator, on `no_phone` / `bad_phone_format` | VR3 → route 12 |
| `cancelled` | the cancel endpoint | terminal — no trigger |
| `calling` | orchestrator, after `place_call` returns a `vapi_call_id` | `voice_reminder_call_placed` → VR8 → `no_action`; then the webhook |
| `confirmed` | orchestrator, from `classify_outcome` | `voice_reminder_outcome_received` → VR9 → `no_action` (terminal, clean) |
| `reschedule_requested` / `wrong_person` / `out_of_scope` | orchestrator, from `classify_outcome` | `voice_reminder_outcome_received` → VR10 → route 12 |
| `no_answer` | orchestrator, from `classify_outcome` | VR11 → route 12 |
| `call_failed` | orchestrator, on `VoiceReminderUnavailable` or a Vapi failure event | `voice_reminder_call_failed` → VR12 → route 12 |
| `error` | orchestrator, on any other exception in the run | `voice_reminder_error` → VR13 → route 12 |

---

## 7. Where 17 sits

```
a human enrolls an appointment for a reminder
   POST /api/appointments/{id}/voice-reminder   (records authorized_by)
        │  creates voice_reminders row (pending), snapshots name/phone/clinic/time/consent
        │
        ├─ within the lead window now  → trigger: voice_reminder_due
        └─ earlier                     → trigger: voice_reminder_enrolled → VR5 no_action (row waits)
        │
  ── cron: run_voice_reminder_due_scan.py  → voice_reminder_due for each pending row past scheduled_call_at
        │
   00 ── VR6 ──► consent_gate()  [pure, fail-closed]
        │
        ├─ no consent  → status skipped_no_consent → 00 ── VR7 ──► 12  (staff: capture consent / call manually)
        ├─ no phone    → status skipped_no_phone   → 00 ── VR3 ──► 12
        │
        └─ can_call    → 17.place_call()  [REAL POST api.vapi.ai/call, existing assistant + our 4 variables]
                         status = calling, vapi_call_id stored
                         trigger: voice_reminder_call_placed → 00 ── VR8 ──► no_action
        │
   ── Vapi runs the call (its assistant, its LLM, its disclosure + identity check) ──
        │
   POST /webhooks/vapi   (X-Vapi-Secret verified; row found by vapi_call_id; org from the row)
        │  classify_outcome()  [pure]
        │  status ∈ {confirmed | reschedule_requested | wrong_person | out_of_scope | no_answer | call_failed}
        │  trigger: voice_reminder_outcome_received
        │
        ├─ confirmed          → 00 ── VR9  ──► no_action   (done — a green "Patient confirmed" on the appointment)
        └─ anything else      → 00 ── VR10/VR11 ──► 12  (one escalation, appointment_id + voice_reminder_id)
```

No arrow writes `appointments` or `claims`.

---

## 8. The webhook (`POST /webhooks/vapi`)

### 8.1 Request handling

1. Read raw body + headers. `verify_webhook(headers, secret=settings.vapi_webhook_secret)` → `False` ⇒ `401 {"detail":"bad signature"}`, nothing else runs (W1).
2. Parse JSON. Vapi wraps the payload as `{"message": {...}}`. Branch on `message.type`:
   - `end-of-call-report` — the terminal event. `classify_outcome(message)` → update the row + drive the Commander (§8.2).
   - `tool-calls` (a live `end_reminder_call` invocation) — record `outcome` on the row if not already terminal, and **respond** with the tool result body Vapi expects: `{"results": [{"toolCallId": <id>, "result": "acknowledged"}]}`. Do **not** drive the Commander from this message — wait for `end-of-call-report` so duration / ended-reason are known. (If `end-of-call-report` is not delivered for some reason, a reconcile pass in the due-scan can pull `GET /call/{id}` — §12.13.)
   - `status-update`, `hang`, anything else — `200 {"ok": true}`, no-op (optionally note `status-update` transitions in `activity_log`).
3. Any handler exception after verification → log, `200` (so Vapi does not hammer retries), and leave the row for the reconcile pass. A `500` is only returned if we cannot even parse.

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
the only such router. It is mounted at `/webhooks/vapi`. CORS is irrelevant
(server-to-server). Document loudly in the router docstring that this is the
first unauthenticated write path and why it is safe (W1–W4).

---

## 9. The orchestrator (`handle_voice_reminder`)

`backend/app/agents/orchestrator.py` gains `handle_voice_reminder(vr_id, trigger)`,
a mirror of `handle_prior_auth`, reusing `MAX_INVOCATIONS` and the existing
machinery.

1. Load the `voice_reminders` row → resolve `organization_id` **once** from it
   (`db.get_voice_reminder`, the `get_claim` analogue). Load the appointment and
   the organization.
2. Resolve `context.can_call` **fail-closed**:
   ```
   snap = {**vr, "patient_phone": vr["patient_phone_snapshot"],
                 "reminder_consent": vr["consent_snapshot"]}
   can_call = voice_reminder.consent_gate(snap).can_call and vr.get("authorized_by") is not None
   ```
   Any missing field → `consent_gate` already returns `False`. `authorized_by`
   missing → `False`.
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
| `route` → `17-voice-reminder` (VR4 / VR6, `reason_code == "voice_reminder_place_call"`) | `_run_place_call(org_id, vr, appointment, organization)`; on success emit `voice_reminder_call_placed` and re-enter; on `VoiceReminderUnavailable` write `call_failed` + emit `voice_reminder_call_failed` and re-enter. |
| `route` → `12-escalation` (VR2 / VR3 / VR7 / VR10 / VR11 / VR12 / VR13 / VR14) | `escalation.escalate(org_id, claim_pk=None, appointment_id=vr["appointment_id"], voice_reminder_id=vr_id, reason_code=decision.reason_code, context={...})`; store `escalation_id` on the row; return. |
| `no_action` (VR1 / VR5 / VR8 / VR9) | return. |

### 9.1 `_run_place_call`

```
1. vars = voice_reminder.resolve_variables(appointment, organization)
2. update_voice_reminder(org_id, vr_id, {"status": "calling", "placed_at": now,
       "variable_values": vars.as_vapi_variable_values(),
       "vapi_assistant_id": settings.vapi_assistant_id,
       "vapi_phone_number_id": settings.vapi_phone_number_id})
3. call = await voice_reminder.place_call(vars, vr["patient_phone_snapshot"],
       assistant_id=settings.vapi_assistant_id,
       phone_number_id=settings.vapi_phone_number_id)
4. update_voice_reminder(org_id, vr_id, {"vapi_call_id": call.vapi_call_id,
       "outcome_payload": {"vapi_call_status": call.vapi_call_status}})
5. insert_activity(actor="17-voice-reminder", action="call_placed",
       appointment_id=..., voice_reminder_id=vr_id,
       details={"vapi_call_id": call.vapi_call_id})   # NO phone number, NO variables-with-name in the log details beyond what's needed
6. return {"type": "voice_reminder_call_placed"}
```

Step 4 is what wires §2.3 — the `vapi_call_id` is stored **before** the webhook
can fire (Vapi call-create returns synchronously; the call rings after). A
crash between 3 and 4 leaves an orphan Vapi call with a `calling` row and no
`vapi_call_id`; the reconcile pass (§12.13) picks it up.

Any exception in 1–2 (bad appointment data) → caught, row → `error`, emit
`voice_reminder_error` → VR13.

---

## 10. Backend + UI

### 10.1 Authed endpoints (`backend/app/routers/voice_reminders.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/appointments/{id}/voice-reminder` | the latest reminder for the appointment + its history + the reminder-scoped `activity_log` slice |
| `GET` | `/api/voice-reminders?status=` | the reminder work queue (RLS-scoped); `?status=` filter |
| `POST` | `/api/appointments/{id}/voice-reminder` | **enroll + authorize.** Body: `{scheduled_call_at?, patient_phone?, consent_affirmed?, consent_source?}`. If `patient_phone` / consent are supplied they are written to the appointment first (backend-mediated). Creates the `pending` `voice_reminders` row with **all snapshots** + `authorized_by = ctx.user_id`, `authorized_at = now`. Emits `voice_reminder_due` if `now >= scheduled_call_at` else `voice_reminder_enrolled`. `409` if the appointment `scheduled_at` is in the past, or an active (`pending`/`calling`) reminder already exists. |
| `POST` | `/api/voice-reminders/{id}/cancel` | a human cancels a `pending` reminder. `status = 'cancelled'`. `409` unless `pending`. |
| `GET` | `/api/appointments/{id}` **(extended)** | gains `voice_reminder` (latest + history), the way it gained `coverages` + `cob` in Phase 4. |

The `POST` handler is the **HITL record** (V1/V3). It never trusts a body
`organization_id`; org comes from `require_organization`.

### 10.2 The webhook (`backend/app/routers/webhooks_vapi.py`) — §8. Unauthenticated, `X-Vapi-Secret` verified.

### 10.3 UI

- **On `AppointmentDetailPage`** — a **Voice Reminder** card next to the
  eligibility / prior-auth cards:
  - **no reminder, appointment in the future** — consent + phone status; an
    **Enroll reminder** button (opens a small form: confirm/enter the phone, an
    "I have consent to call this number" checkbox with a source dropdown, an
    optional call time defaulting to `appointment − lead_hours`). The checkbox is
    the visible consent affirmation; unchecked → the button is disabled.
  - **`pending`** — "Reminder call scheduled for &lt;time&gt;", a **Cancel** button.
  - **`calling`** — "Calling the patient now…".
  - **`confirmed`** — a green "Patient confirmed this appointment" with the call
    time and duration.
  - **`skipped_no_consent` / `skipped_no_phone`** — "Couldn't place a reminder
    call: no consent on file / no phone number." + a link to the escalation +
    "capture consent and re-enroll, or call the patient directly."
  - **`reschedule_requested` / `wrong_person` / `out_of_scope` / `no_answer` /
    `call_failed`** — the outcome, plainly stated, + a link to the escalation.
- **A small work queue** — fold into the existing **Tasks** surface rather than a
  new nav item (**open decision §12.14**): a "Reminders need follow-up" group
  (`reschedule_requested` / `wrong_person` / `out_of_scope` / `no_answer` /
  `call_failed` / `skipped_*`), a "Confirmed" group, a "Scheduled" group.
- Types → `frontend/src/lib/types.ts` (`VoiceReminder`, `VoiceReminderStatus`,
  `VoiceReminderDetail`). Badge + card → `frontend/src/components/voice-reminder.tsx`.

---

## 11. Test plan

Same standard as Phases 1–5. **No `ANTHROPIC_API_KEY` anywhere** (17 calls no
Claude model). **No real Vapi call anywhere** except the explicitly opt-in
`--live-vapi` test.

### `tests/voice_reminder_agent_test.py` (pure, no stack, no key, no network)

- **`resolve_variables()`** — over a grid of appointments × org timezones: every
  one of the four returned strings is a verbatim field (`clinic_name`,
  `patient_name`) or a deterministic strftime of `scheduled_at` in the org tz;
  DST boundaries render the right local time; deterministic.
- **`consent_gate()` fail-closed grid** — `{consent: true/false/None} ×
  {phone: valid E.164 / local-format / empty / None}`: `can_call` is `True`
  **iff** `consent is true` **and** the phone matches `E164_RE`. Every `None` /
  missing combination → `False`. Named
  `test_missing_consent_never_permits_a_call`,
  `test_non_e164_phone_never_permits_a_call`.
- **`classify_outcome()`** — a fixture set of realistic Vapi `end-of-call-report`
  shapes: each of `confirmed / reschedule_needed / wrong_person / out_of_scope`
  maps to its status; a report with **no** structured outcome and a
  voicemail/no-answer `endedReason` → `no_answer`; an unrecognised `endedReason`
  with no outcome → `no_answer` (never `confirmed`); a "could not connect" shape
  → `call_failed`. Named `test_classify_never_confirms_without_the_literal_string`.
- **`verify_webhook()`** — correct secret → `True`; wrong → `False`; missing
  header → `False`; empty configured secret → `False` regardless of header; uses
  `hmac.compare_digest` (constant-time).

### `tests/voice_reminder_commander_test.py` (pure, no stack, no key)

- one case per **VR1–VR14**;
- a re-run of representative **R / E / A / AP** cases proving the four existing
  blocks are unchanged; assert `EXECUTABLE_ACTIONS` / `MANUAL_ACTIONS` /
  `ELIGIBILITY_TRIGGERS` / `PRIOR_AUTH_TRIGGERS` / `APPEAL_TRIGGERS` untouched;
- **the consent-gate fuzz** — over
  `trigger × vr.status × context.can_call × authorized_by{set,None}`, assert on
  every result:
  - determinism (`decide` twice → identical);
  - **`decision.next_status is None`** — every case (§2.4);
  - `route_to == "17-voice-reminder"` ⟹ `reason_code == "voice_reminder_place_call"`
    **and** `context.can_call is True` **and** `trigger.type in
    {"voice_reminder_due", "voice_reminder_enrolled"}`;
  - `context.can_call is not True` ⟹ `route_to != "17-voice-reminder"`;
- named `test_a_reminder_never_calls_without_consent_and_authorization`,
  `test_voice_reminder_never_touches_claim_or_appointment_status`.

### `tests/voice_reminder_webhook_test.py` (local stack, no key)

- bad / missing `X-Vapi-Secret` → `401`, and **no** `voice_reminders` /
  `activity_log` row is read or written;
- unknown `call.id` → `200`, ignored, one `vapi_webhook_unmatched_call` log line,
  no row touched;
- a genuine `end_reminder_call: confirmed` for a real `calling` row → row
  `confirmed`, `completed_at` set, **no** escalation;
- `reschedule_needed` / `wrong_person` / `out_of_scope` → row set accordingly +
  **exactly one** `escalations` row each (`voice_reminder_outcome_needs_human`),
  org-scoped, `voice_reminder_id` + `appointment_id` set;
- an `end-of-call-report` with no structured outcome + a voicemail `endedReason`
  → `no_answer` + one escalation;
- **W3**: feed an event whose body carries `orgId` / `clinicName` for Clinic B
  against a Clinic A `calling` row — assert the write lands on Clinic A and no
  Clinic B row is created or read;
- a replayed identical event → idempotent (same terminal row, no second
  escalation).

### `tests/voice_reminder_orchestrator_test.py` (local stack, no key; `place_call` monkeypatched)

- monkeypatch `place_call` to return a fake `VapiCall`: enroll (consent + phone +
  auth) → `voice_reminder_due` → VR6 → `_run_place_call` → row `calling` with the
  fake `vapi_call_id`, `variable_values` populated with the four keys, one
  `call_placed` activity row;
- monkeypatch `place_call` to raise `VoiceReminderUnavailable` → row
  `call_failed` → VR12 → one escalation; `place_call` was attempted exactly once
  (no retry in Phase 6);
- enroll with `reminder_consent = false` → VR2/VR7 → row `skipped_no_consent` →
  one escalation, and **`place_call` is never invoked** (assert the monkeypatch's
  call count is 0) — the V2 hard-`raise` is also exercised by a direct crafted
  call;
- enroll with a non-E.164 phone → `skipped_no_phone`, `place_call` not invoked;
- the V2 hard-`raise`: craft a state with `route_to == "17-voice-reminder"` but
  `consent_snapshot = false` and assert `handle_voice_reminder` raises
  `RuntimeError` (defence in depth, like the eligibility / appeals guards);
- every `CommanderDecision` seen in these flows had `next_status is None`.

### `tests/e2e_voice_reminder_test.py` (local stack; `place_call` stubbed — real end to end otherwise)

| # | setup | asserted |
|---|-------|----------|
| 1 | future appt, consent on file, valid phone → enroll → due-scan → stubbed place → webhook `confirmed` | row `confirmed`; appointment + claims untouched; activity chain complete; all rows org-scoped |
| 2 | as 1 but webhook returns `reschedule_needed` | row `reschedule_requested`; one escalation `voice_reminder_outcome_needs_human`, org-scoped |
| 3 | future appt, **no consent** → enroll | row `skipped_no_consent`; one escalation; the stub `place_call` recorded zero calls |
| 4 | future appt, consent, bad phone `"415-555-0142"` → enroll | row `skipped_no_phone`; one escalation; zero calls |
| 5 | stub `place_call` raises | row `call_failed`; one escalation `voice_reminder_call_failed` |
| 6 | second org re-runs scenario 1 | Clinic A sees no new rows; the webhook for B's call never reads an A row |
| 7 | collected across 1–6 | every `CommanderDecision` had `next_status is None`; every `voice_reminders` / `activity_log` / `escalations` row carried the right `organization_id` |

### `tests/voice_reminder_live_test.py` (opt-in `--live-vapi`; needs `VAPI_API_KEY` + `VAPI_LIVE_TEST_NUMBER`)

**Off by default. It rings a real phone and costs money.** Places one real call
via `place_call` to `VAPI_LIVE_TEST_NUMBER` with obviously-synthetic variables,
asserts a `vapi_call_id` comes back and `GET /call/{id}` is retrievable, then
(optionally) waits for the webhook and asserts a terminal row. Documented in
`PHASE-6.md` "how it was verified" as a manual gate, like the `--live` appeal
draft test.

### `tests/agent_isolation_test.py` (extended)

A reminder run over Clinic A's appointment never reads or writes a Clinic B
`voice_reminders` / `activity_log` / `escalations` row; the webhook path, driven
only by `vapi_call_id`, stays A-scoped even when the event body names B.

### `scripts/seed_voice_reminders.py`

Reuses the two seed clinics and their appointments. Sets `patient_phone` +
`reminder_consent` across a spectrum (most consented with a valid number, one
with no consent, one with a malformed number), enrolls each with a
`scheduled_call_at` in the **past** so a single `run_voice_reminder_due_scan.py`
pass fires them, and **stubs `place_call`** (deterministic fake `vapi_call_id`)
unless `--live-vapi`. Then feeds synthetic `end-of-call-report` payloads through
the webhook for a realistic outcome mix (mostly `confirmed`, a couple
`reschedule_needed`, one `no_answer`). Prints the outcome distribution like
`seed_prior_auth.py`.

### `scripts/run_voice_reminder_proof.sh`

Pure agent + Commander tests (no stack) → the stack tests against a fresh
`supabase db reset` → the seed. `--live-vapi` additionally runs
`voice_reminder_live_test.py`.

---

## 12. Open decisions — resolve at review

1. **VR block: a disjoint family vs. extending R1–R20 / a care family.** →
   recommend **disjoint fifth family**, dispatched before R1 like E / A / AP.
   `commander_test.py` and the other three suites stay byte-for-byte.
2. **The human-in-the-loop model for the call itself.** → recommend **the
   per-appointment enrollment IS the recorded approval** — a human calls
   `POST /api/appointments/{id}/voice-reminder`, which records `authorized_by`;
   the call then fires automatically at `scheduled_call_at`. Alternatives: (a) a
   per-call approval queue (a human clicks "place this call now" for every row —
   heavier, and appointment reminders are inherently routine); (b) a standing
   clinic-level "remind all appointments" policy (no per-row human touch — too
   loose for the first real external action). Given §1.1, recommend (enrollment)
   **plus** explicit review sign-off that enrollment + fail-closed consent + the
   already-configured disclosure is sufficient, and we do **not** also gate each
   call.
3. **Consent storage.** → recommend **fields on `appointments`, snapshotted onto
   `voice_reminders`** (matches `eligibility_checks` / `prior_authorizations`).
   Alternative: a `patient_contacts` table keyed by `(patient_name, patient_dob,
   phone)` holding consent + source + revocation — better model, more surface;
   defer to a later phase.
4. **`no_answer` / voicemail handling (VR11).** → recommend **route to 12**
   (matches the user's "anything other than confirmed → a human"). Alternatives:
   record-only (no escalation) so the queue is not noisy; or **one** automatic
   retry N hours later before escalating. Flag for review — a retry needs a
   second `scheduled_call_at` and a retry counter on the row.
5. **Call recording & transcript.** → recommend **persist neither in Phase 6**.
   Either disable recording on the Vapi assistant, or (if kept for QA) store the
   recording URL behind a retention purge exactly like the card images
   (`20260907000002`). New `BEFORE-PHI.md` rows regardless.
6. **Webhook authentication.** → recommend **shared secret `X-Vapi-Secret`**
   (constant-time compare) as the baseline — it matches Vapi's server-URL-secret
   model and needs no key management beyond one env var. Add **HMAC signature
   verification** over the raw body if/when Vapi signs, and note **source-IP
   allowlisting** as defence-in-depth. Confirm the exact header name against
   Vapi's current docs at build.
7. **The due-scan mechanism.** → recommend a **cron-invoked script**
   (`run_voice_reminder_due_scan.py`), same operational shape as the card-image
   purge. Alternatives: a Postgres `pg_cron` job emitting a NOTIFY; an in-process
   `asyncio` scheduler (rejected — dies with the process, and the backend may run
   multiple workers).
8. **`organizations.timezone`.** → recommend **add the column now**, default
   `America/New_York` as an explicit placeholder, and surface it in org settings
   in a later phase. Without it `{{appointment_date}}`/`{{appointment_time}}` are
   wrong. Alternative: a per-appointment `timezone` column.
9. **`activity_log` / `escalations` `voice_reminder_id` column.** → recommend
   **add it** (appointment-scoped family, `appointment_id` alone is ambiguous
   across multiple reminders) — follows the eligibility / prior-auth precedent
   rather than the appeals `context.*_id`-only approach.
10. **Vapi payload shapes.** The exact JSON paths for the structured outcome, the
    ended reason, and the call id in `end-of-call-report` / `tool-calls` are
    **to be pinned against a real captured payload** before `classify_outcome` is
    finalised. The classifier is written to fail toward `no_answer` on anything
    unrecognised, so a wrong guess never yields a false `confirmed`.
11. **`{{patient_name}}` — full name vs. first name.** → recommend **first name
    only** for a natural call ("Hi, is this Maria?"), derived from
    `patient_name.split()[0]`. Confirm.
12. **Retry on a transient `place_call` failure.** → recommend **no retry in
    Phase 6** — a `call_failed` routes to a human who re-enrolls. A bounded retry
    (like `executors.py`'s) is a clean later addition.
13. **Reconcile pass for lost webhooks.** → recommend the due-scan also sweeps
    `calling` rows older than ~1h with no terminal outcome and pulls
    `GET /call/{id}` from Vapi to backfill. Confirm scope.
14. **UI placement.** → recommend **folding the queue into Tasks** rather than a
    new nav item (reminders are lower-volume than prior-auth). Confirm.
15. **Agent number 17.** 11 went to appeals (Phase 5); 13–16 are left unassigned
    (13 was pencilled as a claims-pipeline `auth-submitter` alternative in Phase
    3; 14–16 held for other patient-access / claims work). 17 is the voice agent.
    Confirm the number, or renumber.
16. **Real external action — explicit review gate.** This is the first agent that
    is not a simulation. Confirm the review is comfortable that: (a) the call is
    disclosed + clinic-identified in Vapi; (b) `authorized_by` + fail-closed
    consent are the guardrails; (c) no transcript/recording is stored; (d) the
    unauthenticated webhook's W1–W4 controls are sufficient — before code starts.

`00-commander.md` §2 / §5 / §15 and `architecture.md` §6 / §7 to be updated to
match once these are resolved.
