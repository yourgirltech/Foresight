# 11 — Appeals Agent

_Spec. Written before implementation, per the Phase 1–4 discipline. This
document and the Commander addendum in [`00-commander.md`](00-commander.md) §14
are kept in lockstep with the code._

_Status: **BUILT & PASSING** (2026-09-08). Companion:
[`../PHASE-5.md`](../PHASE-5.md). Code: `backend/app/agents/appeals.py`,
`commander._decide_appeal` (AP1–AP12), `orchestrator.handle_appeal`,
`backend/app/routers/appeals.py`, `frontend/src/components/appeals.tsx` on
`ClaimDetailPage`. Migration `supabase/migrations/20260907000005_appeals.sql`._

---

## 1. What 11 is

The **Appeals Agent** drafts an appeal for a claim the payer has **denied**, so a
biller can review it, approve it, and send it — instead of writing every appeal
letter from scratch.

It **extends the claims pipeline** (Phase 1: 06 → 07 → 08 → 09/10) rather than
sitting beside it as a synchronous tool. Unlike 03/04/05 — which were correctly
synchronous, human-invoked tools with no lifecycle — **11 is a Commander agent**.
It reacts to a real claim lifecycle event (a claim reaching `denied`), advances
its own small state machine behind a human-approval gate, and does a **simulated
submission** — the same shape as 09/10 and as the prior-auth flow (02).

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 11 | **appeals-agent** | drafter + simulated executor | **yes** (drafting only) | denied claim → an appeal letter grounded strictly in evidence already in the system → (human approves) → simulated submission → a deterministic resolution |

## 2. The non-negotiables

### 2.1 Grounded strictly in real evidence — never fabricated (same rule as 07 and 03)

> 11 cites **only** facts already documented in Foresight for that claim: the
> rule-engine issues (06's `claim_issues`), the recorded payer denial reason (if
> we have one), and actions the team already took on the claim (an approved,
> executed recommendation). It **never** invents a diagnosis, a procedure code, a
> date, a policy number, a medical-necessity rationale, or a supporting document
> that does not exist.

Enforced structurally:

| # | Mechanism | Where |
|---|-----------|-------|
| G1 | **`appeal_basis()` is a pure function that runs BEFORE the model.** It collects the citable grounds from the claim's real rows. `draft_appeal()` is called only with that grounds list; it asserts the list is non-empty. | §6.2, §6.4 |
| G2 | **No grounds → no draft.** If `appeal_basis()` finds nothing to cite, the appeal is recorded `insufficient_basis`, the model is never called, and the Commander routes it to a human (AP3). 11 never pads a hollow letter. | §6.2, §14 AP3 |
| G3 | **The prompt carries only real strings.** `draft_appeal()`'s user message is assembled from the grounds and the claim's own fields — no free-form context. The grounding test asserts every substantive string in the prompt appears in the claim's real evidence. | §6.4, §8 |
| G4 | **The model output is prose only.** It is stored as `appeals.letter_text` for a human to edit. Nothing is parsed from it — no amount, no status, no structured field. | §6.4 |

### 2.2 Human-in-the-loop — no appeal is ever sent automatically

