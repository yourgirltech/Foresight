# 01 — Eligibility Verification Agent

_Spec. Written before implementation, per the Phase 2 plan. Cross-check this
document — and the §12 addition to [`00-commander.md`](00-commander.md) — before
any Phase 2 code is written._

_Status: **IMPLEMENTED** (2026-09-06). Reviewed and approved with five decisions
(§13). Code: `backend/app/agents/eligibility.py` (pure `simulate`),
`commander._decide_eligibility` (E1-E7), `orchestrator.handle_eligibility`;
migration `supabase/migrations/20260906000001_eligibility_verification.sql`;
seed `scripts/seed_eligibility.py`; UI `frontend/src/pages/AppointmentsPage.tsx`
+ `AppointmentDetailPage.tsx` + `EligibilityCheckDetailPage.tsx`; tests
`tests/eligibility_commander_test.py` (E1-E7 + the 648-state care-safety fuzz),
`tests/eligibility_orchestrator_test.py`, `tests/e2e_eligibility_test.py`, and
an eligibility slice added to `tests/agent_isolation_test.py`._

_One change from the spec during review (§13 Q2): an emergency registration
creates **only** an `eligibility_checks` row (`appointment_id` null), never an
appointments row. Re-checks chain via a new `previous_check_id` column._

---

## 1. What 01 is

The **Eligibility Verification Agent** answers one question about a patient
encounter: _does this patient have active insurance coverage with this payer?_

In Phase 2 it does **not** call a real clearinghouse. It runs a **deterministic
simulation** against the payer configuration we already seed (§7) — the same way
06 (`rules.py`) is a deterministic engine over seeded payer config, not a live
integration. The point of this phase is to prove three things before any real
integration is chosen:

1. the **Commander wiring** — a new trigger family, a new rule block, a new
   routed agent — works end to end;
2. the **EMTALA-required behavior** (§2) is enforced *structurally*, not by a
   comment;
3. the simulation produces a **meaningful, reproducible distribution** so the
   seed and the UI show something real.

01 is a specialist agent in the sense of `00-commander.md` §2: it does one job
and returns control. It is **mostly pure** — a pure `simulate()` core (no I/O, no
LLM, fully unit-testable) wrapped by a thin persistence shell (§11).

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 01 | **eligibility-agent** | deterministic simulation | no | patient + payer → an eligibility result, written to `eligibility_checks` |

---

## 2. The non-negotiable: eligibility verification never gates emergency care

> **For a scheduled appointment**, verification runs *ahead of time* and informs
> staff before the visit.
>
> **For an emergency / unscheduled patient**, verification runs *in parallel with
> care*, the moment any patient information exists. It never blocks, never
> delays, and is never a precondition for anything care-related.

This is an EMTALA-shaped requirement and it is treated like the Phase 1
human-in-the-loop invariant: **enforced by the structure of the design**, not by
developer discipline. Eight concrete mechanisms, each independently verifiable:

| # | Mechanism | Where it lives |
|---|-----------|----------------|
| M1 | **Separate trigger.** `emergency_patient_registered` is a distinct trigger type from `appointment_scheduled`; they diverge on the first line of the rule block. There is no shared "verify" path they both funnel through. | §5, §6 (E1 vs E3) |
| M2 | **`eligibility_checks` is a sidecar table.** No care object has a `not null` FK to it. No care workflow has a state meaning "waiting on eligibility". Nothing in the schema can put one there. | §4 |
| M3 | **`is_emergency` is snapshotted onto every `eligibility_checks` row.** The care-safety context travels *with the data*, so any query or test can select `where is_emergency` and assert none of those checks ever gated anything. | §4 |
| M4 | **The Commander's `next_status` is `None` for every eligibility rule (E1–E7).** The Commander never transitions an appointment, encounter, or any care object off the back of an eligibility trigger. Enforced by an `assert` in the orchestrator *and* a fuzz test, not only prose. | §6, §9, §12 |
| M5 | **No emergency-context rule routes to `12-escalation`.** E6 — the only eligibility rule that escalates — requires `not is_emergency`. Every emergency outcome is `no_action` or a route to `01-eligibility` itself. | §6 |
| M6 | **Emergency `01` execution is detached.** The orchestrator spawns it; nothing on a care path `await`s it; its exceptions are swallowed into a recorded `check_failed`; it emits no chained follow-on. A hung or throwing `01` cannot propagate anywhere near care. | §9 |
| M7 | **`insufficient_info` and `check_failed` are recorded outcomes, never errors, for an emergency.** They carry `recheck_recommended: true` and mean "re-check when more patient info exists" — the expected, common case for an unidentified or unconscious patient. | §6 (E4), §7, §8 |
| M8 | **Ambiguity fails safe.** Missing or contradictory `is_emergency`, or a check with no appointment, is treated as emergency (the non-blocking path). You cannot accidentally land an emergency in the gating path. | §6 (E2, E7) |

