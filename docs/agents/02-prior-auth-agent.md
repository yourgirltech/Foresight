# 02 — Prior Authorization Agent

_Spec. Written before implementation, per the Phase 3 plan. Cross-check this
document — and the §13 addition to [`00-commander.md`](00-commander.md) — before
any Phase 3 code is written._

_Status: **IMPLEMENTED** (2026-09-07). All six §13 decisions resolved as
recommended. Code: `backend/app/agents/prior_auth.py` (pure `determine` /
`draft_request` / `simulate_response`), `commander._decide_prior_auth` (A1–A11),
`orchestrator.handle_prior_auth`; migration
`supabase/migrations/20260906000003_prior_authorization.sql`; router
`backend/app/routers/prior_auth.py`; seed `scripts/seed_prior_auth.py`; UI
`frontend/src/pages/PriorAuth{,Detail}Page.tsx` + a card on
`AppointmentDetailPage`; tests
`tests/prior_auth_{determiner,commander,orchestrator}_test.py`,
`tests/e2e_prior_auth_test.py`, and a prior-auth slice in
`tests/agent_isolation_test.py`. Companion: [`../PHASE-3.md`](../PHASE-3.md)._

_One addition during implementation: `simulate_response` takes `is_resubmit` and
shifts the effective bucket down by `RESUBMIT_APPROVAL_BONUS` (18) so a resubmit
with attached documentation genuinely improves the odds (§7.6)._

---

## 1. What 02 is

The **Prior Authorization Agent** answers one question about a planned service:
_does this payer require prior authorization for this procedure, and — if a
request is submitted — is it approved?_

Prior authorization is the single largest **preventable** denial category: a
service that needed payer sign-off and did not get it is denied on the claim,
after the visit, when it is expensive to fix. 02 moves that check to **before the
service**, where a human still decides whether to pursue the authorization.

Like 01 (eligibility), Phase 3 does **not** integrate a real payer. It runs a
**deterministic simulation** over the payer configuration we already seed (§7) —
the same pattern as 06 (`rules.py`) and 01 (`eligibility.py`). The phase proves,
before any real integration is chosen:

1. the **Commander wiring** — a third trigger family, a third disjoint rule block
   (`A1–A11`), a third routed agent — works end to end;
2. the **care-safety requirement** (§2) is enforced *structurally*: prior
   authorization can gate an *elective* service (correctly), but it can **never**
   gate, delay, or precondition **emergency / urgent** care;
3. the **human-in-the-loop** gate from Phase 1 is reused unchanged — 02 drafts a
   request; **a human approves submitting it**; only then does anything leave the
   building.

02 is a specialist agent in the sense of `00-commander.md` §2: it does one job
and returns control. It has **two phases**, split exactly like claims splits
analysis (06/07/08) from execution (09/10):

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 02 | **prior-auth-agent** (`determine`) | deterministic simulation | no | appointment + payer + procedure → an auth **determination** + a drafted request packet, written to `prior_authorizations` |
| 02 | **prior-auth-agent** (`submit`) | deterministic simulated send | no | an **approved** drafted request → a simulated submission + a deterministic payer response |

`submit` is only ever reachable **after** a human-approval trigger (§6, A7) — the
same structural gate as claims R9 sitting behind R7/R8.

---

## 2. The non-negotiable: prior authorization never gates emergency care

> **For an elective / scheduled service**, prior authorization runs *ahead of
> time*. If the payer requires it, 02 drafts the request and a human decides
> whether to submit. Foresight **surfaces** the auth status on the appointment —
> it never sets a state that blocks the visit. The clinic's own scheduling
> system is the gate, not Foresight.
>
> **For an emergency / urgent service**, prior authorization **does not apply** —
> by law (EMTALA) and by every payer contract, emergency services are exempt from
> pre-service authorization. 02 records `emergency_exempt` and stops. It never
> drafts a request, never asks a human to approve one, never escalates, and never
> produces a state that implies waiting. Any post-stabilization retro-auth the
> payer wants is a *scheduled-style* follow-up with lead time — never a gate.

This is treated like the Phase 1 human-in-the-loop invariant and the Phase 2
EMTALA invariant: **enforced by the structure of the design**, not by developer
discipline. Eight concrete mechanisms, each independently verifiable:

| # | Mechanism | Where it lives |
|---|-----------|----------------|
| P1 | **Separate trigger.** `prior_auth_emergency` is a distinct trigger type from `prior_auth_requested`; they diverge on the first line of the rule block. No shared "determine" path they both funnel through. | §5, §6 (A1 vs A3) |
| P2 | **`prior_authorizations` is a sidecar table.** No care object has a `not null` FK to it. No care workflow has a state meaning "waiting on authorization". Nothing in the schema can put one there. | §4 |
| P3 | **`is_emergency` is snapshotted onto every `prior_authorizations` row** (plus `place_of_service`). The care-safety context travels *with the data*; any query or test can `select where is_emergency` and assert none of those rows ever produced a draft, a submission, or an escalation. | §4 |
| P4 | **The Commander's `next_status` is `None` for every prior-auth rule (A1–A11)** — identical to the eligibility invariant. The Commander never writes a status off a patient-access trigger. `prior_authorizations.status` is written only by 02 and the orchestrator's response handler; nothing on `appointments` is ever touched. Enforced by a hard `raise` in the orchestrator *and* a fuzz test. | §6, §9, §13-commander |
| P5 | **`determine()` returns `required = False` unconditionally when `is_emergency` or `place_of_service == 'emergency'`.** It is *structurally impossible* for an emergency service to yield an auth requirement, a drafted request, or a submission — the branch that would produce `required_draft` is unreachable in that case. | §7.3 |
| P6 | **No emergency-context rule routes to submission or to `12-escalation`.** A4 catches every emergency determination before A5 (`await_human`) or A6 can run; A7 (`submit`) and A10 (`→ 12`) both additionally guard `not context.is_emergency`. Every emergency outcome is `no_action` or a detached route to `02` for the determination itself. | §6 |
| P7 | **Emergency determination runs detached.** The orchestrator spawns it; nothing on a care path `await`s it; its exceptions are swallowed into a recorded `emergency_exempt` / `insufficient_info`; it chains no follow-on. A hung or throwing 02 cannot propagate near care. | §9 |
| P8 | **Emergency-flag ambiguity fails safe.** Missing/contradictory `is_emergency`, `place_of_service == 'emergency'`, or a PA row with no appointment → treated as emergency (the non-blocking, no-auth path). You cannot accidentally land an emergency in the drafting path. A *non-emergency* row with an unknown procedure does **not** silently become "no auth needed" — it becomes `insufficient_info` (§7.3, step 2), surfaced to staff. | §6 (A2, A11), §7.3 |