> The drafted appeal is a **draft**. A human reads the letter and the cited
> grounds and explicitly approves before `11.submit` runs — through a **dedicated
> endpoint**, not by reusing the `recommendations` table (same reasoning as
> Phase 3's decision 1).

| # | Mechanism | Where |
|---|-----------|-------|
| H1 | **AP6 is the only rule that routes to `11.submit`**, and it requires an `appeal_submission_approved` trigger on an appeal whose `status == "drafted"`. R7/R8's structure, in the appeal family. | §14 AP6, §6.5 |
| H2 | **Dedicated endpoints** — `POST /api/appeals/{id}/approve-submission` / `decline-submission`. No client write to any shared table. | §7.1 |
| H3 | **The orchestrator hard-`raise`s** if `11.submit` is about to run and `appeals.status != "submitting"` or `appeals.reviewed_by is None`. | §6.6 |

### 2.3 The claim is reversed only on a won appeal, only after approval

> The appeal family may write **exactly one** `claims.status` transition:
> `denied → paid`, on a won appeal (AP9), and only after a human approved the
> submission. Every other appeal rule leaves the claim untouched.

| # | Mechanism | Where |
|---|-----------|-------|
| C1 | **AP9 is the only AP rule with a non-`None` `next_status`** (`paid`). Every other AP rule is `next_status: None`. | §14 AP1–AP12 |
| C2 | **The orchestrator hard-`raise`s** on any AP decision whose `next_status` is set to anything other than the exact pair `(reason_code == "appeal_won_claim_reversed", next_status == "paid")`. | §6.6 |
| C3 | A partial or upheld resolution **does not** change the claim — it routes to 12 (AP10). The claim stays `denied`; a human decides the next move. | §14 AP10 |

### 2.4 Simulated submission (same "real record, no real delivery" pattern as 09/10)

11's `submit()` does a **simulated** send (there is no live payer/portal
integration in Foresight) and writes a real `appeals` row. The payer's answer is
a **deterministic** resolution (§6.5), the appeal analogue of 02's
`simulate_response`. `submit()` is folded into agent 11 — not a new numbered
agent — for the same reason 02 folded in its `submit` (Phase 3 decision 2): the
approval gate is the architectural boundary that matters, not a new agent number
for one function.

### 2.5 Not a change to R1–R20 / E1–E7 / A1–A11

The appeal trigger family and rule block (AP1–AP12) are **disjoint**, dispatched
by a third early branch in `decide()` before R1 — exactly like eligibility
(§12) and prior-auth (§13). `commander_test.py` / `eligibility_commander_test.py`
/ `prior_auth_commander_test.py` still pass unchanged.

---

## 3. Where 11 sits

```
claim reaches  denied   (seed / later payer-sync / a human starting an appeal)
        │
   trigger: claim_denied      → Commander AP2 → route 11-appeals (draft)
        │
   1. appeal_basis()   [pure]  → the citable grounds from this claim's real rows
        │
        ├─ no grounds  → appeals.status = insufficient_basis
        │                trigger: appeal_drafted → AP3 → route 12-escalation
        │                (a human decides: appeal manually, or write off)
        │
        └─ grounds     → draft_appeal()  [Claude, drafting only]
                         appeals.status = drafted, letter_text + grounds stored
                         trigger: appeal_drafted → AP4 → await_human
        │
   ── UI (claim detail): the letter, the cited grounds, Approve & submit / Don't submit
        │
   human approves  → POST /api/appeals/{id}/approve-submission
        │             trigger: appeal_submission_approved → AP6 → route 11-appeals (submit)
        │
   2. submit()  [simulated send] + simulate_resolution() [deterministic]
        │        appeals.status = appeal_approved | appeal_partial | appeal_denied
        │        trigger: appeal_resolution_received
        │
        ├─ approved → AP9  → no_action, next_status = paid   (claim reversed, simulated)
        └─ partial / denied → AP10 → route 12-escalation      (claim stays denied; human decides)
```

No arrow writes `claims.status` except the human-triggered submission that wins
(AP9).

## 4. Data model

New migration `supabase/migrations/2026090X000005_appeals.sql`.

### 4.1 `claims.denial_reason`

```sql
alter table public.claims add column denial_reason text;   -- what the payer said, if recorded

comment on column public.claims.denial_reason is
  'The payer''s stated reason for a denied claim, when we have one (set by the seed / a future payer-sync). One of 11-appeals-agent''s grounding inputs. NULL is allowed — an appeal can still be drafted from the rule-engine issues alone.';
```

### 4.2 Enum

