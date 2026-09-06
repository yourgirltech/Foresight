# 00 — Commander

_Spec. Written before implementation, per the Phase 1 plan. Cross-check this
document before any Commander code is written._

_Status: **IMPLEMENTED** (2026-09-02). Five open questions (§11) resolved and
incorporated — including `resubmit_corrected_coding` as a manual-only action
(§2.1, §6.3.1). Code: `backend/app/agents/commander.py`; tests:
`tests/commander_test.py` (one case per rule + determinism/invariant fuzz).
One refinement during implementation: R19 now also requires
`claim.status == executing` so a stray `execution.completed` from another state
falls through to R20 (escalate) instead of being swallowed._

_**Phase 2 addendum (§12): IMPLEMENTED** (2026-09-06). The eligibility trigger
family and rule block (E1–E7) — `commander._decide_eligibility`, dispatched from
`decide()` before R1. Care-safety invariant (§12.3) additionally hard-enforced in
`orchestrator.handle_eligibility` and fuzzed in
`tests/eligibility_commander_test.py`. §12 does not modify R1–R20 —
`commander_test.py` still passes unchanged. Full detail:
[`01-eligibility-agent.md`](01-eligibility-agent.md)._

_**Phase 3 addendum (§13): IMPLEMENTED** (2026-09-07). The prior-authorization
trigger family and rule block (A1–A11) — `commander._decide_prior_auth`,
dispatched from `decide()` after the eligibility check and before R1. Care-safety
invariant (§13.3) hard-enforced in `orchestrator.handle_prior_auth` (three
`raise`s: non-None `next_status`, emergency→12, emergency→`await_human`) and
fuzzed over 23,328 states in `tests/prior_auth_commander_test.py`. §13 does not
modify R1–R20 or E1–E7 — `commander_test.py` / `eligibility_commander_test.py`
still pass unchanged. Full detail: [`02-prior-auth-agent.md`](02-prior-auth-agent.md)._

---

## 1. What the Commander is

The Commander is the **single decision node** of the Foresight agent system. It is
the first real agent implemented in this project.

It is a **pure function**:

```
decide(state, trigger) -> CommanderDecision
```

- **No tools.** It calls nothing, fetches nothing, writes nothing.
- **No content generation.** It never calls an LLM. It contains no prose,
  no templates.
- **Deterministic.** Same `(state, trigger)` in → same `CommanderDecision` out,
  every time. Fully unit-testable with no mocks.
- **Ordered rule table.** The decision is the first matching row of a fixed,
  top-to-bottom rule list (§6). Terminal and safety guards sit at the top.

Everything else — loading the claim, running the routed agent, writing status,
appending to `activity_log` — is done by the **orchestrator** (§7), not the
Commander. The Commander only decides.

### 1.1 Why it exists

The system-wide invariant (see `architecture.md` §1.2):

> AI recommends or drafts. A human approves anything consequential. Automation
> executes only *after* approval.

The Commander is where that invariant is **structurally enforced**. There is
exactly **one** rule that routes to an execution agent (§6, **R9** → 09 / 10),
it sits behind two guard rules that reject any approval not backed by an
`approved`, non-low-confidence recommendation (**R7**, **R8**), and the single
most consequential action — resubmitting a claim to a payer — has *no*
agent-execution path at all (**R10**, §6.3.1). If the Commander is correct, no
claim-consequential action fires without a recorded human approval, and a claim
resubmission never fires from automation — regardless of bugs elsewhere.

---

> **Phase numbering:** the multi-tenant foundation is **Phase 0**
> (`docs/PHASE-0.md`). This claims & billing module is **Phase 1**
> (`docs/PHASE-1.md`). Earlier drafts of the repo labelled the foundation
> "Phase 1"; those references are being renumbered as part of this phase.

## 2. Agent roster (Phase 1)

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 00 | **commander** | decision node | no | route one `(state, trigger)` to the next step |
| 06 | analyzer-agent | deterministic rule engine | no | find issues on a claim, score risk |
| 07 | reasoning-agent | explainer | **yes** | plain-language write-up of the issue list |
| 08 | recommendation-agent | recommender | no | issue list → `action_type` + confidence band + `low_confidence` |
| 09 | followup-agent | executor | no | carry out an approved *follow-up* action (simulated send + logged record) |
| 10 | reminder-agent | executor | no | carry out an approved *payer-reminder* action (simulated send + logged record) |
| 12 | escalation-agent | safety net | no | log full context, flag for a human, stop — for **both** errors/unrecognised states **and** approved actions that a human must perform (§6.3.1) |

Numbers 02–05 and 11 are intentionally unassigned — reserved for later phases
(prior-auth, patient comms, …). The Commander never routes to them; if a trigger
implies one, it falls through to escalation (R20). **01 (eligibility) is assigned
in Phase 2 — see §12**; the claims pipeline still never routes to it.

### 2.1 Two kinds of "execution"

Not every approved action is executed by an agent. Two tiers:

- **Agent-executed after approval** — 09 / 10. Lower-stakes administrative
  actions: chasing internal documentation, filing an authorization request,
  setting a payer status-check reminder. The agent does a **simulated** send and
  writes a real logged record (Phase 1 has no live payer integration).
- **Human-executed after approval** — `resubmit_corrected_coding`. Resubmitting
  a claim to a payer with changed codes has direct billing and compliance
  consequences. The AI still recommends it and a human still approves it through
  the **identical** approval gate (§6.5 → §6.3), but **no agent carries it out**.
  On approval the Commander hands the fully-approved context to 12, which records
  it and opens a task for a person. See §6.3.1.

