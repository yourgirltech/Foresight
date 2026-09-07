# 05 — Cost Estimate Agent

_Spec. Written before implementation, per the Phase 4 plan. Cross-check this
document before any agent-05 code is written._

_Status: **BUILT** (2026-09-07). Companion: [`../PHASE-4.md`](../PHASE-4.md).
Implementation: migration `supabase/migrations/20260907000004_cost_estimates.sql`
(`procedure_prices` + `cost_estimates`), `backend/app/agents/cost_estimate.py`
(`price()` pure, `gate_reason()` pure, `phrase()` AI-wording-only,
`plain_template()` fallback, `NSA_GFE_DISCLAIMER` + `NSA_GFE_DISCLAIMER_VERSION`
+ `GFE_DISPUTE_THRESHOLD_USD` / `GFE_DISPUTE_WINDOW_DAYS` named constants),
`backend/app/routers/cost_estimates.py`, `scripts/seed_procedure_prices.py`,
`frontend/src/components/costEstimate.tsx` + `frontend/src/pages/CostEstimatePage.tsx`
(`/app/cost-estimates/:id`, print-friendly). Tests: `tests/cost_estimate_test.py`
(price arithmetic + the exhaustive `(self_pay × has_coverage)` gate grid +
disclaimer constant), `tests/e2e_cost_estimate_test.py`,
`tests/cost_estimate_live_test.py` (`--live`), and the cost-estimate slice of
`tests/agent_isolation_test.py`. `bash scripts/run_cost_estimate_proof.sh`._

