# 17 — Voice Reminder Agent (n8n workflow)

_Spec. Written before implementation, per the Phase 1–5 discipline. Cross-check
this document before any Phase 6 code or n8n workflow is built._

_Status: **SPEC — awaiting review (rev. 3 — n8n architecture).** Companion doc:
[`../PHASE-6.md`](../PHASE-6.md) (to be written at build time). **The Commander is
not involved** — see [`00-commander.md`](00-commander.md), the Phase 6 note after
the §14 addendum. Proposed artefacts:_

- _`supabase/migrations/20260909000001_voice_reminders.sql`_
- _`backend/app/voice/` — pure helpers (`variables.py`, `consent.py`,
  `outcomes.py`) reused by the endpoints; **no** agent, **no** orchestrator entry_
- _`backend/app/routers/voice_reminders.py` — the staff-facing endpoints
  (enroll / cancel / consent / views), JWT-scoped_
- _`backend/app/routers/voice_automation.py` — the **n8n-facing** endpoints,
  gated by a dedicated machine token_
- _`backend/app/auth.py` — a new `require_automation` dependency_
- _`n8n/voice-reminder-place-calls.json` + `n8n/voice-reminder-receive-outcome.json`
  — the two workflows, exported and version-controlled in the repo_
- _`frontend/src/components/voice-reminder.tsx`, `patient-consent.tsx`_
- _`scripts/seed_voice_reminders.py`, `tests/voice_reminder_*`_

_`commander.py` / `orchestrator.py` have **no diff**;
`commander_test.py` / `eligibility_commander_test.py` /
`prior_auth_commander_test.py` / `appeals_commander_test.py` pass byte-for-byte._

---

## 1. What 17 is

The **Voice Reminder Agent** places an **automated outbound phone call** to a
patient ahead of a scheduled appointment, using the **Vapi assistant that already
exists** (§3), to get one of two answers — _the patient confirms_, or _the
patient wants to reschedule_ — and routes everything that is not a clean
confirmation to a human by creating a real **`escalations`** row (the same table
12-escalation-agent writes), so it lands in the same Tasks queue as a denied
prior-auth and an upheld appeal.

### 1.1 It is an **n8n workflow**, not a Commander agent

Phases 2–5 each added a disjoint Commander rule family. **Phase 6 does not.**
After review, agent 17 is built as an **n8n workflow** that calls a narrow set of
**FastAPI automation endpoints**. The reasons:

- The work is a **time-driven batch loop** (poll for due reminders, dial, receive
  a callback) with no claim/appointment lifecycle to advance — it does not fit
  the `(state, trigger) → CommanderDecision` shape.
- Keeping it out of `commander.py` means the four existing Commander test suites
  stay **byte-for-byte unchanged** — the Phase 1 invariant engine is not touched
  to ship a reminder feature.
- n8n gives the scheduling, ret/branching, and webhook plumbing for free; the
  parts that must be **correct and tenant-safe** (consent resolution, variable
  rendering, outcome classification, escalation creation) stay in tested Python
  behind the endpoints.

| layer | responsibility |
|-------|----------------|
| **n8n** | *when* (schedule), *sequencing* (call Vapi, then report back), *transport* (receive Vapi's webhook, verify its secret). Holds **no Supabase credential** and **no cross-clinic data access** beyond one purpose-built endpoint (§2.3). |
| **FastAPI automation endpoints** (`/api/automation/voice-reminders/*`) | the authoritative logic: which reminders are due, live consent re-check, variable resolution, `voice_reminders` state transitions, outcome classification, **escalation creation**. Tenant-scoped from the `voice_reminders` row every time. |
| **FastAPI staff endpoints** (`/api/appointments/{id}/voice-reminder`, `/api/patient-contacts/*`) | the human-in-the-loop: a staff member captures consent and **authorizes** a reminder. JWT-scoped to their clinic. |
| **Commander / orchestrator** | **nothing.** No diff. |

### 1.2 This is still the first **real external action** in the system

Every prior phase **simulated** the leg that leaves the building (09/10
`_simulated_send`, 01/02/11 deterministic simulations). Agent 17 makes a real
phone ring via `POST https://api.vapi.ai/call`. Three consequences carry over
from rev. 2 and are now enforced by the **endpoints and n8n**, not by a Commander
rule:

1. **Human-in-the-loop.** `architecture.md` §1.2 — a patient communication needs a
   recorded human approval. The staff **enrollment** call
   (`POST /api/appointments/{id}/voice-reminder`) records `authorized_by`; the
   `/due` endpoint refuses to surface a reminder with no `authorized_by`, and
   `mark-calling` asserts it. **Decision §11.2: the per-appointment enrollment IS
   that approval — approved. No per-call approval, and no automatic retry of a
   missed or failed reminder** (an auto-redial without human review is a
   TCPA-adjacent risk).
2. **TCPA consent is a precondition, fail-closed, re-checked live.** Consent is a
   durable, auditable, revocable property of a patient + number — stored in a
   dedicated append-only **`patient_consents` ledger** (§4.1, **decision §11.3**).
   The `/due` endpoint and `mark-calling` both re-resolve consent from the **live
   ledger**; a revocation any time before the call is dialed stops the call and
   creates an escalation.
3. **Call recording disabled entirely.** **Decision §11.4:** recording is off on
   the Vapi assistant, and n8n's call-create body additionally sends
   `assistantOverrides.artifactPlan.recordingEnabled = false`. The
   `outcome` endpoint stores only the **structured `end_reminder_call` result**
   and metadata — **never** transcript text or a recording URL.

New [`../BEFORE-PHI.md`](../BEFORE-PHI.md) rows: TCPA voice-consent capture +
retention; the recording-disabled attestation; the Vapi BAA; `N8N_SERVICE_TOKEN`
+ `VAPI_WEBHOOK_SECRET` + the n8n instance itself in secrets management; and the
fact that the `/due` endpoint returns patient phone numbers to n8n (§2.3).

---

## 2. The non-negotiables

### 2.1 Nothing calls a patient without a recorded human authorization

> A `voice_reminders` row is created only by `POST
> /api/appointments/{id}/voice-reminder`, which sets `authorized_by` from the
> **verified staff session** (never a request body). The `/due` endpoint filters
> out any row with `authorized_by IS NULL`; `mark-calling` returns `409` if it is
> null. No n8n node can create a `voice_reminders` row.

### 2.2 No call without a current TCPA voice-consent record — fail-closed, re-checked live

> `can_call` is `True` only when, **at the moment of the check**, the number has a
> `patient_consents` row for channel `voice` whose latest state is `granted`, and
> the number is valid E.164. Absent consent, a `revoked` latest state, missing or
> malformed number, or any ambiguity → `can_call = False`.
>
> The check runs **three times**, each against the **live** ledger:
> 1. at **enrollment** — `409` if not granted (fail fast, clean UX);
> 2. at **`/due`** — a pending row whose consent has since been revoked is set to
>    `skipped_no_consent`, an escalation is created, and it is **omitted from the
>    response** — n8n never sees it;
> 3. at **`mark-calling`** — the final gate, closing the seconds-wide race
>    between `/due` and the dial; `409 {reason:"consent_revoked"}` → the row is
>    skipped + escalated and n8n cancels the just-placed Vapi call.

`current_voice_consent()` (pure — §9) reduces the append-only ledger to one
state: the row with the greatest `recorded_at`; `granted` iff that row's state is
`granted`; no rows → not granted.

### 2.3 n8n holds no Supabase credential and no broad cross-clinic access

> n8n authenticates to FastAPI with a single **machine bearer token**
> (`N8N_SERVICE_TOKEN`), verified by `require_automation`. That principal:
> - is **not** a Supabase role — it cannot reach PostgREST, the service-role key,
>   or any table directly;
> - is authorized for **only** the `/api/automation/voice-reminders/*` routes —
>   nothing else in the API accepts it;
> - each of those routes returns a **purpose-shaped payload** (a due reminder's
>   resolved variables + dial target; an outcome ack) — never a general query
>   surface, never a row from another table.
>
> The `/due` endpoint does return reminders **across clinics** (it is the
> cross-clinic dispatch queue) and includes each patient's **phone number** (n8n
> must dial it). That is the entire blast radius if `N8N_SERVICE_TOKEN` leaks:
> "read pending appointment-reminder dial targets and post call outcomes" — not
> "read PHI across the platform". A tighter model (per-clinic tokens, clinic
> opt-in) is **open decision §11.6**.