Both tiers pass through `awaiting_approval` → `human.approved`. The only
difference is what happens *after* approval.

---

## 3. The state the Commander reads

The orchestrator assembles a plain dict and hands it in. The Commander treats it
as read-only. Every piece is scoped to **one** `organization_id` (the claim's);
see §8.

```python
state = {
  "claim": {
    "id":                     "<uuid>",
    "claim_id":               "CLM-...",          # human-facing id
    "organization_id":        "<uuid>",
    "status":                 "received",         # §4
    "risk_score":             0,                  # 0..100, written by 06
    "risk_level":             "Low",              # Low|Medium|High, written by 06
    "authorization_present":  False,
    "documentation_present":  True,
    "coding_matches":         True,
    "last_followup_at":       None,               # timestamptz | None
    "created_at":             "<timestamptz>",
  },
  "payer": {
    "id":                       "<uuid>",
    "authorization_required":   True,
    "documentation_required":   True,
    "follow_up_threshold_days": 14,               # int | None
  },
  "issues": [                                     # rows from claim_issues, may be []
    {"issue_type": "missing_authorization", "severity": "high",   "description": "...", "evidence": {...}},
    {"issue_type": "code_mismatch",         "severity": "medium", "description": "...", "evidence": {...}},
  ],
  "recommendation": {                             # newest row from recommendations, or None
    "action_type":     "submit_authorization_request",
    "confidence":      "High",                    # High|Medium|Low
    "low_confidence":  False,
    "approval_status": "approved",                # pending|approved|declined
    "decided_at":      "<timestamptz>",           # set iff approved/declined
  },
}
```

The Commander uses only these fields. It does **not** re-derive issues or risk —
that is 06's job. It does **not** decide `action_type` — that is 08's job.

---

## 4. Claim status taxonomy

`claims.status` is a Postgres enum. It is the claim's position in the Foresight
workflow — **not** the payer's adjudication state (those are the three external
terminals).

| status | meaning | set by |
|--------|---------|--------|
| `received` | ingested, not yet analyzed | seed / ingest |
| `analyzed` | 06 ran, ≥1 issue found | orchestrator after R17 |
| `cleared` | 06 ran, **no** issues — nothing to do | orchestrator after R16 |
| `reasoned` | 07 ran, explanation stored | orchestrator after R18 |
| `awaiting_approval` | 08 ran, a recommendation is pending a human | orchestrator after R14 |
| `executing` | an execution agent (09/10) is running | orchestrator after R9 |
| `actioned` | agent execution completed successfully | orchestrator after R19 |
| `manual_action_required` | recommendation approved, but the action itself must be performed by a human (e.g. `resubmit_corrected_coding`) — 12 has logged it and opened the task | orchestrator after R10 |
| `declined` | a human declined the recommendation | orchestrator after R12 |
| `escalated` | routed to 12 because of an error / unrecognised state; a human now owns it | orchestrator after R5/R6/R7/R8/R11/R13/R20 |
| `denied` | **external:** payer denied the claim | seed / later payer-sync |
| `paid` | **external:** payer paid the claim | seed / later payer-sync |
| `rejected` | **external:** clearinghouse/payer rejected pre-adjudication | seed / later payer-sync |

`denied` / `paid` / `rejected` / `escalated` / `actioned` / `manual_action_required`
are **terminal for the Commander** — it will not drive a claim out of them
(R1–R3). `declined` is a soft stop: no automation, but a human may re-open it
via `claim.reanalyze`.

---

## 5. Trigger taxonomy

A trigger is `{"type": <string>, "payload": {...}}`. The Commander branches only
on `type`. `payload` carries context for the orchestrator / `activity_log`, not
for the decision.

| trigger `type` | emitted when | typical source |
|----------------|--------------|----------------|
| `claim.ingested` | a new claim row is created | seed script, later ingest endpoint |
| `claim.reanalyze` | a human asks to re-run analysis | claim detail UI button |
| `analysis.completed` | 06 finished and wrote issues + risk | orchestrator, after running 06 |
| `reasoning.completed` | 07 finished and stored its explanation | orchestrator, after running 07 |
| `recommendation.completed` | 08 finished and wrote a `recommendations` row | orchestrator, after running 08 |
| `human.approved` | a human clicked **Approve** on the recommendation | `POST /api/claims/{id}/approve` |
| `human.declined` | a human clicked **Decline** | `POST /api/claims/{id}/decline` |
| `execution.completed` | 09 or 10 finished its action successfully | orchestrator, after running 09/10 |
| `execution.failed` | 09 or 10 exhausted bounded retries / hit a non-transient error | orchestrator |
| `agent.error` | any agent raised an unhandled exception | orchestrator's try/except |

The human-decision triggers are the only ones that originate from a user request
path. They are minted **after** the approve/decline endpoint has (a) verified the
session and (b) written the `recommendations.approval_status` change under the
caller's own RLS scope. By the time the Commander sees `human.approved`, the
approval is already durably recorded — the Commander re-checks it anyway (R7).

**Phase 2 adds a second trigger family** — `appointment_scheduled`,
`emergency_patient_registered`, `eligibility_check_completed`,
`eligibility_check_failed` — handled by a separate rule block (§12.6, E1–E7)
reached by a dispatch at the top of `decide()`, *before* R1. A claims trigger
never reaches an E-rule; an eligibility trigger never reaches R1–R20.

---

## 6. The rule table

Evaluated **top to bottom. First match wins.** No rule below a match is
considered. If nothing matches, R20 (escalate) fires — the Commander never
"falls off the end" silently.

`CommanderDecision` fields:

- `action` — one of `route` | `await_human` | `no_action`
- `route_to` — agent id string when `action == route`, else `None`
- `reason_code` — stable snake_case string, written to `activity_log.action`
- `next_status` — the `claims.status` the orchestrator should write, or `None`
  to leave it unchanged

Constants — the action-type routing map. Every `action_type` 08 can emit appears
in exactly one of these; anything else is unrecognised and escalates (R11).

```python
# action_type -> the agent that executes it, after human approval
EXECUTABLE_ACTIONS = {
    "submit_authorization_request": "09-followup",
    "request_documentation":        "09-followup",
    "payer_status_follow_up":        "10-reminder",
}

# action_type -> approved, but NO agent executes it; a human does (§6.3.1)
MANUAL_ACTIONS = {"resubmit_corrected_coding"}
```

### 6.1 Terminal & re-entrancy guards (checked first)

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R1** | `claim.status in {denied, paid, rejected}` | `no_action` | — | `claim_terminal` | — |
| **R2** | `claim.status in {escalated, manual_action_required}` | `no_action` | — | `human_owns_claim` | — |
| **R3** | `claim.status == actioned` | `no_action` | — | `already_actioned` | — |
| **R4** | `claim.status == executing` **and** `trigger.type not in {execution.completed, execution.failed, agent.error}` | `no_action` | — | `execution_in_progress` | — |

### 6.2 Safety guards

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R5** | `trigger.type == agent.error` | `route` | `12-escalation` | `agent_error` | `escalated` |
| **R6** | `trigger.type == execution.failed` | `route` | `12-escalation` | `execution_failed` | `escalated` |

### 6.3 The human-approval hard guard

**R9 is the only rule that routes to an execution agent (09 / 10).** **R10 is the
only rule that hands an approved action off for manual execution.** Both require
a `human.approved` trigger backed by an `approved`, non-low-confidence
recommendation on an `awaiting_approval` claim — R7 and R8 reject everything
else *before* R9/R10 are reached.

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R7** | `trigger.type == human.approved` **and not** (`claim.status == awaiting_approval` **and** `recommendation is not None` **and** `recommendation.approval_status == "approved"`) | `route` | `12-escalation` | `approval_without_recommendation` | `escalated` |
| **R8** | `trigger.type == human.approved` **and** `recommendation.low_confidence is True` | `route` | `12-escalation` | `low_confidence_cannot_be_approved` | `escalated` |
| **R9** | `trigger.type == human.approved` **and** `recommendation.action_type in EXECUTABLE_ACTIONS` | `route` | `EXECUTABLE_ACTIONS[action_type]` (`09-followup` or `10-reminder`) | `approved_followup` when → 09, `approved_reminder` when → 10 | `executing` |
| **R10** | `trigger.type == human.approved` **and** `recommendation.action_type in MANUAL_ACTIONS` | `route` | `12-escalation` | `approved_manual_action` | `manual_action_required` |
| **R11** | `trigger.type == human.approved` (action_type in neither map) | `route` | `12-escalation` | `unknown_action_type` | `escalated` |

Ordering: R7 rejects any approval not backed by an `approved` recommendation on
an `awaiting_approval` claim. R8 then rejects a low-confidence recommendation
even if a human clicked approve (low confidence is never a one-click path — see
§6.5 / R13 and requirement 5). Only approvals that clear both reach R9 / R10 /
R11, which dispatch purely on `action_type`.

#### 6.3.1 Manual-execution actions (R10)

`resubmit_corrected_coding` resubmits a claim to a payer — a consequential,
compliance-sensitive action. It is a first-class recommendation: 08 emits it,
it flows through `awaiting_approval` exactly like any other action, and a human
approves or declines it with the same Approve / Decline control. What it is
**not** is agent-executable — there is no code path in which 09, 10, or any
other agent performs a `resubmit_corrected_coding`.

On `human.approved` for such an action, R10 routes to 12 with reason_code
`approved_manual_action`. 12 writes an `escalations` row
(`reason_code = 'approved_manual_action'`, `originating_agent = '00-commander'`,
`context` = the approved recommendation + issue list) and the claim moves to
`manual_action_required`. The claim detail UI renders this as *"Approved —
awaiting manual resubmission by staff"*, distinct from an error escalation. A
future "mark manual action complete" flow would move it to `actioned`; Phase 1
stops at the logged, human-owned task.

This keeps the invariant intact from both directions: nothing consequential
executes without human approval, **and** the most consequential action in the
set never executes via automation at all.

### 6.4 Human decline

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R12** | `trigger.type == human.declined` | `no_action` | — | `declined_by_human` | `declined` |

### 6.5 Recommendation routing

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R13** | `trigger.type == recommendation.completed` **and** `recommendation.low_confidence is True` | `route` | `12-escalation` | `low_confidence_recommendation` | `escalated` |
| **R14** | `trigger.type == recommendation.completed` | `await_human` | — | `awaiting_human_approval` | `awaiting_approval` |

R13 implements requirement 5: **a low-confidence recommendation goes straight to
escalation** — it is never surfaced as an Approve/Decline choice.

### 6.6 Analysis pipeline progression

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R15** | `trigger.type in {claim.ingested, claim.reanalyze}` | `route` | `06-analyzer` | `needs_analysis` | — |
| **R16** | `trigger.type == analysis.completed` **and** `issues == []` | `no_action` | — | `no_issues_found` | `cleared` |
| **R17** | `trigger.type == analysis.completed` **and** `issues != []` | `route` | `07-reasoning` | `needs_reasoning` | `analyzed` |
| **R18** | `trigger.type == reasoning.completed` | `route` | `08-recommendation` | `needs_recommendation` | `reasoned` |
| **R19** | `trigger.type == execution.completed` **and** `claim.status == executing` | `no_action` | — | `execution_complete` | `actioned` |