If someone later adds a real integration, these mechanisms are the checklist it
must not break — and the tests in §12 fail loudly if it does.

---

## 3. Where 01 sits in the system

```
appointment booked ──► appointment_scheduled ──► 00 ──E3──► 01 (ahead of time, awaited)
                                                              │
                                                     eligibility_check_completed
                                                              │
                                                        00 ──E5──► record, stop
                                                        00 ──E6──► 12 (operational: re-verify before visit)

ER patient walks in ─► emergency_patient_registered ─► 00 ──E1──► 01  (DETACHED — fire and forget)
   (care proceeds,                                                   │
    unaffected)                                          eligibility_check_completed / _failed
                                                                    │
                                                              00 ──E4──► record, stop
                                                                    (insufficient_info / check_failed
                                                                     => recheck_recommended, NOT escalated)
```

The left column — "care proceeds, unaffected" — is the whole point. There is no
arrow from any eligibility box back into a care box.

---

## 4. Data model

New migration: `supabase/migrations/20260906000001_eligibility_verification.sql`.
Same rules as Phase 1: every table carries
`organization_id uuid not null references public.organizations(id)` and is run
through `select public.enable_tenant_isolation('public.<table>')`. No hardcoded
tenant id anywhere.

### 4.1 New enum

```sql
create type public.eligibility_status as enum (
  'pending',            -- created, 01 has not run yet
  'verified_active',    -- simulation: active coverage with this payer
  'verified_inactive',  -- simulation: coverage found but not active (lapsed / termed)
  'insufficient_info',  -- not enough identity to check — EXPECTED for unidentified patients, not an error
  'check_failed'        -- the (simulated) clearinghouse could not complete the check
);
```

`insufficient_info` and `check_failed` are deliberately **separate**:
`insufficient_info` is "we could not identify the member"; `check_failed` is "the
verification channel itself failed". Neither is an escalation for an emergency
(§6, M7).

### 4.2 `appointments`

```sql
create table public.appointments (
  id                uuid primary key default gen_random_uuid(),
  organization_id   uuid not null references public.organizations (id) on delete cascade,
  patient_name      text not null,
  patient_member_id text not null default '',   -- '' / 'UNKNOWN' allowed: an ER walk-in has no card yet
  patient_dob       date,                        -- nullable
  payer_id          uuid references public.payers (id) on delete restrict,  -- nullable: unknown at ER intake
  scheduled_at      timestamptz,                 -- NULL => unscheduled / emergency registration
  is_emergency      boolean not null default false,
  created_at        timestamptz not null default now()
);
create index appointments_organization_id_idx on public.appointments (organization_id);
create index appointments_scheduled_at_idx    on public.appointments (organization_id, scheduled_at);
```

The user's field list was `organization_id, patient info, scheduled_at,
is_emergency, created_at`. "Patient info" is expanded to `patient_name`,
`patient_member_id`, `patient_dob`; `payer_id` is added because the eligibility
check needs a payer to check against. `scheduled_at` and `payer_id` are nullable
**by design** — an emergency registration legitimately has neither.

An emergency registration **does** create an `appointments` row
(`is_emergency = true`, `scheduled_at = null`). That row is immediately valid and
usable; the eligibility check is attached to it asynchronously and its state
never feeds back.

### 4.3 `eligibility_checks`

```sql
create table public.eligibility_checks (
  id                uuid primary key default gen_random_uuid(),
  organization_id   uuid not null references public.organizations (id) on delete cascade,
  appointment_id    uuid references public.appointments (id) on delete cascade,  -- nullable BY DESIGN
  patient_name      text not null,               -- snapshot at check time
  patient_member_id text not null default '',    -- snapshot ('' if unknown)
  payer_id          uuid references public.payers (id) on delete restrict,       -- nullable
  payer_name        text,                        -- snapshot
  is_emergency      boolean not null default false,  -- care-context snapshot — drives the safety invariant (M3)
  status            public.eligibility_status not null default 'pending',
  result_payload    jsonb not null default '{}'::jsonb,
  checked_at        timestamptz,                 -- set when 01 completes
  created_at        timestamptz not null default now()
);
create index eligibility_checks_organization_id_idx on public.eligibility_checks (organization_id);
create index eligibility_checks_appointment_id_idx  on public.eligibility_checks (appointment_id, created_at);
```

- `appointment_id` nullable — mirrors `escalations.claim_id` / `activity_log.claim_id`
  in Phase 1. Phase 2 always links one, but the schema does not require it, so a
  future "verify a patient with no appointment yet" path costs no migration.
- **A re-check inserts a new row.** History is append-only, like
  `recommendations`. The "add more info, re-verify" flow (§7) produces a second
  row, and the UI shows the progression `insufficient_info → verified_active`.
- `is_emergency` is copied from the appointment (or the registration payload)
  **at creation** and never updated. It is the authoritative field E4 branches on.

### 4.4 `payers` — two new columns

```sql
alter table public.payers
  add column eligibility_verification_supported boolean not null default true,
  add column eligibility_active_threshold       integer not null default 85
    check (eligibility_active_threshold between 0 and 100);