Every automation endpoint resolves `organization_id` **from the `voice_reminders`
row** (by id, or by the unique `vapi_call_id`) and scopes every subsequent write
to it — exactly as the Phase 1–5 orchestrator does. `tests/agent_isolation_test.py`
gains an automation slice.

### 2.4 The outcome path never learns tenancy from Vapi's payload

> `POST /api/automation/voice-reminders/outcome` looks up the `voice_reminders`
> row **only** by the `vapi_call_id` n8n forwards. No match → `200`, logged,
> **ignored**. `organization_id` comes from that row. Anything in the forwarded
> Vapi `message` that looks like an org / clinic identifier is untrusted display
> data. `vapi_call_id` is `unique`, so a replayed callback updates the same row
> idempotently.

### 2.5 A reminder never changes a claim or an appointment

> The only rows Phase 6 writes are `patient_contacts`, `patient_consents`,
> `voice_reminders`, `activity_log`, and `escalations`. Nothing on `appointments`
> or `claims` is ever touched — no status, no column. (Same discipline as the
> eligibility and prior-auth sidecars.)

### 2.6 The Commander is untouched

No `_decide_voice_reminder`, no `VOICE_REMINDER_TRIGGERS`, no
`orchestrator.handle_voice_reminder`, no new `route_to` value, no new
`reason_code`. `commander.py` and `orchestrator.py` have no diff. The "non-confirmed
→ a human" routing that a Commander rule would have done is a plain `if` in the
`outcome` endpoint (§6.3), and it reuses `escalation.escalate()` and the
`escalations` table verbatim.

---

## 3. What already exists in Vapi (do **not** rebuild) vs. what we build

### 3.1 Already built, in Vapi's dashboard — fixed infrastructure

| Thing | Identifier | Notes |
|-------|-----------|-------|
| Published assistant **"Healthcare Appointment Reminder"** | `VAPI_ASSISTANT_ID` = `ec0bcddf-b32e-4afb-982d-fd0fb852c7e4` | Prompt, voice, model, turn-taking configured **there**. We never send a prompt. |
| Dynamic variables | `{{clinic_name}}`, `{{patient_name}}`, `{{appointment_date}}`, `{{appointment_time}}` | Currently empty. Phase 6 fills **exactly these four** via `assistantOverrides.variableValues` on the call-create request. |
| Attached tool **`end_reminder_call`** | Tool ID `1629964c-f437-4548-b0b7-f81273e60e92` | Structured outcome ∈ `{confirmed, reschedule_needed, wrong_person, out_of_scope}`. We **read** it. |
| Configured behaviour | automated-call disclosure · clinic-only identity (never "Foresight" / any platform) · identity check before details · accepts only confirm or reschedule · ends promptly · "a representative from {{clinic_name}} will reach out" for out-of-scope | Already in the assistant. Phase 6 adds no conversational logic. |
| **Recording** | **must be OFF on the assistant** (decision §11.4) | n8n also sends `recordingEnabled: false` on every call. Confirm the toggle is off as part of the Phase 6 checklist + a BEFORE-PHI attestation. |
| Credentials | `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_ASSISTANT_ID` in `backend/.env` | Already set. **These move into n8n credentials** (n8n calls Vapi directly). The backend keeps `VAPI_ASSISTANT_ID` / `VAPI_PHONE_NUMBER_ID` only to **echo** them in the `/due` payload so n8n does not hardcode them (§6.1). Phase 6 adds **`VAPI_WEBHOOK_SECRET`** (shared with n8n) and **`N8N_SERVICE_TOKEN`**. |
| **Server URL** | to be set to the **n8n webhook URL** (§7.2) | with `VAPI_WEBHOOK_SECRET` as the Server URL Secret, so every callback carries `X-Vapi-Secret`. |