_**`NSA_GFE_DISCLAIMER` carries a "REQUIRES LEGAL SIGN-OFF BEFORE PRODUCTION"
comment** (module docstring + the constant's own comment block), consistent with
PHASE-4.md open item **O-3** and `../BEFORE-PHI.md` item **C-3**. The
`nsa-gfe-2026-01` version string lets the wording be revised after sign-off
without rewriting stored estimates._

_As-built notes on §10: (1) disclaimer is a faithful CMS-model-language draft,
not counsel-reviewed — O-3; (2) the gate keeps BOTH conditions (`self_pay: true`
affirmation AND no active coverage); (3) the estimate is a dedicated printable
route; (4) the document notes it covers the listed services; (5) the seed reuses
the prior-auth code set with illustrative flat self-pay prices. One product
call: `phrase()` is given the service NAMES and the total only — never the
per-line prices — so the model cannot restate a figure, and the summary carries
exactly one dollar amount._

---

## 1. What 05 is

The **Cost Estimate Agent** produces a **Good Faith Estimate** for a self-pay
patient: given the planned procedures for a visit, it prices them and produces a
patient-facing document that meets the **No Surprises Act** requirement for
uninsured / self-pay individuals.

The split is strict:

| step | who | notes |
|------|-----|-------|
| **price** the visit | deterministic arithmetic | `subtotal = Σ base_price` over the line items. **The AI never computes or adjusts the number.** |
| **phrase** the estimate for the patient | Claude (one call) | rewrites the *already-computed* numbers into plain, warm language. Forbidden from changing any figure, adding, or removing a line. |
| **the disclaimer** | fixed compliance text | the actual NSA-mandated language (§7), rendered verbatim — not generated. |

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 05 | **cost-estimate-agent** | deterministic pricing + AI phrasing | **phrasing only** | self-pay patient + procedure codes → a priced, NSA-compliant Good Faith Estimate document |

## 2. The non-negotiable: No Surprises Act gating — self-pay only

> A Good Faith Estimate under the No Surprises Act (45 CFR §149.610) is for
> **uninsured or self-pay** individuals. 05 **refuses to generate** an estimate
> for a patient who has an active insurance coverage on file, or when staff have
> not affirmed the encounter is self-pay.

Enforced structurally:

| # | Mechanism | Where |
|---|-----------|-------|
| E1 | **Two gate conditions, both required.** `POST` must carry `self_pay: true` (an explicit staff affirmation) **and** the patient must have **no active `patient_coverages` row** (checked server-side via 04's data). Either failing → `409`, no estimate row created. | §6.2, §7.1 |
| E2 | **`self_pay_confirmed` is snapshotted onto every `cost_estimates` row.** A query can prove every stored estimate was gated. | §4 |
| E3 | **The number is computed before the AI is called.** `price()` (pure) runs first and its output is passed to `phrase()` as read-only context. The AI response is never parsed for a dollar amount. | §6 |
| E4 | **The disclaimer is a constant, rendered verbatim.** Not model output. Includes the dispute-rights notice and the $400 threshold. | §7 |
| E5 | **05 bills nothing and commits nothing.** It produces a document. A human reviews it and gives it to the patient. | §1 |
| E6 | **Not a Commander agent.** A synchronous action from the appointment view. `commander.py` untouched. | `../PHASE-4.md` |

## 3. Where 05 sits

```
appointment for a SELF-PAY patient
        │
   POST /api/appointments/{id}/cost-estimate   { procedure_codes: [...], self_pay: true }
        │
   GATE:  self_pay == true   AND   no active patient_coverages for this patient
        │   (else 409 — "this patient has active coverage; a GFE is for self-pay individuals")
        │
   1. price()   ── deterministic: look up procedure_prices, sum          [no AI]
   2. phrase()  ── Claude rewrites the computed estimate in plain language  [AI, wording only]
   3. store cost_estimates row (line items, subtotal, disclaimer, patient summary)
        │
   return the estimate
        │
   ── UI: a print-friendly Good Faith Estimate document
        (clinic header · patient · line items + total · plain-language summary · NSA disclaimer)
```

## 4. Data model

New migration `supabase/migrations/2026090X000003_cost_estimates.sql`.

### 4.1 `procedure_prices`

```sql
create table public.procedure_prices (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations (id) on delete cascade,
  procedure_code  text not null,
  description     text not null,
  base_price      numeric(12,2) not null check (base_price >= 0),
  active          boolean not null default true,
  updated_at      timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  unique (organization_id, procedure_code)
);
create index procedure_prices_organization_id_idx on public.procedure_prices (organization_id);

select public.enable_tenant_isolation('public.procedure_prices');
revoke all on public.procedure_prices from anon, authenticated;
grant  select on public.procedure_prices to authenticated;
```

Flat pricing only — one `base_price` per code. Insurance-adjusted / contract
pricing is explicitly a **future refinement**, not Phase 4.

### 4.2 `cost_estimates`

```sql
create table public.cost_estimates (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  appointment_id     uuid references public.appointments (id) on delete set null,
  patient_name       text not null,
  patient_dob        date,
  line_items         jsonb not null default '[]'::jsonb,   -- [{procedure_code, description, base_price}]
  subtotal           numeric(12,2) not null,               -- Σ base_price — computed, never from the model
  currency           text not null default 'USD',
  patient_summary    text not null default '',             -- the AI plain-language paragraph
  disclaimer_text    text not null,                        -- the NSA disclaimer, snapshotted verbatim
  disclaimer_version text not null,                        -- e.g. "nsa-gfe-2026-01" — which constant was used
  self_pay_confirmed boolean not null,                     -- E2 — always true for a stored row
  model              text,                                 -- which model phrased it (null if phrasing skipped)
  generated_by       uuid references auth.users (id) on delete set null,
  created_at         timestamptz not null default now()
);
create index cost_estimates_organization_id_idx on public.cost_estimates (organization_id);
create index cost_estimates_appointment_id_idx  on public.cost_estimates (appointment_id, created_at);

select public.enable_tenant_isolation('public.cost_estimates');
revoke all on public.cost_estimates from anon, authenticated;
grant  select on public.cost_estimates to authenticated;
```

## 5. Settings

```python
# app/config.py
cost_estimate_model: str = "claude-opus-5"   # override COST_ESTIMATE_MODEL
```

## 6. The contract

### 6.1 Interfaces

```python
# backend/app/agents/cost_estimate.py
from dataclasses import dataclass

class PhrasingUnavailable(RuntimeError):
    """The phrasing model could not be reached — the estimate is still valid
    (numbers + disclaimer); patient_summary falls back to a plain template."""

@dataclass(frozen=True)
class EstimateLine:
    procedure_code: str
    description: str
    base_price: float

@dataclass(frozen=True)
class PricedEstimate:
    lines: list[EstimateLine]
    subtotal: float
    unpriced_codes: list[str]     # codes with no active procedure_prices row

def price(procedure_codes: list[str], price_rows: list[dict]) -> PricedEstimate:
    """Pure. Deterministic arithmetic. No AI, no I/O."""

async def phrase(estimate: PricedEstimate, *, patient_name: str, clinic_name: str) -> str:
    """One Claude call. Rewrites the COMPUTED estimate in plain language.
    Never returns a number the caller didn't pass in. Raises PhrasingUnavailable
    on API error — the caller then uses _plain_template(estimate)."""
```

### 6.2 The gate (in the endpoint, before anything else)

```
if not body.self_pay:                     -> 409 "confirm this is a self-pay encounter to generate a GFE"
active = active patient_coverages for (patient_name, patient_dob)   # 04's table
if active:                                -> 409 "this patient has active coverage (<payer>); a Good Faith
                                                   Estimate under the No Surprises Act is for uninsured /
                                                   self-pay individuals"
```

### 6.3 `price()` — deterministic

```
for each code:
    row = active price row for (org, code)
    if row: lines.append(EstimateLine(code, row.description, row.base_price))
    else:   unpriced_codes.append(code)
subtotal = round(sum(l.base_price for l in lines), 2)
```

`unpriced_codes` is surfaced to staff ("no price on file for 99213 — add it in
Settings") and the code is **excluded** from the subtotal — 05 never invents a
price.

### 6.4 `phrase()` — AI, wording only

`anthropic.AsyncAnthropic(...).messages.create(model=settings.cost_estimate_model,
...)`. System prompt (verbatim intent):

> You write the plain-language summary paragraph of a medical Good Faith Estimate
> for a self-pay patient. You are given the clinic name, the patient's name, an
> itemised list of services with prices, and a computed total.
>
> Rules:
> - Use **only** the numbers given. Do not change, round, recompute, add, or
>   remove any figure or line item. The total is fixed.
> - Do not mention insurance, coverage, copays, or deductibles — this patient is
>   self-pay.
> - Do not give medical advice or speculate about additional services.
> - Two short paragraphs, warm and clear, at a 6th–8th-grade reading level: what
>   the visit is estimated to cost and that it's an estimate, not a bill.
> - Do not restate the legal disclaimer — that is added separately.

If `phrase()` raises, the estimate is still produced with a deterministic
`_plain_template(estimate)` summary ("Based on the services planned for your
visit, your estimated cost is $X. This is an estimate, not a bill…").

## 7. The disclaimer — a versioned constant (E4)

`backend/app/agents/cost_estimate.py` holds `NSA_GFE_DISCLAIMER` +
`NSA_GFE_DISCLAIMER_VERSION = "nsa-gfe-2026-01"`. Rendered verbatim on every
estimate and snapshotted into `cost_estimates.disclaimer_text`. Faithful to the
CMS model language — **legal review required before production** (noted in the
spec and in a code comment). It covers:

- what a Good Faith Estimate is and that it's based on information known today;
- that it does not include unknown or unexpected costs that may arise during
  treatment;
- the **right to dispute the bill** if billed **$400 or more** over the estimate,
  and how (the federal patient–provider dispute resolution process, within 120
  days of the bill);
- that it is not a contract and does not require the patient to get the services;
- to keep a copy.

The spec ships a full draft of this text; it is a **real compliance document**,
so the estimate screen and the stored `disclaimer_text` use that exact wording,
not generic copy.

## 8. Backend + UI

### 8.1 Endpoints (`backend/app/routers/cost_estimates.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/appointments/{id}/cost-estimate` | body `{procedure_codes: [...], self_pay: true}`. Runs the gate (§6.2), `price()`, `phrase()`, stores the row, returns it. |
| `GET` | `/api/cost-estimates/{id}` | one estimate (for the print view) |
| `GET` | `/api/appointments/{id}/cost-estimate` | the latest estimate for an appointment, if any |
| `GET` | `/api/procedure-prices` | the clinic's price list (for the code picker) |

Seed `scripts/seed_procedure_prices.py` — a documented flat price list for the
common codes already used elsewhere (the 70553 / 72148 / 29881 / 99213 / … set
from prior-auth), so estimates have something to price against.

### 8.2 UI

- On **`AppointmentDetailPage`**, a **Cost estimate** section, shown **only when
  the patient has no active coverage** (from the 04 insurance summary) — with a
  "Confirm self-pay & generate estimate" action and a procedure-code picker
  (from `procedure_prices`).
- The estimate itself renders at **`/app/cost-estimates/:id`** as a
  **print-friendly Good Faith Estimate document**: clinic header, "Good Faith
  Estimate" title, date, patient name, the itemised table + total, the AI
  summary paragraph (labelled "In plain language"), then the full
  `NSA_GFE_DISCLAIMER`. A **Print / Save PDF** button (`window.print()` + a
  `@media print` stylesheet). Unpriced codes shown as a staff-only warning, not
  on the patient document.

Types → `frontend/src/lib/types.ts` (`CostEstimate`, `EstimateLine`,
`ProcedurePrice`).

## 9. Test plan

### `tests/cost_estimate_test.py` (pure, no stack, no key)

- **`price()` arithmetic**: over combinations of {known code, unknown code,
  duplicate code, empty list}, assert the subtotal is exactly `Σ base_price` of
  the priced lines, `unpriced_codes` is exactly the unknown set, duplicates are
  each priced, rounding to 2 dp is correct, and it's deterministic.
- **The NSA gate** (the standard the review asked for) — a table-driven
  exhaustive check of the `(self_pay, has_active_coverage)` grid:

  | `self_pay` | active coverage? | expected |
  |-----------|------------------|----------|
  | false | no | **refused** — "confirm self-pay" |
  | false | yes | **refused** |
  | true | yes | **refused** — "patient has active coverage" |
  | true | no | **allowed** |

  Tested against the real gate function with a stub coverage lookup, and asserted
  again end-to-end in the stack test below.
- **`_plain_template()`** produces a sane summary with the right number when
  `phrase()` is unavailable.
- The disclaimer constant is non-empty, contains "$400", "dispute", and "120
  days", and its version string matches `disclaimer_version`.

### `tests/e2e_cost_estimate_test.py` (local stack, no key needed — `phrase()` degrades)

- self-pay patient, priced codes → 201, row stored, `self_pay_confirmed = true`,
  `subtotal` correct, `disclaimer_text` == the constant, org-scoped;
- same patient given an active `patient_coverages` row → `POST` now returns 409,
  **no** new `cost_estimates` row;
- `self_pay: false` → 409;
- an unpriced code → excluded from `subtotal`, reported in the response;
- second org isolation.

### `tests/cost_estimate_live_test.py` (opt-in `--live`, needs the key)

`phrase()` on a fixed priced estimate — assert the returned paragraph contains
the exact subtotal string and **no other dollar figure**, mentions neither
"insurance" nor "copay", and is non-empty.

### `tests/agent_isolation_test.py` (extended)

A cost-estimate generation for Clinic A never reads a Clinic B `procedure_prices`
or `patient_coverages` row; the stored estimate is org-A-scoped.

## 10. Open decisions — resolve at review

1. **The disclaimer wording.** The spec ships a full draft from the CMS model
   language. → needs a **legal review** sign-off before production; the version
   string (`nsa-gfe-2026-01`) lets us bump it later without touching stored
   estimates.
2. **How "self-pay" is determined.** The spec requires **both** an explicit
   `self_pay: true` affirmation **and** no active coverage. → confirm, or relax
   to "no active coverage alone is enough". Recommend keeping both (the
   affirmation is a deliberate staff step, and coverage data can be stale).
3. **Estimate as a route vs. a modal.** Spec uses a dedicated printable route
   (`/app/cost-estimates/:id`). → recommend the **route** — it's a real document
   the patient may want a clean copy of.
4. **Scheduled-services window.** The NSA GFE is meant to cover the primary item
   plus services reasonably expected with it. Phase 4 prices exactly the codes
   staff select. → note as a limitation on the document ("covers the services
   listed; ask us if your visit changes").
5. **Price list seed.** Reuse the prior-auth procedure set with plausible
   self-pay prices; tune at review.