### 6.7 Fallthrough

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **R20** | nothing above matched | `route` | `12-escalation` | `unrecognized_state` | `escalated` |

### 6.8 Worked traces

| # | start status | trigger | matches | outcome |
|---|--------------|---------|---------|---------|
| 1 | `received` | `claim.ingested` | R15 | run 06 |
| 2 | `received` | `analysis.completed`, 0 issues | R16 | → `cleared`, stop |
| 3 | `received` | `analysis.completed`, 2 issues | R17 | → `analyzed`, run 07 |
| 4 | `analyzed` | `reasoning.completed` | R18 | → `reasoned`, run 08 |
| 5 | `reasoned` | `recommendation.completed`, `low_confidence=False` | R14 | → `awaiting_approval`, wait |
| 6 | `reasoned` | `recommendation.completed`, `low_confidence=True` | R13 | → `escalated`, run 12 |
| 7 | `awaiting_approval` | `human.approved`, action `submit_authorization_request` | R9 | → `executing`, run 09 |
| 8 | `awaiting_approval` | `human.approved`, action `payer_status_follow_up` | R9 (→ `10-reminder`) | → `executing`, run 10 |
| 9 | `awaiting_approval` | `human.approved`, action `resubmit_corrected_coding` | R10 | → `manual_action_required`, run 12 (logs approved task for a human) |
| 10 | `awaiting_approval` | `human.declined` | R12 | → `declined`, stop |
| 11 | `executing` | `execution.completed` | R19 | → `actioned`, stop |
| 12 | `executing` | `execution.failed` | R6 | → `escalated`, run 12 |
| 13 | `received` | `human.approved` (spoofed — no recommendation) | R7 | → `escalated`, run 12 |
| 14 | `awaiting_approval` | `human.approved`, but `low_confidence=True` | R8 | → `escalated`, run 12 |
| 15 | `awaiting_approval` | `human.approved`, action `resubmit_corrected_coding`, but `low_confidence=True` | R8 | → `escalated`, run 12 (low confidence loses even the manual path) |
| 16 | `denied` | any trigger | R1 | `no_action` |
| 17 | `escalated` **or** `manual_action_required` | any trigger | R2 | `no_action` |
| 18 | `analyzed` | `execution.completed` (impossible ordering) | R20 | → `escalated`, run 12 |

---

## 7. The orchestrator (not the Commander)

A thin driver — `backend/app/agents/orchestrator.py`. One public entry point:

```python
async def handle(claim_id: str, trigger: dict) -> CommanderDecision
```

Steps:

1. **Load state** for `claim_id`, scoped to that claim's `organization_id`
   (§8). Assemble the §3 dict.
2. `decision = commander.decide(state, trigger)`.
3. **Append to `activity_log`**: `actor = "00-commander"`, `action =
   decision.reason_code`, `details = {trigger, route_to, next_status}`,
   `organization_id = claim.organization_id`.
4. If `decision.next_status` is set, `UPDATE claims SET status = ...` (scoped).
5. If `decision.action == "route"`, invoke that agent. Each agent:
   - runs, writing only rows in the claim's `organization_id`;
   - on success the orchestrator emits the natural follow-on trigger
     (`analysis.completed`, `reasoning.completed`, `execution.completed`, …) and
     calls `handle` again;
   - on a raised exception the orchestrator catches it and calls `handle` with
     `agent.error`;
   - 09 / 10 that exhaust bounded retries cause the orchestrator to call `handle`
     with `execution.failed`.
   - **R10 → 12 (`approved_manual_action`)** is a special case: 12 writes the
     escalation row and the run ends at `manual_action_required`. No follow-on
     trigger — a human, not the orchestrator, drives what happens next.
6. `await_human` / `no_action` → return; nothing further runs.

The loop (step 5 re-calling `handle`) is what walks a fresh claim from
`received` all the way to `awaiting_approval` in one pass, then stops and waits
for a human.

### 7.1 Loop-safety

- Every `route` transitions `status` (or the run ends). The terminal / re-entrancy
  guards R1–R4 stop a claim being re-driven.
- A hard cap of **12 Commander invocations per originating trigger** — if ever
  hit, the orchestrator forces an `agent.error` escalation. The rule table should
  make this unreachable; the cap exists so a future rule bug degrades to
  "escalate to a human", never "infinite loop".

---

## 8. Tenant scoping (non-negotiable)

Per `architecture.md` §3: **no hardcoded tenant id, anywhere, ever.**

- The Commander is a pure function over the state it is handed. It has no
  database access and therefore cannot leak across tenants — but it also must
  never be given a mixed-tenant `state`. The orchestrator guarantees single-tenant
  state.
- The orchestrator resolves `organization_id` **once**, from the `claims` row it
  is asked to process, and threads that value explicitly into every subsequent
  query (`... WHERE organization_id = $org AND ...`). It is never read from a
  request body, env var, or constant.
- Agent processing is a **background job**, not a user request, so it runs with
  the service-role key (it must write `activity_log` / `escalations` rows the
  end user cannot). This is the "batch job" carve-out in `architecture.md` §3.4.
  The compensating control: every agent query is explicitly `organization_id`-
  filtered from the claim, and `tests/agent_isolation_test.py` proves a run over
  a Clinic A claim never reads or writes a Clinic B row.
- New tables (`payers`, `claims`, `claim_issues`, `recommendations`,
  `follow_ups`, `escalations`, `activity_log`) each carry
  `organization_id uuid not null references public.organizations(id)` and each
  migration calls `select public.enable_tenant_isolation('public.<table>')`.