### 3.2 What Phase 6 builds

| # | Piece | Where |
|---|-------|-------|
| 1 | Data model — `patient_contacts` + `patient_consents` ledger; `voice_reminders` + `voice_reminder_status` enum (incl. `dispatching`); `organizations.timezone`; audit columns | `supabase/migrations/20260909000001_voice_reminders.sql` |
| 2 | Pure helpers — `resolve_variables()`, `current_voice_consent()`, `consent_gate()`, `classify_outcome()` | `backend/app/voice/` |
| 3 | Staff endpoints — enroll / cancel / consent grant+revoke / views (JWT) | `backend/app/routers/voice_reminders.py` |
| 4 | Automation endpoints — `/due`, `/{id}/mark-calling`, `/outcome`, `/{id}/dispatch-failed` (machine token) | `backend/app/routers/voice_automation.py` |
| 5 | `require_automation` dependency + `N8N_SERVICE_TOKEN` setting | `backend/app/auth.py`, `config.py` |
| 6 | Two n8n workflows (exported JSON in the repo) | `n8n/*.json` |
| 7 | UI — consent panel + reminder card on the appointment page; queue folded into Tasks | `frontend/src/components/*` |
| 8 | Seed + tests | `scripts/`, `tests/` |

---

## 4. Data model

New migration `20260909000001_voice_reminders.sql`. Every new table carries
`organization_id uuid not null references public.organizations(id)` and runs
through `select public.enable_tenant_isolation('public.<table>')`. No hardcoded
tenant id. The automation endpoints write with the service-role key (background
job — the `architecture.md` §3.4 carve-out) and filter every query by the
`organization_id` resolved from the `voice_reminders` row.

### 4.1 `patient_contacts` + `patient_consents` — the consent ledger (decision §11.3)

```sql
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

create type public.consent_channel as enum ('voice');   -- 'sms' / 'email' are later phases
create type public.consent_state   as enum ('granted', 'revoked');

-- APPEND-ONLY. Current consent for (contact, channel) = the row with the greatest recorded_at.
create table public.patient_consents (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  patient_contact_id uuid not null references public.patient_contacts (id) on delete cascade,
  channel            public.consent_channel not null default 'voice',
  state              public.consent_state   not null,
  source             text not null,     -- granted: 'intake_form'|'patient_portal'|'verbal_documented'
                                        -- revoked: 'patient_request'|'staff_correction'|'returned_call_opt_out'
  note               text,
  recorded_by        uuid references auth.users (id) on delete set null,
  recorded_at        timestamptz not null default now(),
  created_at         timestamptz not null default now()
);

create index patient_contacts_lookup_idx on public.patient_contacts (organization_id, patient_name, patient_dob);
create index patient_consents_contact_idx on public.patient_consents (patient_contact_id, channel, recorded_at desc);

comment on table public.patient_consents is
  'Append-only TCPA consent ledger. Current consent for (contact, channel) = the row with the greatest recorded_at; a revocation is a new state=revoked row. The Phase 6 voice-reminder automation will not place a call unless the current voice-channel state is granted (fail-closed). Tenant-scoped.';
```

### 4.2 `organizations.timezone` (decision §11.5)

```sql
alter table public.organizations
  add column timezone text not null default 'America/New_York';   -- IANA tz for {{appointment_date}}/{{appointment_time}}
```

`resolve_variables()` **raises** on an invalid IANA zone → the `/due` endpoint
records the row `error` + an escalation, and omits it — never a mis-timed call.

### 4.3 Enum + `voice_reminders`

```sql
create type public.voice_reminder_status as enum (
  'pending',              -- enrolled + authorized by a human; not yet dispatched
  'skipped_no_consent',   -- no current granted voice consent — no call, escalated (enroll / due / mark-calling)
  'skipped_no_phone',     -- no usable E.164 number — no call, escalated
  'cancelled',            -- a human cancelled before dispatch
  'dispatching',          -- returned to n8n by /due, lease held; awaiting mark-calling (or a lost-dispatch sweep)
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

  -- snapshots at enrollment
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

  authorized_by             uuid references auth.users (id) on delete set null,   -- the HITL approval (V1)
  authorized_at             timestamptz,

  vapi_assistant_id         text,
  vapi_phone_number_id      text,
  vapi_call_id              text,                   -- the ONLY join key the outcome path trusts
  variable_values           jsonb not null default '{}'::jsonb,   -- exactly the 4 vars, verbatim, for audit

  outcome                   text,                   -- raw end_reminder_call string, or NULL
  outcome_payload           jsonb not null default '{}'::jsonb,   -- {outcome, ended_reason, duration_seconds, vapi_call_status, cost} — NO transcript / recording

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
```

### 4.4 Audit trail

```sql
alter table public.activity_log
  add column patient_contact_id uuid references public.patient_contacts (id) on delete cascade,
  add column voice_reminder_id  uuid references public.voice_reminders  (id) on delete cascade;
alter table public.escalations
  add column voice_reminder_id  uuid references public.voice_reminders  (id) on delete cascade;
```

Consent grant / revoke → an `activity_log` row (`actor="human:<uid>"`,
`action="voice_consent_granted"` / `"voice_consent_revoked"`,
`patient_contact_id` set). Every automation step → an `activity_log` row with
`voice_reminder_id` set. `insert_activity` / `insert_escalation` gain the kwargs
(**decision §11.9: add the columns**).

### 4.5 Isolation + grants

```sql
select public.enable_tenant_isolation('public.patient_contacts');
select public.enable_tenant_isolation('public.patient_consents');
select public.enable_tenant_isolation('public.voice_reminders');

revoke all on public.patient_contacts, public.patient_consents, public.voice_reminders from anon, authenticated;
grant  select on public.patient_contacts, public.patient_consents, public.voice_reminders to authenticated;
```

