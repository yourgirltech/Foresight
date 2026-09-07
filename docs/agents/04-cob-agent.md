# 04 — Coordination of Benefits Agent

_Spec. Written before implementation, per the Phase 4 plan. Cross-check this
document before any agent-04 code is written._

_Status: **SPEC — awaiting review.** Companion: [`../PHASE-4.md`](../PHASE-4.md).
No code exists yet._

---

## 1. What 04 is

The **Coordination of Benefits Agent** answers one question: _when a patient has
two or more active insurance coverages, which one pays first?_

It is a **pure deterministic rule engine** — no LLM, no simulation, no external
dependency. Coordination of benefits is genuinely just rules: the NAIC model
regulation that (almost) every state has adopted spells out an ordered list of
tie-breakers, and the answer is a mechanical walk down that list. 04 is
`rules.py` (06) in a new domain.

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 04 | **cob-agent** | deterministic rule engine | no | a patient's active coverages → each labelled `primary` / `secondary` / `tertiary`, **with the specific rule that decided the order cited** in the output |

## 2. The non-negotiable: the reasoning is always shown, never a black box

> 04 never returns a bare ordering. Every coverage in the output carries the
> **numbered rule** (§6) that placed it and a one-line rationale, so a biller can
> verify the logic — not just trust an answer.

And, like all of Phase 4:

| # | Mechanism | Where |
|---|-----------|-------|
| C1 | **`patient_coverages` is a plain data table.** 04 reads it and computes; it writes nothing back. Adding, editing, or removing a coverage is a human action through an endpoint. | §4, §7 |
| C2 | **`determine_cob()` is pure** — list of coverages in, ordered list with cited rules out. No I/O, no randomness, no time-dependence except "today" for the active-window check, which is passed in. Fully unit-testable. | §6 |
| C3 | **04 reports, it does not act.** Nothing downstream is triggered by a COB result. A human reads the insurance summary and can override the order when they know something the data doesn't (a court decree, a card that says otherwise). | §7 |
| C4 | **Not a Commander agent.** Synchronous, invoked while rendering an insurance summary. `commander.py` untouched. | `../PHASE-4.md` |

## 3. Where 04 sits

```
front desk records the patient's coverages (from cards / the patient)
        │
   POST /api/coverages           ── one patient_coverages row per plan
        │
   GET /api/appointments/{id}   (or GET /api/coverages?patient_name=&patient_dob=)
        │
   determine_cob(active coverages, today)   ── pure
        │
   returns: [{coverage_id, order: primary|secondary|tertiary, rule: "R3 birthday rule", rationale: "..."}]
        │
   ── UI: an "Insurance summary" card on the appointment detail page —
        each coverage with a Primary / Secondary badge and the deciding rule shown
```

## 4. Data model

New migration `supabase/migrations/2026090X000002_coordination_of_benefits.sql`.

### 4.1 Enums

```sql
create type public.coverage_relationship as enum ('self', 'spouse', 'child', 'other');
create type public.coverage_type as enum (
  'employer_active',   -- group plan through a currently-employed subscriber
  'employer_retiree',  -- group plan through a retired subscriber
  'cobra',             -- COBRA continuation
  'individual',        -- individually purchased / marketplace
  'medicare',
  'medicaid',
  'tricare',
  'other'
);
```

### 4.2 `patient_coverages`

```sql
create table public.patient_coverages (
  id                        uuid primary key default gen_random_uuid(),
  organization_id           uuid not null references public.organizations (id) on delete cascade,
  appointment_id            uuid references public.appointments (id) on delete set null,  -- optional link
  patient_name              text not null,
  patient_dob               date,                          -- the PATIENT's dob (soft identity key)
  payer_id                  uuid references public.payers (id) on delete set null,
  payer_name                text not null,                 -- snapshot (a coverage may name a payer not in the directory)
  member_id                 text not null default '',
  group_number              text not null default '',
  plan_kind                 text not null default 'medical',  -- medical | dental | vision | rx  (COB is computed per kind)
  coverage_type             public.coverage_type not null default 'employer_active',
  relationship_to_subscriber public.coverage_relationship not null default 'self',
  is_dependent              boolean not null default false,   -- patient is covered as someone's dependent
  subscriber_name           text not null default '',
  subscriber_dob            date,                          -- needed for the birthday rule
  effective_date            date not null,
  termination_date          date,                          -- null => open
  manual_order_override     smallint,                      -- a human can pin 1/2/3; NULL => let 04 decide
  created_at                timestamptz not null default now()
);
create index patient_coverages_organization_id_idx on public.patient_coverages (organization_id);
create index patient_coverages_patient_idx on public.patient_coverages (organization_id, patient_name, patient_dob);
create index patient_coverages_appointment_id_idx on public.patient_coverages (appointment_id);

select public.enable_tenant_isolation('public.patient_coverages');
revoke all on public.patient_coverages from anon, authenticated;
grant  select on public.patient_coverages to authenticated;
```