---

## 9. Interfaces

```python
# backend/app/agents/commander.py

from dataclasses import dataclass
from typing import Literal

Action = Literal["route", "await_human", "no_action"]

@dataclass(frozen=True)
class CommanderDecision:
    action: Action
    reason_code: str
    route_to: str | None = None
    next_status: str | None = None

def decide(state: dict, trigger: dict) -> CommanderDecision:
    """Pure. No I/O. No LLM. First matching rule in §6 wins."""
```

`reason_code` values are a closed set (every `reason_code` in §6). They are part
of the contract — `activity_log` rows and the Commander tests assert on them.

---

## 10. Test plan (for reference — full detail in the Phase 1 test section)

`tests/commander_test.py` — table-driven, one case per rule R1–R20, plus:

- the full happy path `received → … → awaiting_approval` (traces 1,3,4,5);
- `human.approved` with no recommendation in state → escalation (R7, trace 13);
- `human.approved` on a `low_confidence` recommendation → escalation (R8, trace 14);
- `recommendation.completed` with `low_confidence` → escalation, never
  `await_human` (R13, trace 6);
- `human.approved` + `resubmit_corrected_coding` → R10, `manual_action_required`,
  **never** `route_to in {09, 10}` (trace 9); and the same with `low_confidence`
  → R8 escalation (trace 15);
- every terminal status × a representative trigger → `no_action` (R1–R3);
- determinism: 1000 randomized `(state, trigger)` pairs, `decide` called twice,
  identical results, and these invariants hold on **every** result:
  - `route_to in {"09-followup", "10-reminder"}` ⟹ `trigger.type ==
    "human.approved"` and `recommendation.approval_status == "approved"` and
    `recommendation.low_confidence is False` and
    `recommendation.action_type in EXECUTABLE_ACTIONS`;
  - `recommendation.action_type == "resubmit_corrected_coding"` ⟹ `route_to
    != "09-followup"` and `route_to != "10-reminder"` (it can only ever reach
    12, and only via R10 / R8 / R7).

---

## 11. Open questions — resolved 2026-09-02

1. **`declined` handling (R12).** → **Soft stop.** `no_action`, status
   `declined`, activity-logged. A human may manually re-open / `claim.reanalyze`.
   No escalation on decline.
2. **Issue → severity mapping for the risk score** (§ risk formula, and
   `architecture.md`). → `missing_authorization` = **high** (50),
   `missing_documentation` = **high** (50), `code_mismatch` = **medium** (30),
   `overdue_follow_up` = **low** (10). `risk_score = min(100, Σ)`; `risk_level`
   High ≥ 70 / Medium 40–69 / Low < 40.
3. **Confidence source (08).** → **Deterministic formula** from the issue set
   (documented alongside 06's scoring), no LLM. `low_confidence == (band ==
   "Low")`. Commander R8 / R13 then keep low-confidence off the one-click path.
4. **Phase numbering.** → Foundation renumbered to **Phase 0**; this module is
   **Phase 1**. `docs/PHASE-1.md` (foundation) → `docs/PHASE-0.md`;
   `architecture.md` "Phase 1 (foundation)" references updated; new phase doc is
   the new `docs/PHASE-1.md`.
5. **`resubmit_corrected_coding` routing.** → **Manual-only, never
   agent-executed.** It stays a first-class recommendation through the identical
   approval gate (`awaiting_approval` → `human.approved`), but on approval R10
   routes it to 12 (`approved_manual_action`) and the claim ends at
   `manual_action_required` — a logged, human-owned task. No agent (09/10/other)
   ever performs it. See §2.1 and §6.3.1.

### Still open (not blocking — flag if you disagree)

- **`claim.reanalyze` scope (R15).** Currently allowed from any non-terminal
  status; re-runs the whole pipeline. Left permissive for now.

---

## 12. Phase 2 addendum — eligibility verification (01)

_Status: **SPEC — awaiting review.** Companion doc:
[`01-eligibility-agent.md`](01-eligibility-agent.md), which carries the data
model, the simulation, the orchestrator changes, the UI, and the test plan. This
section is **only** the Commander-facing part: the new trigger family and the
rule block. **It does not touch R1–R20.**_

### 12.1 What changes, and what does not

| | |
|---|---|
| `CommanderDecision` dataclass | **unchanged** — `action`, `reason_code`, `route_to`, `next_status` |
| `route_to` value set | gains `"01-eligibility"` |
| `reason_code` closed set | gains the six values in §12.6 |
| `next_status` for any eligibility rule | **always `None`** — the Commander never transitions a care object off an eligibility trigger (§12.3) |
| R1–R20 | untouched, not reordered, not re-conditioned |
| `EXECUTABLE_ACTIONS` / `MANUAL_ACTIONS` | untouched |

### 12.2 Dispatch

`decide()` gains a branch on the **first line**, before R1:

```python
ELIGIBILITY_TRIGGERS = {
    "appointment_scheduled", "emergency_patient_registered",
    "eligibility_check_completed", "eligibility_check_failed",
}

def decide(state, trigger):
    if (trigger or {}).get("type") in ELIGIBILITY_TRIGGERS:
        return _decide_eligibility(state, trigger)   # §12.6, E1-E7 — pure, like the rest
    # ... existing R1-R20, entirely unchanged ...