No client write grant. **No grant of any kind to n8n** — n8n never touches
Postgres; it only calls the §6 endpoints.

---

## 5. Settings

```python
# app/config.py
vapi_assistant_id: str = ""       # VAPI_ASSISTANT_ID     (already set — echoed to n8n in /due)
vapi_phone_number_id: str = ""    # VAPI_PHONE_NUMBER_ID   (already set — echoed to n8n in /due)
vapi_webhook_secret: str = ""     # VAPI_WEBHOOK_SECRET    (NEW — the Vapi Server URL Secret; n8n verifies it, we do not receive Vapi directly)
n8n_service_token: str = ""       # N8N_SERVICE_TOKEN      (NEW — the machine bearer token for /api/automation/voice-reminders/*)

voice_reminder_lead_hours: int   = 24    # place the call this many hours before the appointment
voice_dispatch_lease_minutes: int = 15   # a 'dispatching' row with no mark-calling after this -> lost-dispatch sweep
voice_due_batch_limit: int        = 50   # max reminders returned by one /due poll
```

`VAPI_API_KEY` **leaves `backend/.env`** and becomes an **n8n credential** (n8n
is the only thing that calls Vapi now). Named constants in `backend/app/voice/`:

```python
CONFIRMED_OUTCOME   = "confirmed"
OUTCOME_TO_STATUS   = {"confirmed": "confirmed", "reschedule_needed": "reschedule_requested",
                       "wrong_person": "wrong_person", "out_of_scope": "out_of_scope"}
TERMINAL_ENDED_REASONS_NO_OUTCOME = frozenset({
    "customer-did-not-answer", "voicemail", "customer-busy",
    "customer-ended-call-before-outcome", "silence-timed-out"})
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
NO_AUTORETRY = True   # decision §11.2 — a missed/failed reminder is ALWAYS a human decision
```

Exact Vapi payload paths (§9.4) are pinned against a real captured
`end-of-call-report` at build; the classifier fails toward `no_answer`, never
`confirmed`.

---

## 6. The FastAPI automation endpoints (the n8n-facing contract)

Router `backend/app/routers/voice_automation.py`, prefix
`/api/automation/voice-reminders`, **every** route `Depends(require_automation)`.

`require_automation` (in `auth.py`): reads `Authorization: Bearer <token>`,
`hmac.compare_digest` against `settings.n8n_service_token` (non-empty required),
returns a marker principal. It is accepted on **no other router**. No Supabase
call, no user lookup.

### 6.1 `GET /api/automation/voice-reminders/due`

n8n's Schedule workflow polls this (every ~5 min).

**Query:** `?limit=` (default `voice_due_batch_limit`), `?now=` (tests only).

**Behaviour (one transaction per row):**
1. Select `voice_reminders` where `status = 'pending'` and `scheduled_call_at <= now`
   and `authorized_by is not null`, oldest first, up to `limit`.
2. Also select `status = 'dispatching'` rows with `dispatched_at < now - lease`
   → **lost dispatch**: set `status = 'error'`, create an escalation
   (`voice_reminder_dispatch_lost`), log; **do not** return them, **do not**
   re-dial (decision §11.2).
3. For each candidate: load the appointment, the organization, and the **live**
   `patient_consents` rows for `patient_contact_id`.
   - `current_voice_consent()` → not granted → `status = 'skipped_no_consent'`,
     escalation `voice_reminder_no_consent_needs_human`, update `consent_snapshot`,
     log; **omit from the response**.
   - phone invalid → `status = 'skipped_no_phone'`, escalation
     `voice_reminder_no_phone_needs_human`; **omit**.
   - `resolve_variables()` raises (bad tz / bad data) → `status = 'error'`,
     escalation `voice_reminder_render_failed`; **omit**.
   - otherwise → set `status = 'dispatching'`, `dispatched_at = now`; **include**.

**Response `200`:**
```json
{
  "due": [
    {
      "voice_reminder_id": "8f1c…",
      "vapi": {
        "assistant_id": "ec0bcddf-b32e-4afb-982d-fd0fb852c7e4",
        "phone_number_id": "…",
        "customer_number": "+14155550142",
        "variable_values": {
          "clinic_name": "Maple Family Practice",
          "patient_name": "Maria",
          "appointment_date": "Tuesday, September 15",
          "appointment_time": "2:30 PM"
        },
        "recording_enabled": false
      }
    }
  ]
}
```
No status change for a returned row beyond `pending → dispatching`. If n8n dies
after the poll, the row is swept as a lost dispatch (step 2) → human, never
re-dialed.

### 6.2 `POST /api/automation/voice-reminders/{id}/mark-calling`

n8n calls this **immediately after** Vapi's call-create returns.

**Body:** `{ "vapi_call_id": "…", "vapi_call_status": "queued" }`

**Behaviour:**
- Load the row. `404` if unknown. If already `calling` with the same
  `vapi_call_id` → `200` no-op (idempotent).
- `409 {reason:"not_dispatching"}` unless `status = 'dispatching'`.
- **Final live consent re-check** (§2.2 step 3): re-resolve
  `current_voice_consent()`. Not granted → `status = 'skipped_no_consent'`,
  escalation, `409 {reason:"consent_revoked"}` (n8n then cancels the Vapi call).
- Otherwise → `status = 'calling'`, store `vapi_call_id`, `placed_at = now`,
  `vapi_assistant_id` / `vapi_phone_number_id` snapshots, `variable_values`
  (echoed back so the audit row is authoritative); log `call_placed`.
- `200 { "status": "calling" }`.

### 6.3 `POST /api/automation/voice-reminders/outcome`

n8n's Webhook workflow calls this after Vapi's `end-of-call-report` (having
verified `X-Vapi-Secret` at the Webhook node — §7.2).