```sql
create type public.appeal_status as enum (
  'pending',              -- row created, 11 has not drafted yet
  'drafted',              -- 11 wrote a grounded letter — awaiting a human's approval to send
  'insufficient_basis',   -- 11 found nothing citable — no letter written, routed to a human
  'submission_declined',  -- a human reviewed the draft and chose not to send it
  'submitting',           -- a human approved; 11.submit is running (simulated)
  'submitted',            -- sent to the (simulated) payer, awaiting their decision
  'appeal_approved',      -- (simulated) payer reversed the denial in full
  'appeal_partial',       -- (simulated) payer reversed part of it
  'appeal_denied',        -- (simulated) payer upheld the denial
  'error'                 -- the drafting model could not be reached (see letter_text = '')
);
```

### 4.3 `appeals`

```sql
create table public.appeals (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  claim_id           uuid not null references public.claims (id) on delete cascade,
  previous_appeal_id uuid references public.appeals (id) on delete set null,   -- second-level chain (append-only)
  denial_reason      text,                                -- snapshot of claims.denial_reason at draft time
  grounds            jsonb not null default '[]'::jsonb,   -- [{source, ref, detail}] — the cited REAL evidence
  has_basis          boolean not null default false,
  letter_text        text not null default '',            -- the drafted appeal letter ('' if insufficient_basis / error)
  status             public.appeal_status not null default 'pending',
  model              text,                                -- which model drafted it (null if not drafted)
  submission_payload jsonb not null default '{}'::jsonb,   -- the simulated submission packet
  resolution_payload jsonb not null default '{}'::jsonb,   -- {outcome, bucket, reversed_amount, responded_at}
  reviewed_by        uuid references auth.users (id) on delete set null,   -- the human who approved/declined the send
  reviewed_at        timestamptz,
  resolved_at        timestamptz,
  created_at         timestamptz not null default now()
);

create index appeals_organization_id_idx on public.appeals (organization_id);
create index appeals_claim_id_idx        on public.appeals (claim_id, created_at);
create index appeals_status_idx          on public.appeals (organization_id, status);

comment on table public.appeals is
  'One appeal attempt against a denied claim (Phase 5). Sidecar: claims has no FK to appeals, a denial can exist with no appeal. The letter is grounded strictly in this claim''s real rows (11-appeals-agent §2.1); it is never sent without a human approving through POST /api/appeals/{id}/approve-submission. A won appeal is the only thing that transitions claims.status (denied -> paid). Tenant-scoped.';

select public.enable_tenant_isolation('public.appeals');
revoke all on public.appeals from anon, authenticated;
grant  select on public.appeals to authenticated;   -- UI reads; all writes backend-mediated
```

**Why its own table** (open decision §9.4, answered): an appeal is a multi-step
sidecar with its own status machine and an append-only chain — structurally
identical to `eligibility_checks` (Phase 2) and `prior_authorizations`
(Phase 3), both of which got their own table for exactly this shape.
`recommendations` is a single-shot "here is an action, approve or decline";
`follow_ups` is a one-row send log. Neither models draft → approve → submit →
resolution, and neither has a place for the letter, the grounds, the denial
reason, and the resolution payload.

`activity_log` and `escalations` linkage: appeals are **claim-scoped**, so they
reuse `activity_log.claim_id` / `escalations.claim_id` (with `context.appeal_id`
in the escalation row). No new FK column — unlike eligibility / prior-auth, which
are appointment-scoped and needed one (§9.7).

## 5. Settings

```python
# app/config.py
appeals_model: str = "claude-opus-5"   # override APPEALS_MODEL
```

Simulation constants live in `backend/app/agents/appeals.py` as **named module
constants** (Phase 4 discipline — deterministic knobs are never inlined):

```python
APPEAL_BASE_WIN_THRESHOLD = 40   # base of the "approved" band, before the grounds bonus
APPEAL_GROUNDS_BONUS      = 12   # added to the threshold per DISTINCT ground cited
APPEAL_MAX_WIN_THRESHOLD  = 82   # cap on the approved band
APPEAL_PARTIAL_BAND       = 16   # [win, win + this) -> partial; >= win + this -> denied
APPEAL_RESUBMIT_BONUS     = 15   # a second-level appeal (added documentation) shifts the bucket down
```