If someone later adds a real integration, these mechanisms are the checklist it
must not break — and the tests in §12 fail loudly if it does.

### 2.1 Prior auth *may* gate an elective service — and that is fine

The Phase 2 invariant was absolute: eligibility never gates *anything*. Phase 3
is narrower on purpose. It is medically and administratively correct for an
elective MRI to wait on authorization. The line Foresight holds is:

- **Foresight never *sets* a gating state.** There is no `appointments` column or
  status meaning "blocked on auth". The PA status is *information* on the
  appointment view (§10). What the clinic does with that information — reschedule,
  proceed at financial risk, expedite — is the clinic's call.
- **Emergency / urgent is exempt, structurally** (P5, P6). The elective-gating
  nuance never touches the emergency path because the emergency path never
  reaches a state where "wait" is meaningful.

---

## 3. Where 02 sits in the system

```
appointment needs a procedure ─► prior_auth_requested ─► 00 ──A3──► 02.determine  (ahead of time, awaited)
                                                                      │
                                                             prior_auth_determined
                                                                      │
                                              ┌───────────────────────┼───────────────────────┐
                                        not_required /          required_draft            (emergency)
                                        insufficient_info             │                        │
                                              │                 00 ──A5──► await_human    00 ──A4──► record, stop
                                        00 ──A6──► record, stop        │                  (emergency_exempt)
                                                                       ▼
                                                       human approves submitting  ─► prior_auth_submission_approved
                                                                       │
                                                                 00 ──A7──► 02.submit  (simulated send)
                                                                       │
                                                            prior_auth_response_received
                                                                       │
                                       ┌───────────────────────────────┼───────────────────────┐
                                   approved                        info_needed               denied
                                       │                               │                       │
                                 00 ──A9──► record (auth_approved)  00 ──A10──► 12          00 ──A10──► 12
                                                                   (attach clinicals,      (peer-to-peer /
                                                                    resubmit — new row)     appeal)

ER / urgent service ─► prior_auth_emergency ─► 00 ──A1──► 02.determine  (DETACHED — fire and forget)
   (care proceeds, unaffected)                              └► prior_auth_determined ─► 00 ──A4──► record, stop
                                                               (status = emergency_exempt; never a draft,
                                                                never await_human, never an escalation)
```

There is no arrow from any prior-auth box back into a care box.

---

## 4. Data model

New migration: `supabase/migrations/20260906000003_prior_authorization.sql` (date
finalised when built). Same rules as Phases 1–2: every table carries
`organization_id uuid not null references public.organizations(id)` and is run
through `select public.enable_tenant_isolation('public.<table>')`. No hardcoded
tenant id anywhere.

### 4.1 New enum

```sql
create type public.prior_auth_status as enum (
  'pending',            -- created; 02 has not run the determination yet
  'not_required',       -- determination: this payer + procedure needs no prior auth
  'emergency_exempt',   -- determination: emergency / urgent service — prior auth does not apply
  'insufficient_info',  -- not enough on the appointment to determine (missing procedure code / payer)
  'required_draft',     -- determination: auth IS required; a request packet is drafted, awaiting a human
  'submission_declined',-- a human reviewed the draft and chose not to submit it
  'submitting',         -- a human approved; 02.submit is running (simulated send)
  'submitted',          -- request sent to the (simulated) payer, awaiting their determination
  'auth_approved',      -- (simulated) payer approved — the service is authorized
  'info_needed',        -- (simulated) payer needs more clinical information — a human owns the resubmit
  'auth_denied'         -- (simulated) payer denied — a human owns the peer-to-peer / appeal
);
```

`emergency_exempt` and `not_required` are **separate** on purpose:
`emergency_exempt` means "we did not even ask, because the service is exempt";
`not_required` means "we checked and this payer + procedure does not need it".
Neither is an error; neither ever escalates.

### 4.2 `prior_authorizations`

```sql
create table public.prior_authorizations (
  id                    uuid primary key default gen_random_uuid(),
  organization_id       uuid not null references public.organizations (id) on delete cascade,
  appointment_id        uuid references public.appointments (id) on delete cascade,           -- nullable BY DESIGN (sidecar)
  previous_auth_id      uuid references public.prior_authorizations (id) on delete set null,  -- append-only resubmit chain
  patient_name          text not null,                    -- snapshot at determination time
  patient_member_id     text not null default '',         -- snapshot ('' if unknown)
  payer_id              uuid references public.payers (id) on delete restrict,                -- nullable
  payer_name            text,                             -- snapshot
  procedure_code        text not null default '',         -- CPT / HCPCS; '' if unknown
  procedure_description  text not null default '',
  place_of_service      text not null default 'office',   -- office | outpatient | inpatient | emergency
  is_emergency          boolean not null default false,   -- care-context snapshot — drives the safety invariant (P3)
  status                public.prior_auth_status not null default 'pending',
  determination_payload jsonb not null default '{}'::jsonb,  -- why auth is / isn't required (§7.4)
  request_payload       jsonb not null default '{}'::jsonb,   -- the drafted PA request packet (§7.5)
  response_payload      jsonb not null default '{}'::jsonb,   -- the (simulated) payer response (§7.6)
  authorization_number  text,                             -- set on auth_approved
  determined_at         timestamptz,                      -- set when 02.determine completes
  submitted_at          timestamptz,                      -- set when 02.submit completes
  resolved_at           timestamptz,                      -- set on any terminal status
  decided_by            uuid references auth.users (id) on delete set null,  -- the human who approved / declined submission
  created_at            timestamptz not null default now()
);

create index prior_authorizations_organization_id_idx on public.prior_authorizations (organization_id);
create index prior_authorizations_appointment_id_idx  on public.prior_authorizations (appointment_id, created_at);
create index prior_authorizations_previous_auth_id_idx on public.prior_authorizations (previous_auth_id);
create index prior_authorizations_status_idx          on public.prior_authorizations (organization_id, status);
create index prior_authorizations_emergency_idx       on public.prior_authorizations (organization_id, is_emergency);
```

- `appointment_id` nullable — mirrors `eligibility_checks.appointment_id` and
  `escalations.claim_id`. Phase 3 always links one; the schema does not require it.