**Body:** `{ "vapi_call_id": "…", "message": { …verbatim Vapi end-of-call-report… } }`

**Behaviour:**
- `voice_reminder_by_call_id(vapi_call_id)` → `None` → `200 {"ignored":true}`,
  logged `vapi_webhook_unmatched_call` (§2.4).
- `organization_id` from the row.
- `classify_outcome(message)` (pure — §9.4) → one of `confirmed` /
  `reschedule_requested` / `wrong_person` / `out_of_scope` / `no_answer` /
  `call_failed`, plus `outcome_payload = {outcome, ended_reason,
  duration_seconds, vapi_call_status, cost}` — **transcript and recording URL are
  never read or stored**.
- Update the row: `status`, `outcome`, `outcome_payload`, `completed_at = now`.
- **The routing** (the plain `if` that replaces a Commander rule):
  - `status == "confirmed"` → done. `activity_log` `outcome`. No escalation.
  - else → `escalation.escalate(org_id, claim_pk=None,
    appointment_id=<row>, voice_reminder_id=<row>, reason_code=<mapped>,
    context={outcome, ended_reason, payer/appt refs})`; store `escalation_id`;
    `activity_log` `outcome` + `escalated`.
    - `reschedule_requested` / `wrong_person` / `out_of_scope` →
      `voice_reminder_outcome_needs_human`
    - `no_answer` → `voice_reminder_no_outcome_needs_human`
    - `call_failed` → `voice_reminder_call_failed`
- Idempotent: a replayed callback on an already-terminal row → `200`, no second
  escalation.
- `200 { "status": "...", "escalated": true|false }`.

### 6.4 `POST /api/automation/voice-reminders/{id}/dispatch-failed`

n8n calls this if Vapi's call-create returns non-2xx / times out.

**Body:** `{ "error": "…", "vapi_status": 502 }`

**Behaviour:** `409` unless `status = 'dispatching'`. Set `status = 'call_failed'`,
escalation `voice_reminder_call_failed`, log. **No retry** (decision §11.2).
`200 { "status": "call_failed" }`.

### 6.5 Endpoint summary

| Method | Path | Caller | Purpose |
|--------|------|--------|---------|
| `GET` | `/due` | n8n schedule | due reminders → resolved dial targets; leases to `dispatching`; sweeps lost dispatches; skips + escalates consent/phone/render failures |
| `POST` | `/{id}/mark-calling` | n8n | `dispatching → calling`; final live consent gate; stores `vapi_call_id` |
| `POST` | `/outcome` | n8n webhook | classify Vapi's callback; update row; **non-confirmed → escalation** |
| `POST` | `/{id}/dispatch-failed` | n8n | call-create failed → `call_failed` + escalation |

---

## 7. The n8n workflows

Two workflows, exported to `n8n/*.json` and committed. n8n credentials:
**Foresight Automation Token** (`N8N_SERVICE_TOKEN`, header auth), **Vapi API
Key**, **Vapi Webhook Secret**. None is a Supabase credential.

### 7.1 Workflow A — "voice-reminder: place due calls" (Schedule trigger)

