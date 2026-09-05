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

Numbers 01–05 and 11 are intentionally unassigned — reserved for later phases
(eligibility, prior-auth, patient comms, …). The Commander never routes to them;
if a trigger implies one, it falls through to escalation (R20).

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