```

These are the simulation knobs (§7) — the eligibility equivalent of
`authorization_required` / `follow_up_threshold_days` for 06. A payer with
`eligibility_verification_supported = false` models "not on the real-time
eligibility network" → every check against it returns `check_failed`.
`eligibility_active_threshold` is the percentage of members the simulation shows
as active for that payer.

### 4.5 Audit trail — `activity_log` / `escalations` gain two nullable columns

```sql
alter table public.activity_log
  add column appointment_id       uuid references public.appointments (id)      on delete cascade,
  add column eligibility_check_id uuid references public.eligibility_checks (id) on delete cascade;
alter table public.escalations
  add column appointment_id       uuid references public.appointments (id)      on delete cascade,
  add column eligibility_check_id uuid references public.eligibility_checks (id) on delete cascade;
create index activity_log_eligibility_check_id_idx on public.activity_log (eligibility_check_id, created_at);
create index escalations_eligibility_check_id_idx  on public.escalations  (eligibility_check_id);
```

Phase 1 rows keep `claim_id`; Phase 2 rows use the new columns; all three are
nullable. The `activity_log` / `escalations` writers gain optional
`appointment_id` / `eligibility_check_id` kwargs.

### 4.6 Isolation + grants

```sql
select public.enable_tenant_isolation('public.appointments');
select public.enable_tenant_isolation('public.eligibility_checks');