## 6. The contract

### 6.1 Interfaces

```python
# backend/app/agents/appeals.py
from dataclasses import dataclass

class AppealsUnavailable(RuntimeError):
    """The drafting model could not be reached (missing key, API error).
    The orchestrator records appeals.status = 'error' and emits appeal_error."""

@dataclass(frozen=True)
class AppealGround:
    source: str    # "rule_engine_issue" | "payer_denial_reason" | "action_taken"
    ref: str | None  # issue_type / action_type / None
    detail: str    # the human-readable fact, verbatim from the source row

@dataclass(frozen=True)
class AppealBasis:
    grounds: list[AppealGround]
    has_basis: bool
    distinct_grounds: int   # distinct (source, ref) keys — drives the win threshold

@dataclass(frozen=True)
class AppealResolution:
    outcome: str          # "approved" | "partial" | "denied"
    bucket: int            # 0..99, the deterministic roll
    reversed_amount: float # full claim amount / a deterministic fraction / 0.0
    payload: dict

def appeal_basis(claim: dict, issues: list[dict], recommendations: list[dict],
                 follow_ups: list[dict], denial_reason: str | None) -> AppealBasis:
    """Pure. Collects the citable grounds from this claim's real rows."""

async def draft_appeal(claim: dict, payer: dict, basis: AppealBasis) -> tuple[str, str]:
    """One Claude call — drafting only. Returns (letter_text, model).
    asserts basis.grounds is non-empty. Raises AppealsUnavailable on API error."""

def simulate_resolution(claim: dict, appeal_id: str, basis: AppealBasis,
                        *, is_resubmit: bool = False) -> AppealResolution:
    """Pure, deterministic. The appeal analogue of 02.simulate_response."""
```

### 6.2 `appeal_basis()` — pure (G1/G2)

```
grounds = []

# a) rule-engine findings — each documented issue is a citable ground
for issue in issues:
    grounds.append(AppealGround("rule_engine_issue", issue["issue_type"], issue["description"]))

# b) the payer's stated denial reason, if we have one
if denial_reason and denial_reason.strip():
    grounds.append(AppealGround("payer_denial_reason", None, denial_reason.strip()))

# c) actions the team already took (an approved recommendation that executed)
for rec in recommendations:
    if rec["approval_status"] == "approved" and _was_executed(rec, follow_ups, claim):
        grounds.append(AppealGround("action_taken", rec["action_type"],
                                    f"{rec['action_type']} was approved and completed on this claim"))

has_basis = len(grounds) >= 1
distinct  = len({(g.source, g.ref) for g in grounds})
```

`has_basis == False` ⟹ `insufficient_basis` (AP3). A claim that was denied but
has zero issues on file, no recorded denial reason, and no action history has
nothing 11 can honestly cite.

### 6.3 `draft_appeal()` — Claude, drafting only