- **A resubmit inserts a new row**, chained by `previous_auth_id` — append-only
  history, exactly like `eligibility_checks.previous_check_id`. The
  `info_needed → (clinicals attached) → auth_approved` progression is two rows.
- `is_emergency` and `place_of_service` are copied **at creation** and never
  updated. They are the authoritative fields the safety rules branch on.

### 4.3 `payers` — three new simulation knobs

```sql
alter table public.payers
  add column prior_auth_supported          boolean not null default true,
  add column prior_auth_required_default   boolean not null default false,
  add column prior_auth_approval_threshold integer not null default 80
    check (prior_auth_approval_threshold between 0 and 100);
```

The eligibility equivalent of `eligibility_verification_supported` /
`eligibility_active_threshold` for 01. Not a separate config table (Phase 2
decision #4, kept).

- `prior_auth_supported = false` models a payer with no electronic PA channel —
  the determination still runs, but `request_payload` is flagged
  `channel: "manual_fax"` and the drafted task tells the human to fax it. It does
  **not** change whether auth is required.
- `prior_auth_required_default` — this payer requires auth for the "elective /
  high-cost" procedure set (§7.2), on top of the always-auth set.
- `prior_auth_approval_threshold` — the percentage of submitted requests the
  simulation approves for this payer (§7.6).

### 4.4 Procedure sets — documented constants (not a table)

Kept in `backend/app/agents/prior_auth.py`, in lockstep with §7.2. Illustrative,
**not clinical guidance** — the point is a reproducible simulation, exactly like
06's severity weights.

```
ALWAYS_AUTH_PROCEDURES  = { "70553", "72148", "72141",  -- MRI brain / lumbar / cervical
                            "97110-EXT",                 -- extended PT course
                            "J3489", "J0178",            -- specialty injectables
                            "43239-SURG" }               -- (synthetic) elective surgical bundle
ELECTIVE_AUTH_PROCEDURES = { "29881", "29827",           -- knee / shoulder arthroscopy
                             "62323", "64483",           -- pain-management injections
                             "95810" }                   -- sleep study
SENTINEL_PROCEDURE_CODES = { "", "UNKNOWN", "TBD", "NA", "PENDING", "MISC" }
```

### 4.5 Audit trail — `activity_log` / `escalations` gain one nullable column

```sql
alter table public.activity_log add column prior_authorization_id
  uuid references public.prior_authorizations (id) on delete cascade;
alter table public.escalations  add column prior_authorization_id
  uuid references public.prior_authorizations (id) on delete cascade;

create index activity_log_prior_authorization_id_idx on public.activity_log (prior_authorization_id, created_at);
create index escalations_prior_authorization_id_idx  on public.escalations  (prior_authorization_id);
```

Phase 1 rows keep `claim_id`; Phase 2 rows use `appointment_id` /
`eligibility_check_id`; Phase 3 rows use `prior_authorization_id`. All nullable.
The `insert_activity` / `insert_escalation` helpers gain an optional
`prior_authorization_id` kwarg (mirrors the Phase 2 change).

### 4.6 Isolation + grants

```sql
select public.enable_tenant_isolation('public.prior_authorizations');

revoke all on public.prior_authorizations from anon, authenticated;
grant  select on public.prior_authorizations to authenticated;   -- UI reads directly, RLS-scoped
```

**No client write grants** (Phase 2 discipline, kept). Every write — the row
create, the determination, the human approve/decline, the submission — goes
through a backend endpoint (§10) or the service-role agent path, with
`organization_id` derived from the verified session or resolved from the
triggering row. `tests/agent_isolation_test.py` gains a prior-auth slice (§12).

---

## 5. Trigger taxonomy — additions to `00-commander.md` §5

| trigger `type` | emitted when | typical source |
|----------------|--------------|----------------|
| `prior_auth_requested` | a `prior_authorizations` row is created for a non-emergency appointment | seed, `POST /api/prior-authorizations` |
| `prior_auth_emergency` | a `prior_authorizations` row is created with `is_emergency = true` / `place_of_service = 'emergency'` | seed, `POST /api/prior-authorizations` (emergency), ER intake |
| `prior_auth_determined` | `02.determine` finished and wrote a terminal determination status (`not_required` / `emergency_exempt` / `insufficient_info` / `required_draft`) | orchestrator, after `02.determine` |
| `prior_auth_submission_approved` | a human approved submitting a drafted request | `POST /api/prior-authorizations/{id}/approve-submission` |
| `prior_auth_submission_declined` | a human declined submitting a drafted request | `POST /api/prior-authorizations/{id}/decline-submission` |
| `prior_auth_response_received` | `02.submit` produced a deterministic payer response (`approved` / `info_needed` / `denied`) | orchestrator, after `02.submit` |

These six form the **prior-auth trigger family**. `00-commander.md` §13 specifies
that `decide()` dispatches this family to `_decide_prior_auth` — disjoint from
both R1–R20 and E1–E7. A trigger belongs to exactly one family.

`payload` carries context for the orchestrator / `activity_log` only — the
Commander branches on `type` and on the PA row's `status` / `response_payload`,
never on `payload`. As in Phases 1–2.

---

## 6. The prior-auth rule block (A1–A11)

Lives in `00-commander.md` §13.6. Restated here in full for review. Evaluated
**top to bottom, first match wins**, exactly like R1–R20 and E1–E7.
`CommanderDecision` is the **unchanged** dataclass — `route_to` gains
`"02-prior-auth"`, `reason_code` gains the ten values in §6.3, and **`next_status`
is `None` for every row** (P4).

### 6.1 The state the Commander reads for a prior-auth trigger

Assembled by the orchestrator, handed in. Read-only, single-tenant.

```python
pa_state = {
  "appointment": {                      # None for a standalone PA with no appointment row
    "id": "...", "organization_id": "...", "scheduled_at": "...",
    "is_emergency": False, "patient_name": "...", "patient_member_id": "...",
  },
  "prior_authorization": {              # the row this trigger concerns; None only pre-create (never, in practice)
    "id": "...", "organization_id": "...", "appointment_id": "<uuid|None>",
    "is_emergency": False,              # AUTHORITATIVE snapshot (P3)
    "place_of_service": "office",
    "status": "pending",               # see §4.1
    "response_payload": {},             # {"response_status": "approved|info_needed|denied", ...} after 02.submit
  },
  "payer": { "id": "...", "name": "...", "prior_auth_supported": True,
             "prior_auth_required_default": False, "prior_auth_approval_threshold": 80 },  # or {}
  "context": { "is_emergency": False },  # resolved by the orchestrator; see §6.2
}
```

### 6.2 How `context.is_emergency` is resolved (fail-safe — P8)

Identical shape to eligibility §6.2. The orchestrator computes it:

```
trigger.type == "prior_auth_emergency"                              -> True
prior_authorization.is_emergency is True                             -> True
prior_authorization.place_of_service == "emergency"                  -> True
appointment is not None and appointment.is_emergency is True         -> True
appointment is None  (a PA with no appointment)                      -> True   (fail-safe)
context.is_emergency missing / None                                  -> True   (fail-safe)
otherwise                                                            -> False
```

Ambiguity always resolves toward **True** (the non-blocking, no-auth path). The
only way to get `False` is an unambiguous, appointment-backed, non-emergency,
non-`emergency`-place-of-service row.

### 6.3 The rule table

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **A1** | `trigger.type == "prior_auth_emergency"` | `route` | `02-prior-auth` | `prior_auth_emergency_determine` | `None` |
| **A2** | `trigger.type == "prior_auth_requested"` **and** `context.is_emergency` | `route` | `02-prior-auth` | `prior_auth_emergency_determine` | `None` |
| **A3** | `trigger.type == "prior_auth_requested"` | `route` | `02-prior-auth` | `prior_auth_determine` | `None` |
| **A4** | `trigger.type == "prior_auth_determined"` **and** `context.is_emergency` | `no_action` | — | `prior_auth_emergency_exempt_recorded` | `None` |
| **A5** | `trigger.type == "prior_auth_determined"` **and** `pa.status == "required_draft"` | `await_human` | — | `prior_auth_awaiting_submission_approval` | `None` |
| **A6** | `trigger.type == "prior_auth_determined"` | `no_action` | — | `prior_auth_determination_recorded` | `None` |
| **A7** | `trigger.type == "prior_auth_submission_approved"` **and** `pa.status == "required_draft"` **and not** `context.is_emergency` | `route` | `02-prior-auth` | `prior_auth_submit` | `None` |
| **A8** | `trigger.type == "prior_auth_submission_declined"` | `no_action` | — | `prior_auth_submission_declined_recorded` | `None` |
| **A9** | `trigger.type == "prior_auth_response_received"` **and** `pa.response_status == "approved"` | `no_action` | — | `prior_auth_approved_recorded` | `None` |
| **A10** | `trigger.type == "prior_auth_response_received"` **and** `pa.response_status in {"info_needed","denied"}` **and not** `context.is_emergency` | `route` | `12-escalation` | `prior_auth_needs_human` | `None` |
| **A11** | any prior-auth trigger, nothing above matched | `no_action` if `context.is_emergency` else `route` `12-escalation` | — / `12-escalation` | `prior_auth_emergency_exempt_recorded` / `prior_auth_unrecognized_state` | `None` |

Ordering rationale:

- **A1 first, then A2** — anything emergency-flagged goes to `02.determine`
  detached (the determination still *runs*, so the record exists, but nothing on
  a care path waits — §9). A2 resolves the contradiction "requested-as-scheduled
  but the row is emergency-flagged" to the safe path.
- **A4 before A5/A6** — *every* emergency determination is recorded and stopped.
  Because of P5, an emergency `02.determine` can only ever write
  `emergency_exempt` (or `insufficient_info` if identity is too thin) — never
  `required_draft` — so A5 could not match anyway; A4 makes it explicit and
  unreachable. No emergency path ever reaches `await_human`.
- **A5** — a scheduled determination that found auth *is* required. The drafted
  packet (`request_payload`) is ready; the Commander parks at `await_human`. A
  human reviews the draft on the prior-auth queue (§10) and approves or declines
  submitting it. **Foresight does not auto-submit** — mirrors claims R14.
- **A6** — a scheduled determination of `not_required` or `insufficient_info`.
  Recorded and surfaced to staff (`insufficient_info` → "add the procedure code";
  `not_required` → a green "no auth needed" note). Not escalated — like
  eligibility E5.
- **A7** — the **only** rule that routes to `02.submit`, behind three guards:
  the `prior_auth_submission_approved` trigger, `pa.status == "required_draft"`,
  and `not context.is_emergency`. Structurally the analogue of claims R9 behind
  R7/R8.
- **A8** — a human declined. Recorded as `submission_declined` (02 writes the
  status; the Commander only logs). Terminal, no escalation — the clinic chose
  not to pursue it.
- **A9** — the payer approved. Recorded; 02 writes `auth_approved` +
  `authorization_number`. The appointment view shows "Authorized".
- **A10** — the payer denied or needs more info. For a **scheduled** PA this is a
  real human task (peer-to-peer, appeal, or attach clinicals and resubmit — a new
  chained row), so it routes to 12. Guarded `not context.is_emergency`; by P5/P6
  an emergency PA can never have a submission and so can never reach A10, and the
  guard makes that explicit. `next_status` still `None` — 12 only logs.
- **A11** — fallthrough for a malformed prior-auth state (mirror of R20 / E7),
  split: an emergency-context fallthrough is *recorded, never escalated*; only a
  non-emergency fallthrough escalates.

### 6.4 The invariant, stated precisely

> For any prior-auth trigger:
> - **`decision.next_status is None`** — always. (A1–A11.)
>
> For any prior-auth trigger where `context.is_emergency` is true:
> - **`decision.action != "await_human"`** — always.
> - **`decision.route_to not in {"12-escalation"}`** — always. The decision is
>   `no_action`, or a `route` to `02-prior-auth` for the determination only.
> - the determination that results can only be `emergency_exempt` or
>   `insufficient_info` (P5); never `required_draft`, never a submission.

`00-commander.md` §13 and the fuzz test in §12 assert exactly this.

### 6.5 Worked traces

| # | trigger | is_emergency | pa.status / response | matches | outcome |
|---|---------|--------------|----------------------|---------|---------|
| 1 | `prior_auth_requested` | false | (n/a) | A3 | route → 02.determine, awaited, ahead of the visit |
| 2 | `prior_auth_determined` | false | `not_required` | A6 | record, stop — "no auth needed" on the appointment |
| 3 | `prior_auth_determined` | false | `insufficient_info` | A6 | record, stop — staff prompt: add the procedure code |
| 4 | `prior_auth_determined` | false | `required_draft` | A5 | `await_human` — draft on the queue for approval |
| 5 | `prior_auth_submission_approved` | false | `required_draft` | A7 | route → 02.submit (simulated send) |
| 6 | `prior_auth_submission_declined` | false | `required_draft` | A8 | record `submission_declined`, stop |
| 7 | `prior_auth_response_received` | false | `approved` | A9 | record `auth_approved` + auth number, stop |
| 8 | `prior_auth_response_received` | false | `info_needed` | A10 | route → 12 (attach clinicals, resubmit) |
| 9 | `prior_auth_response_received` | false | `denied` | A10 | route → 12 (peer-to-peer / appeal) |
| 10 | `prior_auth_emergency` | true | (n/a) | A1 | route → 02.determine **detached**; care proceeds |
| 11 | `prior_auth_determined` | true | `emergency_exempt` | A4 | `no_action`; recorded; **not** escalated, **no** draft |
| 12 | `prior_auth_determined` | true | `insufficient_info` | A4 | `no_action`; recorded; recheck when identity exists |
| 13 | `prior_auth_requested`, row `is_emergency = true` | true | (n/a) | A2 | route → 02.determine detached (contradiction → safe path) |
| 14 | `prior_auth_response_received`, no PA row in state | true (fail-safe) | — | A11 | `no_action`, `prior_auth_emergency_exempt_recorded` |
| 15 | `prior_auth_response_received`, no PA row in state | false | — | A11 | route → 12, `prior_auth_unrecognized_state` |

---

## 7. The simulation (02's core) — deterministic and documented

Two pure functions. No LLM, no I/O, no call-time randomness. Same inputs → same
output every time. This section is the contract; `backend/app/agents/prior_auth.py`
is the code — kept in lockstep, exactly like 06 / 01.

### 7.1 Inputs

```
encounter : { procedure_code, procedure_description, place_of_service, is_emergency }
payer     : a payers row, or None
patient   : { name, member_id }   -- for the drafted packet + the response bucket
```

### 7.2 Procedure sets

Per §4.4. `ALWAYS_AUTH_PROCEDURES` always require auth (any payer).
`ELECTIVE_AUTH_PROCEDURES` require auth **only** when
`payer.prior_auth_required_default` is true. Everything else needs no auth.

### 7.3 `determine(...)` — the decision (evaluated top to bottom)

```
1. is_emergency or place_of_service == "emergency"
       -> emergency_exempt   (required = False; reason: "emergency/urgent service — prior auth does not apply")   [P5]
2. procedure_key(procedure_code) in SENTINEL_PROCEDURE_CODES  or  len < 3
       -> insufficient_info  (required = None; recheck_recommended = True)
3. payer is None or not payer.id
       -> insufficient_info  (required = None; "no payer on record to check auth rules against")
4. procedure_code in ALWAYS_AUTH_PROCEDURES
       -> required_draft     (required = True; channel = "electronic" or "manual_fax" per payer.prior_auth_supported)
5. payer.prior_auth_required_default and procedure_code in ELECTIVE_AUTH_PROCEDURES
       -> required_draft     (required = True; channel as above)
6. otherwise
       -> not_required       (required = False; "this payer + procedure does not require prior authorization")
```

Step 1 is what makes P5 structural: an emergency encounter can never reach step 4
or 5, so it can never produce `required_draft`, so there is nothing to submit and
nothing to await.

### 7.4 `determination_payload`

```json
{
  "simulated": true,
  "basis": "deterministic-sim-v1",
  "payer": "Cascade Medicaid",
  "procedure_code": "72148",
  "required": true,
  "determination": "required_draft",
  "matched_rule": "ALWAYS_AUTH_PROCEDURES",
  "channel": "electronic",
  "reason": "72148 (MRI lumbar spine) always requires prior authorization",
  "recheck_recommended": false,
  "generated_at": "2026-09-06T12:00:00Z"
}
```

### 7.5 `draft_request(...)` → `request_payload` — the drafted packet

Deterministic template from the appointment + procedure + a seeded synthetic
clinical justification (a fixed sentence per procedure code — this is a
simulation, not a clinical narrative generator; noted in §13 as a real
integration point). Shape:

```json
{
  "simulated": true,
  "channel": "electronic",
  "payer_name": "Cascade Medicaid",
  "member_id": "M-4471820",
  "procedure_code": "72148",
  "procedure_description": "MRI lumbar spine without contrast",
  "place_of_service": "outpatient",
  "diagnosis_hint": "M54.5",
  "clinical_justification": "Conservative therapy 6+ weeks without improvement; imaging indicated per payer policy.",
  "requested_units": 1,
  "drafted_at": "2026-09-06T12:00:00Z"
}
```

`draft_request` is only called when `determine` returned `required_draft`.

### 7.6 `simulate_response(...)` → `response_payload` — the payer's answer

Only computed by the orchestrator **after** `02.submit` runs (i.e. after a human
approved). Deterministic bucket, same construction as the eligibility bucket:

```
key    = f"{payer.id}|{member_key}|{procedure_key}"
raw    = int(sha256(key).hexdigest()[:8], 16) % 100          # 0..99, stable forever

# a resubmit carries added clinical documentation -> better odds
bucket = max(0, raw - 18)  if this row has a previous_auth_id  else raw

bucket <  payer.prior_auth_approval_threshold                 -> approved
bucket <  payer.prior_auth_approval_threshold + 12            -> info_needed
otherwise                                                     -> denied
```

The `-18` resubmit shift (`RESUBMIT_APPROVAL_BONUS`) is what makes the
`info_needed → (attach clinicals, resubmit) → auth_approved` flow real: the same
member + procedure + payer, resubmitted, moves down roughly one-and-a-half bands.
`response_payload` records both `raw_bucket` and the effective `bucket`, plus
`resubmit: true|false`.

```json
{
  "simulated": true,
  "response_status": "approved",
  "authorization_number": "AUTH-2026-0731-72148",   // deterministic: f"AUTH-{yr}-{bucket:04d}-{procedure_code}"
  "bucket": 31,
  "threshold": 80,
  "reason": "request meets payer medical-necessity criteria (simulated)",
  "responded_at": "2026-09-06T12:00:03Z"
}
```

### 7.7 Seeded payer knobs → the distribution

`scripts/seed_prior_auth.py` sets these on the existing seed payers, chosen for a
realistic spread:

| payer | `prior_auth_supported` | `required_default` | `approval_threshold` | simulated meaning |
|-------|------------------------|--------------------|----------------------|-------------------|
| Meridian Health Plan | true | false | 88 | commercial; auth only for the always-auth set; approves most |
| BlueRidge PPO | true | true | 72 | PPO; auth for elective set too; middling approval |
| Cascade Medicaid | true | true | 60 | Medicaid; broad auth requirements; more denials / info requests |
| Summit Commercial | **false** | true | 78 | no electronic PA channel → drafted as `manual_fax` |

Procedure codes for the synthetic appointments come from a **seeded** RNG
(`random.Random(f"{org_id}:pa")`), so the whole
`required_draft` / `not_required` / `approved` / `denied` / `info_needed`
distribution is identical on every re-run. The seeder prints it, like
`seed_claims.py` and `seed_eligibility.py`.

---

## 8. Non-error outcomes are first-class

`insufficient_info`, `not_required`, `emergency_exempt`, and `submission_declined`
are all **normal terminal states**, never alarms:

- **`emergency_exempt`** — the expected outcome for any ER / urgent encounter.
  Recorded (A4) and shown on the appointment as an informational note.
- **`not_required`** — recorded (A6); a green "no prior auth needed" note.
- **`insufficient_info`** — recorded (A6 / A4); staff prompt to add the procedure
  code, then re-run via the endpoint (§10). Carries `recheck_recommended: true`.
- **`submission_declined`** — the clinic reviewed a required auth and chose not
  to pursue it (e.g. patient rescheduling, or proceeding self-pay). Terminal, no
  escalation.

Only `auth_denied` and `info_needed` (scheduled) route to a human via 12 — and
those are genuine work items, not failures of the system.

---

## 9. The orchestrator — `handle_prior_auth`

New entry point in `backend/app/agents/orchestrator.py`, alongside `handle` and
`handle_eligibility`. Same shape, same `MAX_INVOCATIONS` loop cap, same
detached-task machinery (`_spawn` / `_detached_tasks` / `drain_detached` — reused,
not duplicated).

```python
async def handle_prior_auth(pa_id: str, trigger: dict, *, _depth: int = 0) -> CommanderDecision:
    # 1. load the PA row -> resolve organization_id ONCE from it
    # 2. load appointment + payer -> assemble pa_state (§6.1); resolve context.is_emergency (§6.2)
    # 3. decision = commander.decide(pa_state, trigger)
    # 4. insert_activity(actor="00-commander", action=decision.reason_code,
    #                    prior_authorization_id=pa_id, appointment_id=..., details={...})
    # 5. HARD ENFORCE (raise, not assert):
    #      decision.next_status is None
    #      not (context.is_emergency and decision.route_to == "12-escalation")
    #      not (context.is_emergency and decision.action == "await_human")
    # 6. dispatch:
```

| decision | dispatch |
|----------|----------|
| `route` → `02-prior-auth`, **emergency** (A1/A2) | **spawn detached** — `_spawn(_run_prior_auth_detached(...))`; return the decision immediately. Nothing awaits it. On completion it re-enters `handle_prior_auth` with `prior_auth_determined`, hits A4, stops. |
| `route` → `02-prior-auth`, **scheduled, `prior_auth_determine`** (A3) | `await _run_prior_auth_determine(...)`; re-invoke `handle_prior_auth` with the `prior_auth_determined` follow-on. |
| `route` → `02-prior-auth`, **`prior_auth_submit`** (A7) | `await _run_prior_auth_submit(...)` — runs `02.submit`, writes `submitting`→`submitted`, computes `simulate_response`, writes `response_payload` + the resolved status; re-invoke with `prior_auth_response_received`. |
| `await_human` (A5) | return the decision. The PA row is already `required_draft`; a human acts via the endpoint. |
| `route` → `12-escalation` (A10 / A11-non-emergency) | `await escalation.escalate(org_id, claim_pk=None, prior_authorization_id=pa_id, appointment_id=..., reason_code=..., context={...})`; return. |
| `no_action` (A4 / A6 / A8 / A9 / A11-emergency) | return. |

### 9.1 `_run_prior_auth_determine`

1. Run pure `determine(encounter, payer)` (§7.3).
2. If `required_draft`, run `draft_request(...)` (§7.5).
3. `update_prior_authorization(org_id, pa_id, {status, determination_payload,
   request_payload?, determined_at: now})`.
4. `insert_activity(actor="02-prior-auth", action="determined",
   prior_authorization_id=pa_id, details={status, matched_rule})`.
5. Return `{"type": "prior_auth_determined"}`.

Any exception in the pure core is caught and written as `insufficient_info` with
an error note (never re-raised) — a scheduled caller then hits A6, a detached
emergency caller hits A4. Mirrors `_run_eligibility_agent`.

### 9.2 `_run_prior_auth_submit`

Only reachable via A7 (post-approval). 

1. `update_prior_authorization(..., {status: "submitting"})`.
2. Run `02.submit` — a simulated send; `insert_activity(actor="02-prior-auth:submit",
   action="submitted", ...)`; `update_prior_authorization(..., {status: "submitted",
   submitted_at: now})`.
3. Compute `simulate_response(payer, patient, procedure)` (§7.6).
4. `update_prior_authorization(..., {status: <approved|info_needed|denied>,
   response_payload, authorization_number?, resolved_at: now})`.
5. Return `{"type": "prior_auth_response_received"}`.

### 9.3 Detached execution for emergencies (P7)

Identical mechanics to eligibility §9.2: `_spawn(...)`, never awaited; every
exception becomes a written status + an `activity_log` error entry, never
re-raised; the follow-on trigger re-enters `handle_prior_auth`, matches A4, stops
at `no_action`. From the caller's point of view `prior_auth_emergency` returns a
`CommanderDecision` and control essentially immediately.

### 9.4 Tenancy

Identical to Phases 1–2: `organization_id` resolved once from the PA row,
threaded explicitly into every `db` call, never from a request/env/constant. New
`db` helpers — `get_prior_authorization` (by pk, the `get_claim` analogue),
`insert_prior_authorization`, `update_prior_authorization`,
`list_prior_authorizations` — each take `org_id` and filter on it (except the
by-pk lookup used once to resolve the org). `tests/agent_isolation_test.py` gains
prior-auth cases (§12).

---

## 10. Backend + UI

### 10.1 Endpoints (`backend/app/routers/prior_auth.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/prior-authorizations` | PA rows in the caller's clinic (RLS-scoped); supports `?status=` filter for the work queue |
| `GET` | `/api/prior-authorizations/{id}` | one PA + its resubmit chain + related activity + the linked appointment |
| `POST` | `/api/prior-authorizations` | create a PA for an appointment; body `{appointment_id?, patient_name, patient_member_id?, payer_id?, procedure_code, procedure_description?, place_of_service?, is_emergency?}`. Emits `prior_auth_requested` or `prior_auth_emergency`. |
| `POST` | `/api/prior-authorizations/{id}/approve-submission` | a human approves submitting the drafted request. 404 unless the row is visible to the caller and `status == "required_draft"`. Emits `prior_auth_submission_approved` with `payload.user_id`. |
| `POST` | `/api/prior-authorizations/{id}/decline-submission` | a human declines. Emits `prior_auth_submission_declined`. |
| `POST` | `/api/prior-authorizations/{id}/resubmit` | append a new chained PA row (optionally with a corrected procedure code / added clinicals) and re-run — the `info_needed` follow-up flow. |

Reads use the caller's JWT through PostgREST (RLS). Writes verify the row is
visible to the caller first, derive `organization_id` from the session, then hand
to the orchestrator. An `is_emergency` from the request body can only make the
flow *more* non-blocking — it can never force a draft or a submission.

### 10.2 UI

- **New nav item "Prior Auth"** (`/app/prior-auth`) with a badge — the count of
  rows needing a human (`required_draft` + `auth_denied` + `info_needed`).
  Consistent with the Tasks badge. (The `/app/insurance` stub stays as-is for a
  later payer-directory feature.)
- **Work queue** (`/app/prior-auth`): a table grouped by what's needed —
  "Awaiting your approval" (`required_draft`, with a **Review draft** action that
  opens the packet and an Approve / Decline pair), "Payer responded — action
  needed" (`auth_denied` / `info_needed`), and a collapsed "Resolved" section.
- **Prior-auth detail** (`/app/prior-auth/:id`): the drafted `request_payload`
  rendered readably, the `determination_payload`, the `response_payload` when
  present, the full resubmit chain, and the related `activity_log` slice.
- **On the appointment detail page** (`AppointmentDetailPage`): a **Prior
  Authorization card** next to the existing eligibility card — status badge, and
  for `emergency_exempt` an explicit **"Not required — emergency exemption"**
  line so the non-gating principle is visible in the product.
- `PriorAuthBadge` tones: `pending` slate · `not_required` / `emergency_exempt`
  slate-green (informational) · `required_draft` amber ("needs approval") ·
  `submitting` / `submitted` blue · `auth_approved` emerald ·
  `info_needed` violet · `auth_denied` red · `submission_declined` slate-muted.
- **Dashboard tie-in**: the existing "N claims may be denied — missing prior
  authorization" insight is re-pointed at `/app/prior-auth` and its count driven
  by the real `required_draft` + `auth_denied` totals from a new field on
  `GET /api/dashboard` (`prior_auth: {needs_action, authorized, denied}`).

Types go in `frontend/src/lib/types.ts` (`PriorAuthorization`, `PriorAuthStatus`,
`PriorAuthDetail`).

---

## 11. Interfaces

```python
# backend/app/agents/prior_auth.py

from dataclasses import dataclass

@dataclass(frozen=True)
class Determination:
    status: str            # prior_auth_status value: emergency_exempt|not_required|insufficient_info|required_draft
    required: bool | None
    determination_payload: dict

@dataclass(frozen=True)
class PayerResponse:
    response_status: str   # approved | info_needed | denied
    response_payload: dict
    authorization_number: str | None

def determine(encounter: dict, payer: dict | None, *, now=None) -> Determination: ...
def draft_request(encounter: dict, payer: dict | None, patient: dict, *, now=None) -> dict: ...      # request_payload
def simulate_response(encounter: dict, payer: dict, patient: dict, *, now=None) -> PayerResponse: ...
```

`00-commander.md`:

```python
# route_to gains "02-prior-auth"; CommanderDecision is otherwise UNCHANGED.
PRIOR_AUTH_TRIGGERS = {
    "prior_auth_requested", "prior_auth_emergency", "prior_auth_determined",
    "prior_auth_submission_approved", "prior_auth_submission_declined",
    "prior_auth_response_received",
}
# decide() dispatches to _decide_prior_auth() when trigger.type in PRIOR_AUTH_TRIGGERS,
# after the ELIGIBILITY_TRIGGERS check and before the R1-R20 claims table.
# The three tables never interleave.
```

`reason_code` closed-set additions: `prior_auth_emergency_determine`,
`prior_auth_determine`, `prior_auth_emergency_exempt_recorded`,
`prior_auth_awaiting_submission_approval`, `prior_auth_determination_recorded`,
`prior_auth_submit`, `prior_auth_submission_declined_recorded`,
`prior_auth_approved_recorded`, `prior_auth_needs_human`,
`prior_auth_unrecognized_state`.

---

## 12. Test plan

### `tests/prior_auth_determiner_test.py` (deterministic, no stack, no key)

- `determine`: emergency / `place_of_service=='emergency'` → `emergency_exempt`
  **for every payer + procedure combination** (the P5 proof); sentinel / short
  procedure code → `insufficient_info`; `None` payer → `insufficient_info`;
  `ALWAYS_AUTH_PROCEDURES` → `required_draft` regardless of payer;
  `ELECTIVE_AUTH_PROCEDURES` → `required_draft` iff `prior_auth_required_default`;
  everything else → `not_required`.
- `draft_request`: deterministic (same inputs → byte-identical payload bar
  `drafted_at`); `channel == "manual_fax"` iff `not prior_auth_supported`.
- `simulate_response`: stable bucket across runs; the three bands
  (`approved` / `info_needed` / `denied`) partition `0..99` exactly at
  `threshold` and `threshold + 12`.

### `tests/prior_auth_commander_test.py` (deterministic, no stack, no key)

- One case per rule **A1–A11** (table-driven, `commander_test.py` style).
- **R1–R20 and E1–E7 untouched**: import and re-run representative claims and
  eligibility cases; assert `EXECUTABLE_ACTIONS` / `MANUAL_ACTIONS` /
  `ELIGIBILITY_TRIGGERS` unchanged.
- **The emergency-care-safety fuzz** — the cross-check that matters. For every
  combination of
  `trigger ∈ PRIOR_AUTH_TRIGGERS` ×
  `pa.status ∈ {all 11}` ×
  `pa.response_status ∈ {none, approved, info_needed, denied}` ×
  `is_emergency ∈ {True, False}` ×
  `place_of_service ∈ {office, outpatient, inpatient, emergency}` ×
  `appointment ∈ {present, None}` — call `decide` twice and assert:
  - determinism (both calls identical);
  - **`decision.next_status is None`** — every case;
  - resolved `context.is_emergency` ⟹ **`decision.action != "await_human"`**,
    **`decision.route_to != "12-escalation"`**, and
    `decision.route_to in {None, "02-prior-auth"}`;
  - no `decision.next_status` is ever a `claim_status` or `eligibility_status`
    value.
- A named `test_emergency_prior_auth_is_never_gating` enumerating every
  emergency decision and asserting the three prohibitions explicitly, with a
  comment pointing back to §2 / P4 / P5 / P6.

### `tests/prior_auth_orchestrator_test.py` (local stack, no key)

- Monkeypatch `determine` to **raise**; call `handle_prior_auth(..., emergency)`;
  assert it returns cleanly, the row ends `insufficient_info` (or
  `emergency_exempt`), `activity_log` has the error entry, and **no
  `escalations` row exists**.
- Same with a scheduled `prior_auth_response_received` / `denied`: assert an
  `escalations` row **does** appear (A10) — the contrast case.
- **The human-approval gate**: drive a scheduled PA to `required_draft`; assert
  that calling the submit path *without* a `prior_auth_submission_approved`
  trigger never runs `02.submit` (no `submitting` / `submitted` status, no
  `response_payload`). Only the approval trigger via A7 gets there.
- Assert the emergency dispatch never `await`s `02.determine` on the calling
  path (structural: the call returns before a patched slow `determine` resolves).

### `tests/e2e_prior_auth_test.py` (local stack — real end to end, prints PASS/FAIL)

Same pattern and helpers as `e2e_eligibility_test.py`. Scenarios:

| # | setup | asserted |
|---|-------|----------|
| 1 | scheduled appt, Meridian, procedure `72148` (always-auth) → `prior_auth_requested` | 02 ran; `required_draft`; A5 `await_human`; `request_payload` present; appointment unchanged |
| 2 | scenario 1, then `POST /approve-submission` | A7 → `02.submit`; `simulate_response` bucket lands `approved`; row `auth_approved` + `authorization_number`; activity chain complete; all rows org-scoped |
| 3 | scheduled appt, Cascade Medicaid, procedure tuned to bucket into `denied` → approve → response | A10 → one `escalations` row (`prior_auth_needs_human`), org-scoped; row `auth_denied`; appointment unchanged |
| 4 | scenario 3 tuned to `info_needed`, then `POST /resubmit` with corrected clinicals | a **second** chained PA row; re-run; chain reads `info_needed → auth_approved` |
| 5 | scheduled appt, procedure not in either set → `prior_auth_requested` | 02 → `not_required` → A6 `no_action`; **no** `escalations`; no draft |
| 6 | **emergency** registration, `place_of_service = 'emergency'`, always-auth procedure → `prior_auth_emergency` | A1 detached; 02 → `emergency_exempt` → `prior_auth_determined` → A4 `no_action`; **no** `escalations`, **no** `required_draft`, **no** `await_human`; every Commander `next_status` was `None` |
| 7 | scheduled appt, blank procedure code → `prior_auth_requested` | 02 → `insufficient_info` → A6; `recheck_recommended: true`; then `POST /resubmit` with a code → `required_draft` |
| 8 | second org, re-run scenario 1 | first org sees no new rows; nothing leaked across the tenant boundary |
| 9 | collected from scenario 6 | every `CommanderDecision` in an emergency flow had `next_status is None`, `action != "await_human"`, `route_to != "12-escalation"` |

### `tests/agent_isolation_test.py` (extended)

A prior-auth run over Clinic A's appointment never reads or writes a Clinic B row
(`prior_authorizations`, `activity_log`, `escalations`).

Run: `python tests/prior_auth_determiner_test.py` and
`python tests/prior_auth_commander_test.py` (no stack); with the stack up,
`python tests/prior_auth_orchestrator_test.py` /
`python tests/e2e_prior_auth_test.py`. **No `ANTHROPIC_API_KEY` needed anywhere
in Phase 3** — 02 has no LLM step.

---

## 13. Open decisions — resolve at review

1. **Human approval mechanism.** Spec has dedicated
   `/approve-submission` + `/decline-submission` endpoints (backend-mediated, no
   client write grant — Phase 2 style). Alternative: reuse the claims
   `recommendations` table + column grant. → **Recommend the dedicated
   endpoints** (keeps Phase 2's "no client writes" discipline; PA approval is not
   a `recommendations` row).
2. **`02.submit` as part of 02 vs. a new executor number.** Spec folds submission
   into 02, gated by A7 (the `prior_auth_submission_approved` trigger is the
   structural gate, like R9 behind R7/R8). Alternative: mint `13-auth-submitter`
   to mirror the 08→09 split exactly. → **Recommend folding into 02** — 02–05/11
   stay reserved for the larger agents; the gate is the trigger, not the agent
   boundary.
3. **Does an approved PA feed the claims pipeline?** A future claim for that
   procedure could have `authorization_present = true` set automatically from an
   `auth_approved` PA. Phase 3, like Phase 2, keeps the modules **separate** (the
   sidecar). Cross-wiring the three patient-access agents into claims is a
   candidate for a dedicated later phase. → **Recommend: keep separate in
   Phase 3**, note it in `PHASE-3.md` "Still not built".
4. **`place_of_service` values.** Spec uses a free-text column with four
   documented values (`office|outpatient|inpatient|emergency`). Alternative: a
   `place_of_service` enum. → **Recommend text + a check constraint** — it is a
   snapshot/label, not a state machine, and real POS codes are a long list.
5. **New nav item vs. reuse the Insurance stub.** Spec adds a **Prior Auth** nav
   item with a badge and leaves `/app/insurance` for a later payer-directory
   feature. → confirm, or fold PA into `/app/insurance`.
6. **`info_needed` band width** (`threshold + 12`). Arbitrary; tune for a
   realistic seed distribution at review. Same latitude as the eligibility
   thresholds.

`00-commander.md` §2/§5/§13 and `architecture.md` §6/§7 to be updated to match
once these are resolved.