revoke all on public.appointments      from anon, authenticated;
revoke all on public.eligibility_checks from anon, authenticated;
grant  select on public.appointments      to authenticated;   -- UI reads directly, RLS-scoped
grant  select on public.eligibility_checks to authenticated;
```

No client write grants in Phase 2. Appointments are created by the seed and by a
backend endpoint (§10); all eligibility writes go through the service-role agent
path, `organization_id`-filtered from the triggering row, exactly as in
`architecture.md` §3.4 and proven by the isolation test (§12).

---

## 5. Trigger taxonomy — additions to `00-commander.md` §5

| trigger `type` | emitted when | typical source |
|----------------|--------------|----------------|
| `appointment_scheduled` | an appointment row is created with `is_emergency = false` | seed, `POST /api/appointments` |
| `emergency_patient_registered` | an appointment/registration row is created with `is_emergency = true` | seed, `POST /api/appointments` (emergency), ER intake |
| `eligibility_check_completed` | 01 finished and wrote a terminal `eligibility_checks.status` (`verified_active` / `verified_inactive` / `insufficient_info`) | orchestrator, after running 01 |
| `eligibility_check_failed` | 01 could not complete — payer not supported, or the pure core raised | orchestrator, after running 01 |

`payload` carries context for the orchestrator / `activity_log` only — the
Commander branches on `type` and on the eligibility state (§6), never on
`payload`. As in Phase 1.

These four types form the **eligibility trigger family**. `00-commander.md` §12
specifies that `decide()` dispatches on this family *before* the claims rule
table (R1–R20) is consulted; a claims trigger never reaches an E-rule and an
eligibility trigger never reaches an R-rule.

---

## 6. The eligibility rule block (E1–E7)

Lives in `00-commander.md` §12.6. Restated here in full for review. Evaluated
**top to bottom, first match wins**, exactly like R1–R20. `CommanderDecision` is
the **unchanged** Phase 1 dataclass — `route_to` gains the value
`"01-eligibility"`, `reason_code` gains the values below, and **`next_status` is
`None` for every row** (M4).

### 6.1 The state the Commander reads for an eligibility trigger

The orchestrator assembles this and hands it in. Read-only, single-tenant.

```python
elig_state = {
  "appointment": {                      # None for a registration with no appointment row
    "id":              "<uuid>",
    "organization_id": "<uuid>",
    "scheduled_at":    "<timestamptz|None>",
    "is_emergency":    False,
    "patient_name":    "...",
    "patient_member_id": "...",
  },
  "eligibility_check": {                 # the row this trigger concerns; None only for *_scheduled/registered
    "id":              "<uuid>",
    "organization_id": "<uuid>",
    "appointment_id":  "<uuid|None>",
    "is_emergency":    False,            # AUTHORITATIVE snapshot (M3)
    "status":          "pending",        # pending|verified_active|verified_inactive|insufficient_info|check_failed
  },
  "payer": { "id": "...", "name": "...", "eligibility_verification_supported": True,
             "eligibility_active_threshold": 85 },   # or {} when unknown
  "context": {
    "is_emergency": False,               # resolved by the orchestrator; see §6.2
  },
}
```

### 6.2 How `is_emergency` is resolved (fail-safe — M8)

The orchestrator computes `context.is_emergency` as:

```
trigger is emergency_patient_registered                      -> True
eligibility_check.is_emergency is True                        -> True
appointment is not None and appointment.is_emergency is True  -> True
appointment is None  (a check with no appointment)            -> True   (fail-safe)
context.is_emergency missing / None                           -> True   (fail-safe)
otherwise                                                     -> False
```

Ambiguity always resolves toward **True** (the non-blocking path). The only way
to get `False` is an unambiguous, appointment-backed, non-emergency scheduled
booking.

### 6.3 The rule table

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **E1** | `trigger.type == "emergency_patient_registered"` | `route` | `01-eligibility` | `eligibility_emergency_fire_and_forget` | `None` |
| **E2** | `trigger.type == "appointment_scheduled"` **and** `context.is_emergency` | `route` | `01-eligibility` | `eligibility_emergency_fire_and_forget` | `None` |
| **E3** | `trigger.type == "appointment_scheduled"` | `route` | `01-eligibility` | `eligibility_scheduled_ahead_of_time` | `None` |
| **E4** | `trigger.type in {"eligibility_check_completed","eligibility_check_failed"}` **and** `context.is_emergency` | `no_action` | — | `eligibility_emergency_recorded` | `None` |
| **E5** | `trigger.type == "eligibility_check_completed"` | `no_action` | — | `eligibility_scheduled_recorded` | `None` |
| **E6** | `trigger.type == "eligibility_check_failed"` | `route` | `12-escalation` | `eligibility_check_failed_scheduled` | `None` |
| **E7** | any eligibility trigger, nothing above matched | `no_action` if `context.is_emergency` else `route` `12-escalation` | — / `12-escalation` | `eligibility_emergency_recorded` / `eligibility_unrecognized_state` | `None` |

Ordering rationale:

- **E1 first** — an emergency registration is fire-and-forget before anything
  else is considered. It routes to `01-eligibility` (the check must still *run*,
  just detached — §9), never to `12`.
- **E2 before E3** — a contradiction (`appointment_scheduled` but the row is
  emergency-flagged) resolves to the emergency path, not the scheduled one.
- **E4 before E5/E6** — every emergency completion/failure is recorded and
  stopped. `verified_active`, `verified_inactive`, `insufficient_info` and
  `check_failed` are treated **identically**: the agent already wrote
  `eligibility_checks.status` + `result_payload`; the Commander logs to
  `activity_log` and stops. No escalation, ever (M5, M7).
- **E5** — a scheduled check that completed (any of the three non-failure
  terminals). `verified_inactive` and `insufficient_info` are **recorded, not
  escalated** — they surface to staff on the appointments view as information to
  act on before the visit. The clinic decides what to do about a coverage
  problem; the system only informs.
- **E6** — a scheduled check that *failed* (payer unsupported, or the core
  raised). Because there is lead time before the appointment and no patient is
  in front of staff, this becomes an **operational task**: 12 logs it so a human
  re-runs the verification. This is the **only** eligibility rule that routes to
  12, and it is unreachable when `context.is_emergency` (E4 catches those first).
  Even here, nothing care-related changes state — `next_status` is `None`; 12
  just writes an `escalations` row scoped to the check.
- **E7** — fallthrough for a malformed eligibility state (e.g.
  `eligibility_check_completed` with no check row). Mirrors R20, but split: an
  emergency-context fallthrough is *recorded, not escalated* (M5); only a
  non-emergency fallthrough escalates.

### 6.4 The invariant, stated precisely

> For any eligibility trigger:
> - **`decision.next_status is None`** — always. (E1–E7.)
>
> For any eligibility trigger where `context.is_emergency` is true:
> - **`decision.route_to != "12-escalation"`** — always. The decision is either
>   `no_action`, or `route` to `01-eligibility`.
> - `insufficient_info` and `check_failed` produce `no_action` +
>   `eligibility_emergency_recorded`, identical to a successful verification.

`00-commander.md` §12 and the fuzz test in §12 assert exactly this.

### 6.5 Worked traces

| # | trigger | is_emergency | check.status | matches | outcome |
|---|---------|--------------|--------------|---------|---------|
| 1 | `appointment_scheduled` | false | (n/a) | E3 | route → 01, awaited; run ahead of the visit |
| 2 | `eligibility_check_completed` | false | `verified_active` | E5 | record, stop |
| 3 | `eligibility_check_completed` | false | `verified_inactive` | E5 | record, stop — staff see "inactive coverage" before the visit |
| 4 | `eligibility_check_completed` | false | `insufficient_info` | E5 | record, stop — staff prompt: confirm insurance at check-in |
| 5 | `eligibility_check_failed` | false | `check_failed` | E6 | route → 12; operational re-verify task |
| 6 | `emergency_patient_registered` | true | (n/a) | E1 | route → 01, **detached**; care proceeds |
| 7 | `eligibility_check_completed` | true | `insufficient_info` | E4 | `no_action`; `recheck_recommended`; **not** escalated |
| 8 | `eligibility_check_failed` | true | `check_failed` | E4 | `no_action`; **not** escalated (contrast trace 5) |
| 9 | `eligibility_check_completed` | true | `verified_active` | E4 | `no_action`; recorded for staff/billing whenever they look |
| 10 | `appointment_scheduled` but row `is_emergency = true` | true | (n/a) | E2 | route → 01, detached (contradiction → safe path) |
| 11 | `eligibility_check_completed`, no check row in state | true (fail-safe) | — | E7 | `no_action`, `eligibility_emergency_recorded` |
| 12 | `eligibility_check_completed`, no check row in state | false | — | E7 | route → 12, `eligibility_unrecognized_state` |

---

## 7. The simulation (01's core) — deterministic and documented

Pure function. No LLM, no I/O, no randomness at call time. Same inputs → same
`eligibility_status` every time. Documented here in prose; the implementation
(`backend/app/agents/eligibility.py`) is the code, this section is the contract —
kept in lockstep, exactly like 06 / `architecture.md` §6.1.

### 7.1 Inputs

```
patient : { name, member_id }
payer   : { id, name, eligibility_verification_supported, eligibility_active_threshold }  # or None
```

### 7.2 The bucket function

```
member_key = uppercase(strip non-alphanumeric from patient.member_id)
seed       = f"{payer.id}|{member_key}"
bucket     = int(sha256(seed.encode()).hexdigest()[:8], 16) % 100      # 0..99, stable forever
```

Stable across runs, machines, and Python versions (SHA-256 of a fixed string).
Different payers give the same member a different bucket — a member can be active
with one payer and inactive with another, which is realistic.

### 7.3 The decision (evaluated top to bottom)

```
1. not _has_min_identity(patient)          -> insufficient_info
2. payer is None                            -> insufficient_info
3. not payer.eligibility_verification_supported  -> check_failed
4. bucket < payer.eligibility_active_threshold   -> verified_active
5. otherwise                                -> verified_inactive
```

```
_has_min_identity(p):
    key  = member_key(p.member_id)
    name = uppercase(trim(p.name))
    return (
        key not in SENTINEL_MEMBER_IDS      # {"", "UNKNOWN", "NA", "NONE", "DEMO000"}
        and len(key) >= 4
        and name not in SENTINEL_NAMES      # {"", "UNKNOWN", "JOHN DOE", "JANE DOE",
    )                                       #  "UNIDENTIFIED", "TRAUMA", "DOE JOHN", "DOE JANE"}