| # | Node | Type | Does |
|---|------|------|------|
| 1 | **Every 5 minutes** | Schedule Trigger | fires the poll |
| 2 | **Get due reminders** | HTTP Request | `GET {{FORESIGHT_API}}/api/automation/voice-reminders/due` · header `Authorization: Bearer` (Automation Token credential) · returns `{due:[…]}` |
| 3 | **Has due items?** | IF | `{{$json.due.length}} > 0` → true branch; else end |
| 4 | **Split** | Split Out (field `due`) | one item per reminder |
| 5 | **Place Vapi call** | HTTP Request | `POST https://api.vapi.ai/call` · Vapi API Key credential · body: `assistantId` = `{{$json.vapi.assistant_id}}`, `phoneNumberId` = `{{$json.vapi.phone_number_id}}`, `customer.number` = `{{$json.vapi.customer_number}}`, `assistantOverrides.variableValues` = `{{$json.vapi.variable_values}}`, `assistantOverrides.artifactPlan.recordingEnabled` = `false` · **Continue On Fail = true** |
| 6 | **Call created?** | IF | `{{$json.id}}` present (2xx) → 7; else → 6b |
| 6b | **Report dispatch failure** | HTTP Request | `POST …/api/automation/voice-reminders/{{$node["Split"].json.voice_reminder_id}}/dispatch-failed` · body `{error, vapi_status}` · → end item |
| 7 | **Mark calling** | HTTP Request | `POST …/api/automation/voice-reminders/{{…voice_reminder_id}}/mark-calling` · body `{vapi_call_id: {{$json.id}}, vapi_call_status: {{$json.status}}}` · **Continue On Fail = true** |
| 8 | **Consent revoked mid-flight?** | IF | response `409` and `{{$json.reason}} == "consent_revoked"` → 8b; else end item |
| 8b | **Cancel Vapi call** | HTTP Request | `DELETE https://api.vapi.ai/call/{{$node["Place Vapi call"].json.id}}` (or Vapi's cancel endpoint) · the backend already recorded the skip + escalation |

The call is now live at Vapi; its outcome arrives via Workflow B.

### 7.2 Workflow B — "voice-reminder: receive Vapi outcome" (Webhook trigger)

The Webhook node's production URL is set as the **Vapi Server URL**; the
**Vapi Webhook Secret** is set as Vapi's Server URL Secret, so every callback
carries `X-Vapi-Secret`.

| # | Node | Type | Does |
|---|------|------|------|
| 1 | **Vapi callback** | Webhook (POST, `/webhook/vapi-reminder`) | receives `end-of-call-report`, `status-update`, `tool-calls`, `hang` |
| 2 | **Verify secret** | IF | `{{$headers["x-vapi-secret"]}}` equals the Vapi Webhook Secret credential value → true; else → 2b |
| 2b | **401** | Respond to Webhook | status `401`, body `{"detail":"bad signature"}` → stop |
| 3 | **Message type** | Switch (`{{$json.body.message.type}}`) | `end-of-call-report` → 4 · `tool-calls` → 3a · default → 3b |
| 3a | **Ack tool call** | Respond to Webhook | `{"results":[{"toolCallId":"{{…}}","result":"acknowledged"}]}` → stop (wait for the report) |
| 3b | **200 ok** | Respond to Webhook | `{"ok":true}` → stop |
| 4 | **Post outcome** | HTTP Request | `POST …/api/automation/voice-reminders/outcome` · Automation Token · body `{vapi_call_id: {{$json.body.message.call.id}}, message: {{$json.body.message}}}` |
| 5 | **Respond** | Respond to Webhook | `200`, pass through `{{$json}}` from step 4 |

n8n does **no** classification, **no** consent logic, **no** escalation — it
verifies the secret, forwards the message, and relays the response. All judgement
is in the `outcome` endpoint (§6.3), where it is tested in Python.

### 7.3 What if n8n is down?

- Workflow A not running → reminders sit `pending` past `scheduled_call_at`. The
  `/due` endpoint surfaces them whenever n8n resumes (subject to a staleness cap
  — **open decision §11.11**: past a cutoff, a stale-past-due reminder is
  `error` + escalation rather than a very-late call).
- Workflow B not running → Vapi retries the Server URL for a while; if all
  retries fail, the row stays `calling`. A backend **reconcile sweep** (cron or a
  branch of `/due`) flips `calling` rows older than ~1h with no outcome to
  `error` + escalation and pulls `GET /call/{id}` from n8n's Vapi credential is
  **not** available server-side — so this sweep records `error` and a human
  checks Vapi (**open decision §11.11**).

---

## 8. Staff endpoints + UI

### 8.1 Consent (`backend/app/routers/voice_reminders.py`, JWT-scoped)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/patient-contacts?patient_name=&patient_dob=` | the caller-clinic contacts for a patient + each one's current voice-consent state and history |
| `POST` | `/api/patient-contacts` | create a contact `{patient_name, patient_dob?, phone}`; `409` on the unique key |
| `POST` | `/api/patient-contacts/{id}/consent` | **grant** — body `{source, note?}`; appends a `granted` ledger row (`recorded_by` = session user); logs `voice_consent_granted` |
| `POST` | `/api/patient-contacts/{id}/consent/revoke` | **revoke** — appends a `revoked` row; logs `voice_consent_revoked`. Any `pending` reminders for the contact are caught at `/due` / `mark-calling` (§2.2) |

### 8.2 Reminders (JWT-scoped)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/appointments/{id}/voice-reminder` | latest reminder + history + the reminder-scoped `activity_log` slice |
| `GET` | `/api/voice-reminders?status=` | the reminder work queue (RLS-scoped) |
| `POST` | `/api/appointments/{id}/voice-reminder` | **enroll + authorize.** Body `{patient_contact_id, scheduled_call_at?}`. Verifies the contact is visible **and** its `(patient_name, patient_dob)` matches the appointment (soft key — **decision §11.14**). Reads live consent — **`409` if not `granted`**. Creates the `pending` row with all snapshots + `consent_event_id_snapshot` + `authorized_by = ctx.user_id`. `409` if the appointment is past, or an active reminder exists. |
| `POST` | `/api/voice-reminders/{id}/cancel` | cancel a `pending` reminder → `cancelled`. `409` unless `pending`. |
| `GET` | `/api/appointments/{id}` **(extended)** | gains `voice_reminder` + `patient_contacts` (with consent state) |

### 8.3 UI

- **`AppointmentDetailPage`** — a **Voice Reminder** card:
  - **consent panel** — number(s) + a consent badge (`Granted 12 Aug · intake form`
    / `Revoked 3 Sep · patient request` / `No consent on file`), a **Capture
    consent** control (phone + source + an express-consent affirmation checkbox)
    and **Revoke**; the append-only history is expandable.
  - **enroll** (future appt, consent granted) — pick the number, an optional call
    time (default `appointment − lead_hours`). Disabled with "capture consent
    first" otherwise.
  - **`pending` / `dispatching`** — "Reminder call scheduled for &lt;time&gt;",
    **Cancel**.
  - **`calling`** — "Calling the patient now…".
  - **`confirmed`** — green "Patient confirmed", with time + duration.
  - **`skipped_*` / non-confirmed / `call_failed` / `error`** — the outcome stated
    plainly + a link to the escalation. **No "retry" button** (decision §11.2).
- **Queue** — folded into **Tasks** (decision §11.12), grouped "Needs follow-up"
  / "Confirmed" / "Scheduled".
- Types → `frontend/src/lib/types.ts`. Components → `voice-reminder.tsx`,
  `patient-consent.tsx`.

---

## 9. Pure helpers (`backend/app/voice/`)

Tested in isolation, called by the §6 / §8 endpoints. No I/O, no Commander.

```python
@dataclass(frozen=True)
class ReminderVariables:
    clinic_name: str; patient_name: str; appointment_date: str; appointment_time: str
    def as_variable_values(self) -> dict: ...   # exactly the 4 keys

@dataclass(frozen=True)
class VoiceConsent:
    granted: bool; event_id: str | None; source: str | None; recorded_at: str | None

@dataclass(frozen=True)
class ConsentDecision:
    can_call: bool; reason: str   # "" | "no_consent" | "consent_revoked" | "no_phone" | "bad_phone_format"

@dataclass(frozen=True)
class ReminderOutcome:
    status: str; outcome: str | None; payload: dict   # payload NEVER contains transcript / recording

def current_voice_consent(consent_rows: list[dict]) -> VoiceConsent: ...
def consent_gate(phone: str | None, consent: VoiceConsent) -> ConsentDecision: ...
def resolve_variables(appointment: dict, organization: dict, *, now=None) -> ReminderVariables: ...   # raises on bad IANA tz
def classify_outcome(vapi_message: dict) -> ReminderOutcome: ...
```

### 9.1 `current_voice_consent()`
Latest `voice`-channel row by `recorded_at`; `granted` iff it exists and
`state == "granted"`; no rows → `VoiceConsent(False, None, None, None)`.

### 9.2 `consent_gate()` — fail-closed
`not granted and event_id is None → (False,"no_consent")` · `not granted →
(False,"consent_revoked")` · `not phone → (False,"no_phone")` · `not
E164_RE.match → (False,"bad_phone_format")` · else `(True,"")`. No branch returns
`True` from missing data.

### 9.3 `resolve_variables()`
`clinic_name = organization["name"].strip()` · `patient_name =
appointment["patient_name"].split()[0]` (first name — **decision §11.10**) ·
render `scheduled_at` in `organization["timezone"]` (zoneinfo; **raises**
`ValueError` on an unknown zone) → `appointment_date = "%A, %B %-d"` ·
`appointment_time = "%-I:%M %p"`. Deterministic.

### 9.4 `classify_outcome()`
`raw = structured end_reminder_call "outcome"` (from `message.toolCalls` /
`message.analysis.structuredData` — path pinned at build) · `raw in
OUTCOME_TO_STATUS` → that status · `raw is None` → `no_answer` (any ended reason)
· a "could not connect" shape → `call_failed`. **Never** `confirmed` unless
`raw == "confirmed"` literally. Never reads `transcript` / `recordingUrl`.

---

## 10. Test plan

**No `ANTHROPIC_API_KEY` anywhere.** **No real Vapi call** except the opt-in
`--live-vapi` test. **`commander_test.py` / `eligibility_commander_test.py` /
`prior_auth_commander_test.py` / `appeals_commander_test.py` are not modified and
must still pass — proving `commander.py` has no diff.**

### `tests/voice_helpers_test.py` (pure)
- `current_voice_consent()` over `[]`, `[granted]`, `[granted,revoked]`,
  `[revoked,granted]`, `[granted,revoked,granted]`, SMS-only → correct current
  state + `event_id`; deterministic.
- `consent_gate()` fail-closed grid `{never/granted/revoked} × {valid/local/empty/None phone}`
  → `can_call` iff granted + E.164; `reason` distinguishes revoked vs never;
  named `test_revoked_consent_never_permits_a_call`.
- `resolve_variables()` over appts × timezones incl. a DST boundary; invalid IANA
  → raises; first-name extraction; deterministic.
- `classify_outcome()` over fixture Vapi shapes: each outcome maps; no structured
  outcome + voicemail → `no_answer`; unknown ended reason → `no_answer` (never
  `confirmed`); a payload containing `transcript` / `recordingUrl` → returned
  `payload` has neither. Named
  `test_classify_never_confirms_without_the_literal_string`,
  `test_classify_never_surfaces_transcript_or_recording`.

### `tests/voice_automation_api_test.py` (local stack, no key)
- **auth**: every `/api/automation/*` route → `401` without the token, `401` with
  a wrong token, `200`/`4xx` with the right one; the token is rejected on a
  sample `/api/claims` route.
- **`/due`**: returns only rows that are `pending` + past due + `authorized_by`
  set + consent granted + phone valid + renderable; each returned row flips to
  `dispatching` with `dispatched_at`; a second immediate poll does **not**
  re-return it.
- **`/due` skip paths**: a pending row with revoked consent → response omits it,
  row is `skipped_no_consent`, exactly one escalation
  (`voice_reminder_no_consent_needs_human`), org-scoped; same for bad phone and
  bad timezone.
- **`/due` lost-dispatch sweep**: a `dispatching` row older than the lease →
  `error` + one escalation (`voice_reminder_dispatch_lost`), **not** re-returned,
  **not** re-dialed.
- **`mark-calling`**: `409` unless `dispatching`; happy path → `calling` +
  `vapi_call_id`; **revoke consent between `/due` and `mark-calling`** → `409
  {reason:"consent_revoked"}` + row `skipped_no_consent` + one escalation;
  idempotent on the same `vapi_call_id`.
- **`/outcome`**: unknown `vapi_call_id` → `200 {ignored:true}`, nothing written;
  `confirmed` → row `confirmed`, **no** escalation; each of
  `reschedule_needed` / `wrong_person` / `out_of_scope` → mapped status + exactly
  one escalation with the right `reason_code`, `voice_reminder_id` +
  `appointment_id` set; no structured outcome + voicemail → `no_answer` + one
  escalation; a `message` carrying `transcript` + `recordingUrl` → stored row has
  neither; **W3**: a `message` carrying a foreign `orgId` against a Clinic A row
  → write lands on Clinic A; a replayed identical callback → idempotent, no
  second escalation.
- **`dispatch-failed`**: `409` unless `dispatching`; → `call_failed` + one
  escalation; no retry state anywhere.

### `tests/voice_reminder_e2e_test.py` (local stack; Vapi call-create stubbed)
| # | flow | asserted |
|---|------|----------|
| 1 | capture consent → enroll → `/due` → stub call → `mark-calling` → `/outcome` confirmed | row `confirmed`; **no** escalation; `appointments` + `claims` untouched; all rows org-scoped |
| 2 | as 1 but `/outcome` = `reschedule_needed` | row `reschedule_requested`; one escalation |
| 3 | enroll → **revoke consent** → `/due` | row `skipped_no_consent`; one escalation; stub call count 0 |
| 4 | `/due` → stub call → revoke → `mark-calling` | `409 consent_revoked`; row `skipped_no_consent`; one escalation |
| 5 | stub call-create returns 502 → `dispatch-failed` | row `call_failed`; one escalation; no retry |
| 6 | second org repeats scenario 1 | Clinic A sees no new rows; the outcome for B's call never reads an A row |
| 7 | across 1–6 | no row anywhere holds a transcript or recording URL; every escalation/activity row carries the right `organization_id` |

### `tests/voice_reminder_live_test.py` (opt-in `--live-vapi`; needs a Vapi key + `VAPI_LIVE_TEST_NUMBER`)
Off by default — rings a real phone. Places one real call, asserts a
`vapi_call_id` and that the call's recording flag reads `false`.

### `tests/agent_isolation_test.py` (extended)
The automation token reaches only `/api/automation/*`; every write in a full
reminder run over Clinic A is A-scoped; the outcome path stays A-scoped when the
Vapi body names B.

### `tests/commander_test.py` &c. — **run unchanged, must stay green** (the Phase 6 proof that Commander is untouched).

### `scripts/seed_voice_reminders.py`
Sets `organizations.timezone`. Builds `patient_contacts` + `patient_consents`
across a spectrum (most granted, one revoked, one none, one malformed number).
Enrolls the consented ones with `scheduled_call_at` in the past. Runs the flow
with Vapi **stubbed** unless `--live-vapi`: calls `/due`, fakes call ids, calls
`mark-calling`, then posts synthetic `end-of-call-report` bodies to `/outcome`
for a realistic mix (mostly `confirmed`, a couple `reschedule_needed`, one
`no_answer`, one revoke-after-enroll → `skipped_no_consent`). Prints the
distribution.

### `scripts/run_voice_reminder_proof.sh`
Pure helper tests → the automation + e2e stack tests against a fresh
`supabase db reset` → the four Commander suites (unchanged, green) → the seed.

---

## 11. Decisions

### Resolved at review

1. **Commander family vs. n8n.** → **RESOLVED: n8n workflow, entirely outside the
   Commander.** No `_decide_voice_reminder`, no orchestrator entry, no
   `commander.py` diff. The four Commander suites stay byte-for-byte. The
   human-approval + TCPA guarantees move to the FastAPI endpoints.
2. **HITL model + retry.** → **RESOLVED: per-appointment enrollment is the
   recorded approval** (`authorized_by`); the call fires when `/due` next
   surfaces it. **No automatic retry** anywhere — a missed / failed / non-confirmed
   / lost-dispatch reminder is always a human decision (escalation). `NO_AUTORETRY`
   constant; every non-happy path creates an `escalations` row.
3. **Consent storage.** → **RESOLVED: a dedicated append-only `patient_contacts` +
   `patient_consents` ledger** — durable (survives any appointment), auditable
   (full history), revocable (a new `revoked` row). Re-checked against the **live**
   ledger at enrollment, `/due`, and `mark-calling`. The `voice_reminders` row
   snapshots the resolved state for the audit only.
4. **Call recording.** → **RESOLVED: disabled entirely** — off on the assistant,
   `recordingEnabled: false` on every n8n call-create, and the `/outcome`
   endpoint stores only the structured result + metadata (no transcript, no
   recording URL).
5. **`organizations.timezone`.** → **RESOLVED: added now**, default
   `America/New_York` (explicit placeholder). `resolve_variables()` raises on a
   bad zone → `error` + escalation, never a mis-timed call.

### Still open — recommendations stand

6. **n8n's access scope.** → recommend **one machine token
   (`N8N_SERVICE_TOKEN`) authorized for only `/api/automation/voice-reminders/*`**,
   no Supabase credential. The `/due` endpoint spans clinics (it is the dispatch
   queue) and returns patient phone numbers — that is the whole blast radius.
   Tighter alternative: per-clinic tokens + a clinic opt-in flag, so a leaked
   token exposes one clinic — heavier, defer unless review wants it now.
7. **Vapi webhook verification.** → recommend the **n8n Webhook node checks
   `X-Vapi-Secret`** against a credential (constant-time expression); add HMAC
   over the raw body if/when Vapi signs. Confirm the header name at build.
8. **Where the schedule lives.** → recommend the **n8n Schedule Trigger** (every
   ~5 min). No backend cron needed for the happy path; a small backend reconcile
   sweep (§7.3, §11.11) covers n8n/Vapi outages.
9. **Audit columns.** → recommend **add `voice_reminder_id` to `activity_log` +
   `escalations`, and `patient_contact_id` to `activity_log`**.
10. **`{{patient_name}}` — first name only.** → recommend **yes**
    (`patient_name.split()[0]`). Confirm.
11. **Stale / lost work handling.** → recommend: (a) a `/due` staleness cutoff —
    a reminder more than N hours past `scheduled_call_at` becomes `error` +
    escalation, not a very-late call; (b) a backend sweep for `calling` rows with
    no outcome after ~1h → `error` + escalation (a human checks Vapi). Confirm N
    and the sweep's home (cron vs. a `/due` side-effect).
12. **UI placement.** → recommend **fold the queue into Tasks**, no new nav item.
13. **Agent number 17.** 11 → appeals; 12 → escalation; 13–16 held for
    Commander-side work; **17 = the voice-reminder n8n workflow** (not a Commander
    agent — the number is just a label for the docs/roster). Confirm.
14. **Contact ↔ appointment matching.** Enroll matches `patient_contact` to the
    appointment by `(patient_name, patient_dob)` soft key; name-only when the
    appointment has no dob. Confirm that is acceptable.
15. **First real external action — explicit sign-off.** Confirm the review is
    comfortable that: (a) disclosure + clinic identity are in the Vapi assistant;
    (b) `authorized_by` + a live fail-closed consent ledger + recording-off are
    the guardrails; (c) no transcript/recording is stored; (d) n8n holds only a
    narrow non-Supabase token; (e) the outcome path never trusts Vapi's payload
    for tenancy — before any code or n8n build.
16. **n8n hosting / operational ownership.** The configured n8n MCP endpoint
    (`n8n.cloud`) currently fails to connect in this environment. Confirm the
    target n8n instance (cloud vs. self-hosted), who operates it, how the two
    workflows are deployed (the repo holds the exported JSON), and how
    `N8N_SERVICE_TOKEN` / the Vapi credentials are injected there.

`00-commander.md` §2 and `architecture.md` §6 / §7 (a new note: n8n as a
single-workflow orchestration surface for Phase 6, with the narrow-token
tenant-safety rationale) to be updated once §6–§16 are resolved.