```

The two rule tables are **disjoint**. A claims trigger never reaches an E-rule;
an eligibility trigger never reaches R1. `state` is claim-shaped for the former
and eligibility-shaped for the latter (`01-eligibility-agent.md` §6.1) — the
orchestrator assembles whichever the trigger calls for.

### 12.3 The care-safety invariant (the reason this phase is spec-first)

Eligibility verification must **never gate or delay emergency care** (EMTALA).
The Commander enforces it structurally:

> **For every eligibility rule E1–E7: `decision.next_status is None`.**
> The Commander never drives an appointment, encounter, or any care object off
> the back of an eligibility trigger. The eligibility result lives only in
> `eligibility_checks.status` (written by 01) and `activity_log`.
>
> **For every eligibility trigger where the resolved `context.is_emergency` is
> true: `decision.route_to != "12-escalation"`.** The decision is `no_action`,
> or a `route` to `01-eligibility` itself. Emergency `insufficient_info` and
> emergency `check_failed` are *recorded outcomes* (E4), never escalations.

The orchestrator additionally `assert`s `decision.next_status is None` for every
eligibility decision, and `commander_test` / `eligibility_commander_test` fuzz
both clauses over the full trigger × status × `is_emergency` space
(`01-eligibility-agent.md` §12).

### 12.4 `context.is_emergency` — resolved fail-safe

The orchestrator resolves it (not the Commander). Ambiguity resolves to **true**
(the non-blocking path). See `01-eligibility-agent.md` §6.2 for the table; the
short version: emergency trigger, or an emergency-flagged row, or *no
appointment*, or the field is missing → **true**. Only an unambiguous,
appointment-backed, non-emergency booking is **false**.

### 12.5 Trigger taxonomy — additions to §5

| trigger `type` | emitted when | source |
|----------------|--------------|--------|
| `appointment_scheduled` | an appointment created with `is_emergency = false` | seed, `POST /api/appointments` |
| `emergency_patient_registered` | an appointment/registration created with `is_emergency = true` | seed, `POST /api/appointments`, ER intake |
| `eligibility_check_completed` | 01 wrote a terminal non-failure status (`verified_active` / `verified_inactive` / `insufficient_info`) | orchestrator, after 01 |
| `eligibility_check_failed` | 01 resolved `check_failed` (payer off-network) or its pure core raised | orchestrator, after 01 |

### 12.6 The rule block (E1–E7)

Evaluated top to bottom, first match wins — same discipline as §6. `next_status`
is `None` in every row (§12.3). `context.is_emergency` per §12.4.

| # | Condition | action | route_to | reason_code | next_status |
|---|-----------|--------|----------|-------------|-------------|
| **E1** | `trigger.type == "emergency_patient_registered"` | `route` | `01-eligibility` | `eligibility_emergency_fire_and_forget` | `None` |
| **E2** | `trigger.type == "appointment_scheduled"` **and** `context.is_emergency` | `route` | `01-eligibility` | `eligibility_emergency_fire_and_forget` | `None` |
| **E3** | `trigger.type == "appointment_scheduled"` | `route` | `01-eligibility` | `eligibility_scheduled_ahead_of_time` | `None` |
| **E4** | `trigger.type in {"eligibility_check_completed","eligibility_check_failed"}` **and** `context.is_emergency` | `no_action` | — | `eligibility_emergency_recorded` | `None` |
| **E5** | `trigger.type == "eligibility_check_completed"` | `no_action` | — | `eligibility_scheduled_recorded` | `None` |
| **E6** | `trigger.type == "eligibility_check_failed"` | `route` | `12-escalation` | `eligibility_check_failed_scheduled` | `None` |
| **E7** | any eligibility trigger, nothing above matched — `context.is_emergency` ? | `no_action` : `route` | — / `12-escalation` | `eligibility_emergency_recorded` / `eligibility_unrecognized_state` | `None` |

Notes (full rationale in `01-eligibility-agent.md` §6.3):

- **E1/E2** — anything emergency-flagged is *fire-and-forget*: routed to 01 so
  the check still runs, but the orchestrator runs it **detached** — nothing on a
  care path awaits it, its exceptions are swallowed to a recorded `check_failed`,
  and it chains no follow-on. Never routes to 12.
- **E4** — one rule for **every** emergency completion/failure.
  `verified_active`, `verified_inactive`, `insufficient_info`, `check_failed` are
  handled identically: 01 already wrote the row; the Commander logs and stops.
  `insufficient_info` (unidentified/unconscious patient) and `check_failed` carry
  `recheck_recommended: true` and mean "re-check when more info exists" — a
  routine follow-up, **not** an error, **not** an escalation.
- **E5** — a scheduled check that completed. `verified_inactive` /
  `insufficient_info` are recorded and surfaced to staff before the visit, not
  escalated.
- **E6** — the **only** eligibility rule that routes to 12, and unreachable when
  `context.is_emergency` (E4 precedes it). A scheduled check that *failed* (not
  the same as `insufficient_info`) becomes an operational "re-verify before the
  visit" task for a human. `next_status` still `None` — 12 only logs.
- **E7** — malformed eligibility state (mirror of R20), split so an
  emergency-context fallthrough is *recorded*, never escalated.

### 12.7 Worked traces

| # | trigger | is_emergency | check.status | matches | outcome |
|---|---------|--------------|--------------|---------|---------|
| E-1 | `appointment_scheduled` | false | — | E3 | route → 01 (awaited, ahead of the visit) |
| E-2 | `eligibility_check_completed` | false | `verified_inactive` | E5 | record, stop; staff see it before check-in |
| E-3 | `eligibility_check_failed` | false | `check_failed` | E6 | route → 12 (operational re-verify) |
| E-4 | `emergency_patient_registered` | true | — | E1 | route → 01 **detached**; care unaffected |
| E-5 | `eligibility_check_completed` | true | `insufficient_info` | E4 | `no_action`; recheck_recommended; **not** escalated |
| E-6 | `eligibility_check_failed` | true | `check_failed` | E4 | `no_action`; **not** escalated (contrast E-3) |
| E-7 | `appointment_scheduled`, row `is_emergency = true` | true | — | E2 | route → 01 detached (contradiction → safe path) |
| E-8 | `eligibility_check_completed`, no check row | true (fail-safe) | — | E7 | `no_action`, `eligibility_emergency_recorded` |
| E-9 | `eligibility_check_completed`, no check row | false | — | E7 | route → 12, `eligibility_unrecognized_state` |

### 12.8 Test plan (Commander-facing slice — full plan in `01-eligibility-agent.md` §12)

`tests/eligibility_commander_test.py`, stdlib only, no stack:

- one case per E1–E7;
- a re-run of representative R-rule cases to prove R1–R20 are unchanged;
- **the emergency-care-safety fuzz**: over
  `trigger × check.status × is_emergency × appointment{present,None}`, assert
  determinism, `next_status is None` on every result, and
  `is_emergency ⟹ route_to != "12-escalation"` and
  `route_to ∈ {None, "01-eligibility"}`;
- a named `test_emergency_eligibility_is_never_in_a_gating_path`.

### 12.9 Decisions — see `01-eligibility-agent.md` §13

Resolved at review: E6 escalates for a scheduled `check_failed` (operational
re-verify task); an emergency `check_failed` is recorded, never escalated (E4).
Emergency registration creates only an `eligibility_checks` row. One shared
`decide()` entry point. E5/E6/E4 additionally require a check row present in
state; a completion trigger with no check row falls to E7.

---

## 13. Phase 3 addendum — prior authorization (02)

_Status: **SPEC — awaiting review.** Companion doc:
[`02-prior-auth-agent.md`](02-prior-auth-agent.md), which carries the data model,
the determination / draft / response simulation, the orchestrator changes, the
UI, and the full test plan. This section is **only** the Commander-facing part:
the new trigger family and the rule block. **It does not touch R1–R20 or
E1–E7.**_

### 13.1 What changes, and what does not

| | |
|---|---|
| `CommanderDecision` dataclass | **unchanged** — `action`, `reason_code`, `route_to`, `next_status` |
| `route_to` value set | gains `"02-prior-auth"` |
| `reason_code` closed set | gains the ten values in §13.6 |
| `next_status` for any prior-auth rule | **always `None`** — same invariant as eligibility (§13.3) |
| R1–R20, E1–E7 | untouched, not reordered, not re-conditioned |
| `EXECUTABLE_ACTIONS` / `MANUAL_ACTIONS` / `ELIGIBILITY_TRIGGERS` | untouched |

### 13.2 Dispatch

`decide()` gains a second early branch, after the eligibility check, before R1:

```python
PRIOR_AUTH_TRIGGERS = {
    "prior_auth_requested", "prior_auth_emergency", "prior_auth_determined",
    "prior_auth_submission_approved", "prior_auth_submission_declined",
    "prior_auth_response_received",
}