```

Step 1 is what makes an unconscious / unidentified ER patient return
`insufficient_info` rather than a bogus coverage answer — and E4 then makes that
a routine "recheck later", not an error.

### 7.4 `result_payload`

```json
{
  "simulated": true,
  "basis": "deterministic-sim-v1",
  "checked_against": "Cascade Medicaid",
  "member_bucket": 47,                // null when insufficient_info
  "active_threshold": 68,             // null when insufficient_info / payer unknown
  "determination": "verified_inactive",
  "reason": "member bucket 47 is not below the active threshold 68 for this payer",
  "recheck_recommended": false,       // true for insufficient_info and check_failed
  "generated_at": "2026-09-06T12:00:00Z"
}
```

### 7.5 Seeded payer knobs → the distribution

`scripts/seed_eligibility.py` sets these on the existing seed payers, chosen to
produce a realistic spread (Medicaid churns more; one payer is off-network):

| payer | `verification_supported` | `active_threshold` | simulated meaning |
|-------|--------------------------|--------------------|-------------------|
| Meridian Health Plan | true | 90 | large commercial, mostly active |
| BlueRidge PPO | true | 82 | commercial PPO |
| Cascade Medicaid | true | 68 | Medicaid — more lapsed/termed coverage |
| Summit Commercial | **false** | (n/a) | not on the real-time eligibility network → `check_failed` |

Member ids for the synthetic appointments come from a **seeded** RNG
(`random.Random(f"{org_id}:elig")`), so the bucket for each patient — and hence
the whole `verified_active` / `verified_inactive` / `check_failed` /
`insufficient_info` distribution — is identical on every re-run. The seeder
prints it, the same way `seed_claims.py` prints the risk distribution.

---

## 8. Non-error outcomes are first-class

`insufficient_info` and `check_failed` are **normal**. The design never treats
them as failures to be alarmed about:

- 01 writes them as ordinary terminal statuses with a full `result_payload`.
- For an **emergency**, E4 records them and stops. `recheck_recommended: true`
  tells staff (and a future scheduled re-check job) that this one should be run
  again once more patient information exists. That is the entire "later gets
  re-checked once more info is added" flow (§7, item 4 of the Phase 2 plan).
- For a **scheduled** appointment, `insufficient_info` records and stops (E5) —
  it shows on the appointments view as "confirm insurance at check-in".
  `check_failed` (E6) becomes a human re-verify task because there is time to
  chase it before the visit.

Nothing about either outcome, in either context, blocks or delays care.

---

## 9. The orchestrator — `handle_eligibility`

New entry point in `backend/app/agents/orchestrator.py`, alongside the Phase 1
`handle`. Same shape, same loop-cap discipline (`MAX_INVOCATIONS`).

```python
async def handle_eligibility(check_id: str | None, trigger: dict, *,
                             appointment_id: str | None = None, _depth: int = 0) -> CommanderDecision:
    # 1. resolve organization_id ONCE, from the check row (or the appointment for *_scheduled/registered)
    # 2. load appointment + payer -> assemble elig_state (§6.1); resolve context.is_emergency (§6.2)
    # 3. decision = commander.decide(elig_state, trigger)
    # 4. insert_activity(actor="00-commander", action=decision.reason_code,
    #                    appointment_id=..., eligibility_check_id=check_id, details={...})
    # 5. assert decision.next_status is None, "eligibility Commander must never transition a care object"
    # 6. dispatch:
```

| decision | dispatch |
|----------|----------|
| `route` → `01-eligibility`, **emergency** (E1/E2) | **spawn detached** — `_spawn(_run_eligibility_agent(...))`; return the decision immediately. Nothing on any path awaits the task. |
| `route` → `01-eligibility`, **scheduled** (E3) | `await _run_eligibility_agent(...)`; then re-invoke `handle_eligibility` with the follow-on trigger it returns (`eligibility_check_completed` / `_failed`). |
| `route` → `12-escalation` (E6 only) | `await escalation.escalate(org_id, claim_pk=None, appointment_id=..., eligibility_check_id=check_id, reason_code=..., context={...})`; return. |
| `no_action` (E4/E5/E7-emergency) | return. |

### 9.1 `_run_eligibility_agent`

1. Run the pure `simulate(patient, payer)` core (§7).
2. Insert a **new** `eligibility_checks` row (or, on the very first run for a
   still-`pending` row, update that row) with the resolved `status`,
   `result_payload`, `checked_at = now`, and the `is_emergency` snapshot.
3. `insert_activity(actor="01-eligibility", action="verified", eligibility_check_id=..., details={status, bucket})`.
4. Return `{"type": "eligibility_check_completed"}`, or
   `{"type": "eligibility_check_failed"}` when the resolved status is
   `check_failed`.

### 9.2 Detached execution for emergencies (M6) — the mechanics

- The task is created with `_spawn(...)` (an `asyncio` task tracked in a
  module-level set so it is not GC'd, then discarded) — **never `await`ed** by
  `handle_eligibility` or by anything that called it.
- `_run_eligibility_agent`, when invoked on the emergency path, is wrapped so
  **any exception becomes a written `check_failed` row + an `activity_log` error
  entry, and is not re-raised.** A throwing or hanging 01 stays entirely inside
  the detached task.
- The follow-on trigger it emits (`eligibility_check_completed` / `_failed`)
  re-enters `handle_eligibility`, matches **E4**, and stops at `no_action`. The
  emergency path has no branch that routes to 12 and no branch that sets a
  status.
- Net: from the caller's point of view, `emergency_patient_registered` returns a
  `CommanderDecision` and control, essentially immediately. The verification
  result lands in `eligibility_checks` whenever 01 finishes, for staff and
  billing to read at their leisure.

### 9.3 Tenancy

Identical to Phase 1 (`00-commander.md` §8): `organization_id` resolved once from
the triggering row, threaded explicitly into every `db` call, never from a
request/env/constant. The new `db` helpers (`get_appointment`,
`get_eligibility_check`, `insert_eligibility_check`, `list_eligibility_checks`)
each take `org_id` and filter on it. `tests/agent_isolation_test.py` gains
eligibility cases (§12).

---

## 10. Backend + UI

### 10.1 Endpoints (`backend/app/routers/appointments.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/appointments` | appointments in the caller's clinic (RLS-scoped), each with its latest `eligibility_checks` status |
| `GET` | `/api/appointments/{id}` | one appointment + its full eligibility-check history + related activity |
| `POST` | `/api/appointments` | create an appointment/registration; body `{patient_name, patient_member_id?, payer_id?, scheduled_at?, is_emergency}`. Emits `appointment_scheduled` or `emergency_patient_registered` to `handle_eligibility`. |
| `POST` | `/api/appointments/{id}/verify-eligibility` | (re-)run verification for an appointment — used by the "more info added, re-check" flow and by staff manually. Creates a fresh `eligibility_checks` row. |

Reads use the caller's JWT through PostgREST (RLS). Writes verify the appointment
is visible to the caller first, then hand to the orchestrator. `is_emergency`
from the request body is honored **only** to make verification *more*
non-blocking — it can never be used to force the gating path.

### 10.2 UI (`frontend/src/pages/AppointmentsPage.tsx`, `AppointmentDetailPage.tsx`)

- New nav item **Appointments** (`/appointments`), alongside Claims / Team.
- **List**: table — `scheduled_at` (or "Walk-in / ER"), patient, payer, an
  `EligibilityBadge` for the latest check status, and an explicit
  **"Emergency — care not gated"** pill on emergency rows so the non-blocking
  principle is visible in the product, not just the code.
- **Detail**: the appointment, the full check history (newest first) with each
  `result_payload` rendered readably, and the related `activity_log` slice.
- `EligibilityBadge` tones: `pending` slate · `verified_active` emerald ·
  `verified_inactive` amber (information, not an error) · `insufficient_info`
  violet ("needs more info") · `check_failed` red.
- Deliberately minimal — "real and functional", not polished (Phase 2 plan §5).

Types go in `frontend/src/lib/types.ts` (`Appointment`, `EligibilityCheck`,
`EligibilityStatus`, `AppointmentDetail`).

---

## 11. Interfaces

```python
# backend/app/agents/eligibility.py