`anthropic.AsyncAnthropic(...).messages.create(model=settings.appeals_model, ...)`.
System prompt (verbatim intent — the same discipline as 07's):

> You draft a health-insurance claim appeal letter for a medical billing team.
> You are given a claim, the payer, the payer's denial reason (if recorded), and
> a list of GROUNDS — each a specific fact already documented in our system (a
> rule-engine finding, the recorded denial reason, or an action our team already
> took on this claim).
>
> Strict rules:
> - Ground every sentence in the GROUNDS and the claim fields provided. Do NOT
>   invent a diagnosis, a procedure or diagnosis code, a date, a policy or
>   authorization number, a medical-necessity rationale, or any supporting
>   document that is not in the input.
> - If the grounds are thin, write a short, honest letter that states only what
>   is documented. Do not pad it with unsupported assertions.
> - Do not state that documentation is attached unless a ground says it is.
> - This is a DRAFT for a biller to review, edit, and send. No signature block,
>   no specific staff name, no letterhead.
> - Plain, professional, specific. Reference the claim id and the payer by name.
> - Respond with ONLY the letter body.

The user message is assembled from `basis.grounds`, `claim["claim_id"]`,
`claim["patient_name"]`, `claim["amount"]`, `payer["name"]`, and
`basis` — nothing else. `draft_appeal()` asserts `basis.grounds` is non-empty
before the call (G1).

**No fallback template.** If the call raises, `AppealsUnavailable` propagates;
the orchestrator records `appeals.status = 'error'`, `letter_text = ''`, and
emits `appeal_error` → AP11 → 12. This matches 07 exactly (a hollow appeal is
worse than none — §9.6).

### 6.4 `simulate_resolution()` — pure, deterministic (§2.4)

```
bucket = int(sha256(f"{claim_id}|{appeal_id}").hexdigest()[:8], 16) % 100
win    = min(APPEAL_MAX_WIN_THRESHOLD,
             APPEAL_BASE_WIN_THRESHOLD + APPEAL_GROUNDS_BONUS * basis.distinct_grounds)
eff    = max(0, bucket - APPEAL_RESUBMIT_BONUS) if is_resubmit else bucket

eff < win                     -> "approved",  reversed_amount = claim.amount
win <= eff < win + PARTIAL_BAND -> "partial",  reversed_amount = round(claim.amount * pct, 2)
eff >= win + PARTIAL_BAND      -> "denied",    reversed_amount = 0.0

pct = 0.35 + (bucket % 46) / 100          # deterministic 0.35 .. 0.80
```

The three bands partition `0..99` exactly. A stronger appeal (more distinct
grounds) widens the `approved` band; a resubmit shifts the effective bucket down
by `APPEAL_RESUBMIT_BONUS`.

### 6.5 The state machine

| appeal.status | set by | next |
|---------------|--------|------|
| `pending` | the orchestrator, on `claim_denied` / `appeal_resubmitted`, before routing to 11 | 11 runs `appeal_basis` then `draft_appeal` |
| `drafted` | 11, when `has_basis` and the letter is written | `appeal_drafted` → AP4 → `await_human` |
| `insufficient_basis` | 11, when `not has_basis` | `appeal_drafted` → AP3 → route 12 |
| `error` | orchestrator, on `AppealsUnavailable` | `appeal_error` → AP11 → route 12 |
| `submission_declined` | the decline endpoint | `appeal_submission_declined` → AP8 → `no_action` |
| `submitting` | the approve endpoint (with `reviewed_by`), then 11.submit starts | 11 runs `submit` + `simulate_resolution` |
| `submitted` | 11, transiently, before the resolution is written | — |
| `appeal_approved` / `appeal_partial` / `appeal_denied` | 11, from `simulate_resolution` | `appeal_resolution_received` → AP9 / AP10 |

### 6.6 The orchestrator (`handle_appeal`)

`backend/app/agents/orchestrator.py` gains `handle_appeal(claim_id, trigger)`, a
mirror of `handle_prior_auth`:

1. Load the claim (resolves `organization_id` once, like `db.get_claim`), its
   `claim_issues`, its `recommendations`, its `follow_ups`, and the latest
   `appeals` row for the claim. Assemble the appeal-shaped `state` (§14.4).
2. `decision = commander.decide(state, trigger)` → dispatches to `_decide_appeal`.
3. Append to `activity_log` (`actor = "00-commander"`, `action =
   decision.reason_code`, `claim_id` set).
4. **Hard-`raise`s** (mirror of the eligibility / prior-auth invariant guards):
   - `decision.next_status is not None` **and not** (`decision.reason_code ==
     "appeal_won_claim_reversed"` **and** `decision.next_status == "paid"`) — C2
   - `decision.action == "route"` **and** `decision.route_to == "11-appeals"`
     **and** the resolved intent is `appeal_submit` **and**
     (`appeal.status != "submitting"` **or** `appeal.reviewed_by is None`) — H3
5. If `decision.next_status` set → `UPDATE claims SET status = 'paid'` (scoped).
6. If `decision.action == "route"` to `11-appeals` → run the drafting or the
   submission step; on success emit the follow-on trigger (`appeal_drafted` /
   `appeal_resolution_received`) and call `handle_appeal` again. On
   `AppealsUnavailable` → record `error`, emit `appeal_error`.
7. `route` to `12-escalation` → 12 writes the escalation row
   (`context.appeal_id`), the run ends. No follow-on.
8. `await_human` / `no_action` → return.

`draft_appeal` invoked with an empty grounds list → the agent raises before any
API call (G1).

## 7. Backend + UI

### 7.1 Endpoints (`backend/app/routers/appeals.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/claims/{claim_id}/appeal` | the latest appeal for the claim + the chain + the appeal-scoped activity |
| `POST` | `/api/claims/{claim_id}/appeal` | **manually start** an appeal on a `denied` claim (for claims denied before this feature, or where auto-draft is off). Creates the `pending` row, emits `claim_denied`. 409 if the claim is not `denied` or an active appeal exists. |
| `POST` | `/api/appeals/{id}/approve-submission` | a human approves sending the drafted letter. Sets `reviewed_by` / `reviewed_at` / `status = 'submitting'` under the caller's RLS scope, then emits `appeal_submission_approved`. 409 unless `status == 'drafted'`. |
| `POST` | `/api/appeals/{id}/decline-submission` | a human declines. `status = 'submission_declined'`, emits `appeal_submission_declined`. |
| `POST` | `/api/appeals/{id}/resubmit` | a **second-level** appeal — appends a new `pending` `appeals` row with `previous_appeal_id`, emits `appeal_resubmitted`. Body: optional `added_context` (free text the biller adds — stored, and passed to `draft_appeal` as an `action_taken`-style ground *only if the biller affirms the documentation exists*). |

`GET /api/claims/{claim_id}` (the claim detail endpoint) is **extended** to
include `appeal` (the latest appeal + its chain), the way `GET
/api/appointments/{id}` was extended with `coverages` + `cob` in Phase 4.

### 7.2 UI

On **`ClaimDetailPage`**, an **Appeal** section, shown when `claim.status ==
"denied"` **or** an `appeals` row exists:

- **`insufficient_basis`** — "No documented basis for an automated appeal." The
  grounds we *do* have (if any) are listed. A link to the escalation. A note:
  "review the denial and appeal manually if warranted."
- **`drafted`** — the `letter_text` in a monospace/reading block; the cited
  **grounds**, each labelled by source ("Rule-engine finding: missing
  authorization" / "Payer denial reason: …" / "Action taken: documentation
  requested"); **Approve & submit** / **Don't submit** buttons.
- **`submitting` / `submitted`** — "Appeal submitted — awaiting the payer's
  decision" (in the demo the resolution is produced synchronously, so this is
  usually transient).
- **resolved** — the outcome badge (`Won — claim reversed` / `Partly reversed` /
  `Upheld`), the `reversed_amount`, and for `partial` / `denied` a link to the
  escalation plus an **Appeal again (add documentation)** button (→ `resubmit`).
- the **chain** (attempt #1, #2, …) when `previous_appeal_id` links more than one.

Types → `frontend/src/lib/types.ts` (`Appeal`, `AppealStatus`, `AppealGround`,
`AppealResolutionOutcome`, `AppealDetail`). Badge + section →
`frontend/src/components/appeals.tsx`.

## 8. Test plan

Same standard as Phases 1–4.

### `tests/appeals_agent_test.py` (pure, no stack, no key)

- **`appeal_basis()` grounding grid.** Over synthetic claims built from the
  cross-product of `{0, 1, 3} issues × {denial_reason present / absent} ×
  {0, 1 approved-and-executed recommendation}`:
  - every `AppealGround.detail` / `.ref` appears verbatim in the input row it
    came from — **never a string not in the input**;
  - `has_basis` is `True` iff there is ≥ 1 issue **or** a denial reason **or** an
    executed action;
  - `distinct_grounds` counts distinct `(source, ref)` keys (three
    `missing_documentation` issues → one distinct ground);
  - deterministic.
- **`simulate_resolution()`.** Full sweep of `bucket ∈ 0..99` for representative
  `distinct_grounds ∈ {0..4}` and `is_resubmit ∈ {False, True}`:
  - the three bands partition `0..99` exactly (no gap, no overlap);
  - more `distinct_grounds` ⟹ the `approved` band is ≥ as wide (monotonic);
  - a resubmit's effective bucket is `max(0, bucket - APPEAL_RESUBMIT_BONUS)`;
  - `reversed_amount` is `claim.amount` for `approved`, `0.0` for `denied`, and
    `0 < x < claim.amount` for `partial`;
  - deterministic (same inputs → same `AppealResolution`, twice).
- **Named cases**: `test_no_issues_no_denial_reason_no_history_has_no_basis`,
  `test_denied_for_missing_docs_after_docs_were_provided_is_a_strong_appeal`,
  `test_a_single_thin_ground_still_drafts_but_wins_less_often`.

### `tests/appeals_commander_test.py` (pure, no stack, no key)

- one case per **AP1–AP12**;
- a re-run of representative **R1–R20 / E1–E7 / A1–A11** cases to prove the three
  existing rule blocks are unchanged;
- **the appeal-invariant fuzz**: over `trigger × appeal.status ×
  appeal.resolution × {appeal present / absent}`, assert on every result:
  - determinism (`decide` twice → identical);
  - `route_to == "11-appeals"` **with** `reason_code == "appeal_submit"` ⟹
    `trigger.type == "appeal_submission_approved"` **and** `appeal.status ==
    "drafted"` (H1);
  - `next_status is not None` ⟹ `reason_code == "appeal_won_claim_reversed"`
    **and** `next_status == "paid"` **and** `trigger.type ==
    "appeal_resolution_received"` **and** `appeal.resolution == "approved"` (C1);
  - `next_status in {None, "paid"}` — nothing else is ever written;
- named `test_appeal_never_submits_without_a_recorded_approval`,
  `test_appeal_touches_claims_status_only_on_a_win`.

### `tests/appeals_live_test.py` (opt-in `--live`, needs the key)

`draft_appeal()` on a fixed claim with known issues (`missing_authorization` +
`code_mismatch`) and a recorded denial reason:

- the letter references the claim id and the payer name;
- it mentions the actual issue types in the grounds;
- it mentions **no** issue type, code, date, or document that is **not** in the
  input (a curated deny-list of plausible fabrications is checked absent);
- it is non-empty and contains no signature block.

### `tests/e2e_appeal_test.py` (local stack; key for the draft step — degrades)

- a `denied` claim with a `denial_reason` and issues → `claim_denied` → (key:
  `drafted`; no key: `error` → escalation);
- approve-submission → `11.submit` → a resolution is written; **won** → claim
  `paid` + an `appeal_won_claim_reversed` activity row; **partial/denied** →
  claim still `denied` + exactly one escalation (`appeal_exhausted_needs_human`),
  org-scoped;
- `approve-submission` on an appeal that is not `drafted` → 409, no submit;
- a `claim_denied` for a claim with no issues and no denial reason →
  `insufficient_basis` → one escalation (`appeal_no_basis_needs_human`), no
  model call, no letter;
- a second-level `resubmit` chain: `appeal_denied` → resubmit (with affirmed
  added documentation) → the chained attempt wins (the resubmit bonus), claim
  `paid`;
- second org: nothing leaks; the reversal writes only Clinic A's claim.

### `tests/agent_isolation_test.py` (extended)

An appeal run over Clinic A's denied claim never reads a Clinic B `claim_issues`
/ `recommendations` / `appeals` row; the `denied → paid` reversal writes only
A's claim; every `appeals` / `activity_log` / `escalations` row it writes carries
`organization_id == A`.

### `scripts/seed_appeals.py`

Reuses the two seed clinics. Creates **denied** claims (with recorded
`denial_reason` strings across the grounds spectrum — some with issues, some
denial-reason-only, one bare), then drives:

- a few left at `drafted` (a realistic "awaiting your approval" queue);
- a few auto-approved → the deterministic won / partial / upheld distribution;
- one `insufficient_basis` → escalation;
- one `appeal_denied` → `resubmit` → won.

Prints the resolution distribution (approved / partial / denied) like
`seed_prior_auth.py`. Needs `ANTHROPIC_API_KEY` for the draft step (the pure
parts run without it — same as `seed_claims.py`).

### `scripts/run_appeals_proof.sh`

Pure Commander + agent tests (no stack), then the stack tests against a fresh
`supabase db reset`, then the `--live` draft test, then the seed.

## 9. Open decisions — resolve at review

1. **AP block: a disjoint family vs. extending R1–R20.** → recommend **disjoint
   family**, dispatched before R1 like E1–E7 / A1–A11. `commander_test.py` stays
   byte-for-byte. Unlike the eligibility / prior-auth blocks it *is* permitted a
   `next_status` — but exactly one value (`paid`), on exactly one rule (AP9),
   behind the human-approval gate. Extending R1–R20 would mean relaxing R1
   (`denied` no longer terminal) and rewriting `commander_test.py` — a cost with
   no benefit.
2. **`insufficient_basis` handling (AP3).** → recommend **route to
   12-escalation** (`appeal_no_basis_needs_human`). A denial is always money at
   risk; "we cannot auto-draft" is precisely the "a human must decide what's
   next" case 12 exists for (the same shape as A10 for a denied PA and R10 for a
   manual action). Every denied claim then gets a visible disposition — a draft
   on the approval queue (AP4) or an escalation (AP3) — nothing drops silently.
   Alternative: a soft record surfaced only on the claim UI.
3. **Won-appeal claim status (AP9).** → recommend **reuse `paid`** — the honest
   simulation of "the payer will now pay". The `appeals` row and the
   `appeal_won_claim_reversed` `activity_log` action disambiguate "won on appeal"
   from "paid normally" for any report. Alternative: add an `appeal_won` value to
   the `claim_status` enum for cleaner reporting.
4. **Own `appeals` table** vs. reuse `recommendations` / `follow_ups`. →
   **own table** (answered in §4.3 with reasoning — same shape as
   `eligibility_checks` / `prior_authorizations`).
5. **Auto-draft on every denial vs. draft on staff request.** → recommend
   **auto-draft** (matches the `claim_denied` trigger model; the human still
   gates the *send*). Alternative: `claim_denied` only records the denial;
   staff click "Draft an appeal" to fire AP2.
6. **No-key behaviour for `draft_appeal`.** → recommend **`AppealsUnavailable` →
   `error` → AP11 → 12**, no template (matches 07). Alternative: a grounds-only
   structured skeleton (matches 05's degradation) — rejected, because a skeleton
   labelled "draft" risks being sent as-is.
7. **`escalations` / `activity_log` linkage.** → recommend **reuse `claim_id`**
   (appeals are claim-scoped) + `context.appeal_id`. No new FK column.
   Alternative: add `appeals` FK columns for symmetry with
   `prior_authorization_id`.
8. **`denial_reason` capture.** A new nullable `claims.denial_reason` column, set
   by the seed / a future payer-sync. Confirm (vs. a `claim_denials` table with
   codes + free text).
9. **Agent number 11.** The Phase 1 roster (`00-commander.md` §2) pencilled 11 in
   for "patient comms / voice". Phase 5 assigns it to appeals; patient comms
   takes a later number if it is ever built. Confirm.
10. **Second-level appeal depth.** `previous_appeal_id` is an unbounded chain. →
    recommend **leave it open** (a human drives every resubmit; a real
    internal→external-review cap is a payer-specific rule for later).
11. **`claim_denied` emission.** In the demo the seed sets `denied` and then
    calls `orchestrator.handle_appeal(claim_id, {"type": "claim_denied"})`. A
    future payer-sync does the same. Confirm this is the emission point (vs. a
    database trigger on the status change).