def decide(state, trigger):
    ttype = (trigger or {}).get("type")
    if ttype in ELIGIBILITY_TRIGGERS:
        return _decide_eligibility(state, trigger)      # §12.6, E1-E7
    if ttype in PRIOR_AUTH_TRIGGERS:
        return _decide_prior_auth(state, trigger)       # §13.6, A1-A11
    # ... existing R1-R20, entirely unchanged ...
```

The three rule tables are **disjoint**. A trigger belongs to exactly one family;
`state` is claim-shaped, eligibility-shaped, or prior-auth-shaped
(`02-prior-auth-agent.md` §6.1) — the orchestrator assembles whichever the
trigger calls for. Family order among the two early branches is irrelevant
(disjoint sets); it is fixed as eligibility-then-prior-auth for readability.

### 13.3 The care-safety invariant

Prior authorization can gate an **elective** service (correctly — that is what
prior auth is *for*). It must **never** gate, delay, or precondition **emergency
/ urgent** care (EMTALA; and emergency services are contractually exempt from
prior auth). The Commander enforces the emergency half structurally:

> **For every prior-auth rule A1–A11: `decision.next_status is None`.** The
> Commander never writes a status off a patient-access trigger — identical to the
> eligibility invariant. `prior_authorizations.status` is written only by agent
> 02 and the orchestrator's response handler; nothing on `appointments` is ever
> touched.
>
> **For every prior-auth trigger where the resolved `context.is_emergency` is
> true:**
> - **`decision.action != "await_human"`** — an emergency never parks for a human
>   approval;
> - **`decision.route_to != "12-escalation"`** — an emergency is never escalated;
> - the decision is `no_action`, or a `route` to `02-prior-auth` for the
>   *determination only* (run detached — `02-prior-auth-agent.md` §9.3).
>
> The determination an emergency can produce is `emergency_exempt` or
> `insufficient_info` — never `required_draft`, so there is never a request to
> draft, approve, or submit (`02-prior-auth-agent.md` §7.3, P5).

`orchestrator.handle_prior_auth` additionally hard-`raise`s on any violation
(`next_status` non-None; emergency routed to 12; emergency at `await_human`), and
`prior_auth_commander_test` fuzzes all clauses over the full
trigger × status × response × `is_emergency` × `place_of_service` × appointment
space (`02-prior-auth-agent.md` §12).

### 13.4 `context.is_emergency` — resolved fail-safe

The orchestrator resolves it (not the Commander). Ambiguity resolves to **true**
(the non-blocking, no-auth path). See `02-prior-auth-agent.md` §6.2 for the
table; the short version: emergency trigger, or an emergency-flagged row, or
`place_of_service == 'emergency'`, or *no appointment*, or the field is missing →
**true**. Only an unambiguous, appointment-backed, non-emergency,
non-`emergency`-POS row is **false**.

### 13.5 Trigger taxonomy — additions to §5

| trigger `type` | emitted when | source |
|----------------|--------------|--------|
| `prior_auth_requested` | a `prior_authorizations` row created for a non-emergency appointment | seed, `POST /api/prior-authorizations` |
| `prior_auth_emergency` | a `prior_authorizations` row created with `is_emergency = true` / `place_of_service = 'emergency'` | seed, `POST /api/prior-authorizations`, ER intake |
| `prior_auth_determined` | `02.determine` wrote a terminal determination status | orchestrator, after `02.determine` |
| `prior_auth_submission_approved` | a human approved submitting a drafted request | `POST /api/prior-authorizations/{id}/approve-submission` |
| `prior_auth_submission_declined` | a human declined submitting a drafted request | `POST /api/prior-authorizations/{id}/decline-submission` |
| `prior_auth_response_received` | `02.submit` produced a deterministic payer response | orchestrator, after `02.submit` |

### 13.6 The rule block (A1–A11)

Evaluated top to bottom, first match wins — same discipline as §6 / §12.6.
`next_status` is `None` in every row (§13.3). `context.is_emergency` per §13.4.
`pa` = `state["prior_authorization"]`; `pa.response_status` =
`pa["response_payload"].get("response_status")`.

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
| **A11** | any prior-auth trigger, nothing above matched — `context.is_emergency` ? `no_action` : `route` `12-escalation` | — / `12-escalation` | `prior_auth_emergency_exempt_recorded` / `prior_auth_unrecognized_state` | `None` |

Notes (full rationale in `02-prior-auth-agent.md` §6.3):

- **A1/A2** — anything emergency-flagged routes to `02.determine` **detached**;
  the determination runs (so the record exists) but nothing on a care path awaits
  it. Never routes to 12, never awaits a human.
- **A4 before A5/A6** — every emergency determination is recorded and stopped. By
  P5 an emergency determination can only be `emergency_exempt` /
  `insufficient_info`, so A5 could not match anyway; A4 makes it explicit.
- **A5** — a scheduled determination of `required_draft`. Parks at `await_human`;
  a human approves or declines submitting the drafted packet. Foresight never
  auto-submits (mirror of R14).
- **A7** — the **only** rule that routes to `02.submit`, behind three guards
  (the approval trigger, `pa.status == "required_draft"`, `not is_emergency`).
  The structural analogue of R9 behind R7/R8.
- **A10** — the **only** prior-auth rule that routes to 12, and unreachable when
  `context.is_emergency` (guarded, and by P5/P6 an emergency PA never has a
  submission). A scheduled `denied` / `info_needed` is a real human task
  (peer-to-peer, appeal, or attach clinicals and resubmit as a new chained row).
  `next_status` still `None` — 12 only logs.
- **A11** — malformed prior-auth state (mirror of R20 / E7), split so an
  emergency-context fallthrough is *recorded*, never escalated.

### 13.7 Worked traces

| # | trigger | is_emergency | pa.status / response | matches | outcome |
|---|---------|--------------|----------------------|---------|---------|
| A-1 | `prior_auth_requested` | false | — | A3 | route → 02.determine (awaited, ahead of the visit) |
| A-2 | `prior_auth_determined` | false | `not_required` | A6 | record, stop |
| A-3 | `prior_auth_determined` | false | `required_draft` | A5 | `await_human` — draft on the queue |
| A-4 | `prior_auth_submission_approved` | false | `required_draft` | A7 | route → 02.submit |
| A-5 | `prior_auth_response_received` | false | `approved` | A9 | record `auth_approved`, stop |
| A-6 | `prior_auth_response_received` | false | `denied` | A10 | route → 12 (peer-to-peer / appeal) |
| A-7 | `prior_auth_emergency` | true | — | A1 | route → 02.determine **detached**; care unaffected |
| A-8 | `prior_auth_determined` | true | `emergency_exempt` | A4 | `no_action`; recorded; **not** escalated, **no** draft |
| A-9 | `prior_auth_requested`, row `is_emergency = true` | true | — | A2 | route → 02.determine detached (contradiction → safe path) |
| A-10 | `prior_auth_response_received`, no PA row | true (fail-safe) | — | A11 | `no_action`, `prior_auth_emergency_exempt_recorded` |
| A-11 | `prior_auth_response_received`, no PA row | false | — | A11 | route → 12, `prior_auth_unrecognized_state` |

### 13.8 Test plan (Commander-facing slice — full plan in `02-prior-auth-agent.md` §12)

`tests/prior_auth_commander_test.py`, stdlib only, no stack:

- one case per A1–A11;
- a re-run of representative R-rule and E-rule cases to prove R1–R20 / E1–E7 are
  unchanged;
- **the emergency-care-safety fuzz**: over
  `trigger × pa.status × pa.response_status × is_emergency × place_of_service ×
  appointment{present,None}`, assert determinism, `next_status is None` on every
  result, and
  `is_emergency ⟹ action != "await_human"` and `route_to != "12-escalation"` and
  `route_to ∈ {None, "02-prior-auth"}`;
- a named `test_emergency_prior_auth_is_never_gating`.

### 13.9 Decisions — see `02-prior-auth-agent.md` §13

To resolve at review: the human-approval mechanism (dedicated endpoints vs. the
`recommendations` table); `02.submit` folded into 02 vs. a new executor number;
whether an approved PA feeds the claims pipeline (recommend: not in Phase 3);
`place_of_service` as text vs. enum; a new nav item vs. the Insurance stub.