from dataclasses import dataclass

@dataclass(frozen=True)
class EligibilityResult:
    status: str            # eligibility_status enum value
    result_payload: dict

def simulate(patient: dict, payer: dict | None, *, now=None) -> EligibilityResult:
    """Pure. No I/O, no LLM, no call-time randomness. §7 is the contract."""
```

`00-commander.md`:

```python
# route_to gains "01-eligibility"; CommanderDecision is otherwise UNCHANGED.
ELIGIBILITY_TRIGGERS = {
    "appointment_scheduled", "emergency_patient_registered",
    "eligibility_check_completed", "eligibility_check_failed",
}
# decide() dispatches to _decide_eligibility() when trigger.type in ELIGIBILITY_TRIGGERS,
# BEFORE the R1-R20 claims table. The two tables never interleave.
```

`reason_code` closed-set additions: `eligibility_emergency_fire_and_forget`,
`eligibility_scheduled_ahead_of_time`, `eligibility_emergency_recorded`,
`eligibility_scheduled_recorded`, `eligibility_check_failed_scheduled`,
`eligibility_unrecognized_state`.

---

## 12. Test plan

### `tests/eligibility_commander_test.py` (deterministic, no stack, no key)

- One case per rule **E1–E7** (table-driven, same style as `commander_test.py`).
- The **claims table is untouched**: import and re-run a few R-rule cases;
  assert `EXECUTABLE_ACTIONS` / `MANUAL_ACTIONS` unchanged.
- **The emergency-care-safety fuzz** — the cross-check that matters:
  for every combination of
  `trigger ∈ {appointment_scheduled, emergency_patient_registered,
  eligibility_check_completed, eligibility_check_failed}` ×
  `check.status ∈ {all 5}` × `is_emergency ∈ {True, False}` ×
  `appointment ∈ {present, None}` (a few hundred cases), call `decide` twice and
  assert:
  - determinism (both calls identical);
  - **`decision.next_status is None`** — every case;
  - `is_emergency` (resolved, §6.2) ⟹ **`decision.route_to != "12-escalation"`**
    and `decision.action in {"no_action", "route"}` with
    `route_to in {None, "01-eligibility"}`;
  - no `decision.next_status` is ever a `claim_status` enum value.
- A named test `test_emergency_eligibility_is_never_in_a_gating_path` that
  enumerates every emergency decision and asserts the two prohibitions
  explicitly, with a comment pointing back to §2 / M4 / M5.

### `tests/eligibility_orchestrator_test.py` (local stack, no key)

- Monkeypatch `simulate` to **raise**; call `handle_eligibility(..., emergency)`;
  assert it returns cleanly, the check row ends `check_failed`, `activity_log`
  has the error entry, and **no `escalations` row exists** (M6, M7).
- Same with a scheduled trigger: assert an `escalations` row *does* appear
  (E6) — the contrast case.
- Assert the emergency dispatch never `await`s the agent on the calling path
  (structural: the call returns before the patched slow `simulate` resolves).

### `tests/e2e_eligibility_test.py` (local stack — real end to end, prints PASS/FAIL lines)

Same pattern and helpers as `e2e_claim_test.py` (`_agentlib.Check`, `ensure_org`,
`svc_get` / `svc_write`). Real orgs via the signup/bootstrap path. Prints every
check, not a summary. Scenarios:

| # | setup | asserted |
|---|-------|----------|
| 1 | scheduled appt, Meridian (supported, thr 90), member id that buckets active → `appointment_scheduled` | 01 ran; `eligibility_check_completed` → E5; row `verified_active`; appointment unchanged; activity chain present; all rows org-scoped |
| 2 | scheduled appt, Summit (unsupported) → `appointment_scheduled` | 01 → `check_failed` → E6 → one `escalations` row (`eligibility_check_failed_scheduled`, from `00-commander`), org-scoped; appointment unchanged |
| 3 | **emergency** registration, blank member id → `emergency_patient_registered` | E1 fire-and-forget; 01 → `insufficient_info` → `eligibility_check_completed` → E4; **no `escalations` row**; check `insufficient_info` with `recheck_recommended: true`; appointment `is_emergency = true`, `scheduled_at = null`, no blocking state anywhere; every Commander `next_status` was `None` |
| 4 | scenario 3's appointment, member id now filled in → `POST /verify-eligibility` | a **second** `eligibility_checks` row; `verified_active`; history for that appointment reads `insufficient_info → verified_active` |
| 5 | **emergency** registration, Summit (unsupported) → `emergency_patient_registered` | 01 → `check_failed` → E4 → `no_action`; **no `escalations` row** (proves emergency `check_failed` ≠ escalation — contrast scenario 2) |
| 6 | second org, run scenario 1 again | first org sees no new rows; no `eligibility_checks` / `escalations` / `activity_log` leaked across the tenant boundary |
| 7 | collected from scenarios 3 & 5 | every `CommanderDecision` recorded during an emergency flow had `next_status is None` and `route_to != "12-escalation"` |

Run: `python tests/eligibility_commander_test.py` and, with the stack up,
`python tests/eligibility_orchestrator_test.py` / `python tests/e2e_eligibility_test.py`.
No `ANTHROPIC_API_KEY` needed anywhere in Phase 2 — 01 has no LLM step.

### `tests/agent_isolation_test.py` (extended)

Add: an eligibility run over Clinic A's appointment never reads or writes a
Clinic B row (appointments, eligibility_checks, activity_log, escalations).

---

## 13. Decisions — resolved at review (2026-09-06)

1. **E6 — scheduled `check_failed` escalates; emergency `check_failed` does
   not.** Kept as specified. The scheduled case is an operational re-verify task
   (lead time exists); the emergency case is a recorded outcome with
   `recheck_recommended`. This is the one place the two contexts differ on
   purpose. → `eligibility_orchestrator_test.py` proves both halves.
2. **An emergency registration creates ONLY an `eligibility_checks` row**
   (`appointment_id` null) — **no** appointments row. Changed from the original
   spec. The append-only re-check chain is linked by a new
   `eligibility_checks.previous_check_id` column.
3. **Re-check appends a new `eligibility_checks` row** (history preserved),
   never updates in place.
4. **Two new `payers` columns** (`eligibility_verification_supported`,
   `eligibility_active_threshold`) — not a separate config table.
5. **One shared `decide()` entry point.** `decide()` dispatches to
   `_decide_eligibility` on the first line when
   `trigger.type in ELIGIBILITY_TRIGGERS`, before R1. No separate exported
   function.

`00-commander.md` §2/§5/§12 and `architecture.md` §6.3/§7 updated to match.