- **Active** = `effective_date <= today AND (termination_date IS NULL OR
  termination_date >= today)`. Computed in the query / the pure function, not
  stored.
- COB is computed **within one `plan_kind`** — a patient's medical and dental
  coverages don't coordinate with each other.
- `manual_order_override` is C3 in the schema: a human pins the order and 04
  reports "set by staff" instead of a rule.

## 5. Settings

None. 04 has no config and no external dependency.

## 6. The COB rule engine (`determine_cob`) — the contract

### 6.1 Interface

```python
# backend/app/agents/cob.py
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class CobPlacement:
    coverage_id: str
    order: str          # "primary" | "secondary" | "tertiary"
    rule: str           # "R3" ... — the rule id that decided this coverage's rank
    rationale: str      # one plain-language sentence

def determine_cob(coverages: list[dict], *, today: date, patient_dob: date | None) -> list[CobPlacement]:
    """Pure. `coverages` are the ACTIVE coverages for ONE patient and ONE
    plan_kind. Returns one placement per coverage, ordered. Deterministic."""
```

### 6.2 The ordered rule list (NAIC model regulation, simplified + documented)

Coverages are compared pairwise; the first rule that distinguishes a pair fixes
their relative order. Evaluated top to bottom.

| # | Rule | Primary is… |
|---|------|-------------|
| **R0** | **Manual override.** Any coverage with `manual_order_override` set is placed exactly there; the rest fill around it. Rule cited: `"R0 set by staff"`. | as pinned |
| **R1** | **Non-dependent over dependent.** The plan that covers the patient as the subscriber / employee (`relationship_to_subscriber == "self"`, `is_dependent == false`) is primary over a plan that covers them as a dependent. | the patient's own plan |
| **R2** | **Medicaid is always last.** `coverage_type == "medicaid"` is the payer of last resort — secondary (or tertiary) to every non-Medicaid coverage. | the non-Medicaid plan |
| **R3** | **Birthday rule** (patient is a dependent child on two parents' plans, i.e. both coverages have `is_dependent == true` and `relationship_to_subscriber == "child"`). The plan of the parent whose **birthday (month + day, year ignored) falls earlier in the calendar year** is primary. | earlier-in-year parent's plan |
| **R4** | **Active over inactive employment.** A plan through an actively-employed subscriber (`employer_active`, or a dependent of one) is primary over a plan through a retired / laid-off subscriber (`employer_retiree`, `cobra`). | the active-employment plan |
| **R5** | **Medicare Secondary Payer (simplified).** Medicare is **secondary** to `employer_active` (and to a dependent-of-active plan); Medicare is **primary** over `employer_retiree`, `cobra`, `individual`. | per the other plan's type |
| **R6** | **Longer-covered plan is primary.** The plan with the earlier `effective_date` has covered the person longer and is primary. | earlier `effective_date` |
| **R7** | **Deterministic final tie-break.** If everything above ties, order by `(payer_name, member_id, coverage_id)` ascending — so the result is still deterministic and the rationale says "no distinguishing rule applied; ordered by payer name for stability — confirm with the payers." | lexically first |

Notes:

- **Divorce / custody** is explicitly **out of scope** — we don't capture a court
  decree or custody arrangement. When both parents' plans cover a child, R3
  (birthday rule) applies, which is also the NAIC default in the absence of a
  decree. The rationale for an R3 placement says so.
- **The 20-employee rule for MSP** (Medicare is only secondary to an active group
  plan if the employer has ≥ 20 employees) is **out of scope** — we don't capture
  employer size. R5 assumes the group-plan-primary case and the rationale flags
  it: "assumes an employer with 20+ employees; verify with the plan."
- Every `CobPlacement.rationale` names the concrete facts that fired the rule
  (e.g. "Subscriber birthday Mar 3 is earlier in the year than Nov 12 → this plan
  is primary under the birthday rule").

### 6.3 Worked cases (all in the test suite)

| coverages | result |
|-----------|--------|
| patient's own `employer_active` + spouse's plan covering them as `spouse`/dependent | own plan primary (R1) |
| child on mom's plan (DOB Mar 3) + child on dad's plan (DOB Nov 12) | mom's plan primary (R3) |
| child on both parents' plans, both parents born Jun 1 | earlier `effective_date` primary (R3 → tie → R6) |
| `employer_active` + `medicaid` | employer plan primary, Medicaid secondary (R2) |
| `employer_retiree` + `medicare` | Medicare primary (R5) |
| `employer_active` + `medicare` | employer plan primary, Medicare secondary (R5) |
| `cobra` + `employer_active` (spouse's, as dependent) | active plan primary (R4) |
| two identical `individual` plans, same dates | deterministic order by payer name (R7), rationale flags "confirm" |

## 7. Backend + UI

### 7.1 Endpoints (`backend/app/routers/coverages.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/coverages?patient_name=&patient_dob=` | that patient's coverages + `determine_cob()` over the active ones, per `plan_kind` |
| `POST` | `/api/coverages` | add a coverage (body = the row fields + optional `appointment_id`) |
| `PATCH` | `/api/coverages/{id}` | edit a coverage; set / clear `manual_order_override` |
| `DELETE` | `/api/coverages/{id}` | remove a coverage |

`GET /api/appointments/{id}` is **extended** to include
`coverages` + `cob` (matched to the appointment's `patient_name` +
`patient_dob`), so the insurance summary renders inline.

### 7.2 UI

On **`AppointmentDetailPage`**, a new **Insurance summary** card:

- one row per coverage: payer, member ID, `Primary` / `Secondary` / `Tertiary`
  badge, and the deciding rule as a muted subline ("Primary — birthday rule:
  subscriber born Mar 3");
- an **Add coverage** form (payer, relationship, coverage type, effective date,
  subscriber DOB);
- a small **pin order** control per row that sets `manual_order_override` (for
  when staff know better), which then shows "Primary — set by staff".

No standalone page (per the review).

## 8. Test plan

### `tests/cob_test.py` (pure, no stack, no key) — same standard as the fuzz tests

- **Every worked case in §6.3** as a named test.
- **The exhaustive fuzz.** Over 2 coverages built from the cross-product of
  `relationship ∈ {self, child, spouse}` × `is_dependent ∈ {T, F}` ×
  `coverage_type ∈ {all 8}` × `effective_date order ∈ {a<b, a>b, a==b}` ×
  `subscriber birthday order ∈ {earlier, later, same}` (a few thousand cases),
  call `determine_cob` twice and assert:
  - **deterministic** (both calls identical);
  - **exactly one** `primary`, and the orders are a permutation of
    `{primary, secondary}` (no gaps, no duplicates);
  - the cited `rule` is one of `R0..R7` and, for the pair, the rule's stated
    criterion actually holds in the chosen direction (e.g. an `R3` placement's
    primary really does have the earlier-in-year birthday);
  - swapping the input order of the two coverages produces the **same**
    placements (order-independence).
- A 3-coverage slice: own plan + spouse's plan + Medicaid → `[primary own (R1),
  secondary spouse (R1), tertiary medicaid (R2)]`.
- `manual_order_override` pins are honoured and cited as `R0`.

### `tests/agent_isolation_test.py` (extended)

A `GET /api/coverages` for a Clinic A patient never returns or is influenced by a
Clinic B `patient_coverages` row with the same `patient_name` + `patient_dob`.

## 9. Open decisions — resolve at review

1. **Patient identity** — `patient_name` + `patient_dob` as the soft key (matches
   the codebase), vs. introducing a minimal `patients` table now. → recommend the
   **soft key** for Phase 4; a `patients` table is a bigger refactor across
   appointments / eligibility / prior-auth too.
2. **`plan_kind` scope** — compute COB for `medical` only in Phase 4, or all
   kinds. → recommend **all kinds** (the logic is identical; it's just a
   `group by`), but the UI leads with medical.
3. **R5 (MSP) depth** — the simplified version above, or capture employer size
   for the 20-employee rule. → recommend **simplified + a flagged rationale**;
   employer size is a data-capture project of its own.
4. **Where "Add coverage" lives** — only on the appointment detail card, or also
   a light coverages view under Insurance. → recommend **appointment card only**
   for Phase 4.
