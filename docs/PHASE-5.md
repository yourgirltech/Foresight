# Phase 5 — Appeals (11)

_Status: **SPEC — awaiting review.** Agent spec:
[`agents/11-appeals-agent.md`](agents/11-appeals-agent.md); Commander addendum:
[`agents/00-commander.md`](agents/00-commander.md) §14. No code yet. Approve,
then build in the Phase 1–4 order: migration → agent → orchestrator → endpoints
→ UI → tests._

**Goal.** Close the loop on the claims pipeline. Phases 1–4 take a claim from
ingestion to a human-approved action, verify eligibility and prior auth ahead of
a visit, and read the patient's coverage. What happens when the payer says
**no** was out of scope. Phase 5 adds the **appeal**: a denied claim →
11-appeals-agent drafts a letter grounded strictly in evidence already in the
system → a human reviews and approves → a simulated submission → a deterministic
resolution → a won appeal reverses the claim.

| # | Agent | What it does | AI? |
|---|-------|--------------|-----|
| 11 | **appeals-agent** | denied claim → an appeal letter grounded only in this claim's real rows (06's issues, the recorded denial reason, actions already taken); human approves the send; simulated submission; deterministic resolution; a win reverses the claim `denied → paid` | **yes** — drafting only; the resolution and the gate are deterministic |

## The architectural decision: 11 IS a Commander agent

03/04/05 (Phase 4) were correctly **synchronous tools** — a human invokes them,
they return, no lifecycle. **11 is different.** It reacts to a real claim
lifecycle event (a claim reaching `denied`), advances a small state machine
behind the Phase 1 human-approval gate, and does a simulated submission — the
same shape as 09/10 and the prior-auth flow (02).

It is a **fourth disjoint Commander family** (AP1–AP12), dispatched by an early
branch in `decide()` before R1, exactly like eligibility (E1–E7) and prior-auth
(A1–A11). `commander_test.py` / `eligibility_commander_test.py` /
`prior_auth_commander_test.py` still pass unchanged.

**Unlike E1–E7 / A1–A11**, an appeal rule *may* write a `claims.status`
transition — but exactly one (`denied → paid`, on AP9, after a human-approved
submission). There is no care-safety (EMTALA) constraint here; updating the claim
on a won appeal is the point of the feature. See `00-commander.md` §14.3 for the
two invariants that replace the care-safety invariant.

## Shared conventions

- Same tenancy rule as every prior phase: `appeals` carries `organization_id
  uuid not null references public.organizations(id)` and runs through
  `select public.enable_tenant_isolation('public.appeals')`. No hardcoded tenant
  id. Agent writes use the service-role key and filter every query by the
  `organization_id` resolved from the triggering claim.
- Backend-mediated writes only — no new client write grants. Reads go through the
  caller's RLS-scoped JWT.
- The AI call (`draft_appeal`) reuses 07's pattern exactly:
  `anthropic.AsyncAnthropic`, `client.messages.create`, model from a settings
  field defaulting to `claude-opus-5`, a strict grounded system prompt, and a
  graceful `AppealsUnavailable` on API error — no hollow fallback (07's
  discipline, not 05's template).

## Test standard

| Area | Standard |
|------|----------|
| grounding | `appeal_basis()` (pure) proven to cite **only** strings present in the claim's real `claim_issues` / `recommendations` / `denial_reason` rows — never a fabricated code, date, or document; a `--live` draft test asserting the same on real model output |
| the state machine | one case per AP1–AP12; a fuzz over `trigger × appeal.status × resolution × appeal{present,absent}` asserting determinism, the human-approval gate (AP6 only, backed by an approval trigger + a `drafted` status), and the single claim transition (AP9 only, `paid` only) |
| resolution simulation | `simulate_resolution()` partition sweep of `bucket 0..99`; grounds-bonus monotonicity; the resubmit bonus; deterministic |
| tenant isolation | `agent_isolation_test.py` extended — an appeal over Clinic A's denied claim never reads Clinic B rows; the reversal writes only A's claim |
| end to end | one real test: `denied → claim_denied → draft → approve → submit → resolution`, both a won (claim `paid`) and an upheld (claim `denied` + one escalation) path |

## Delivery order

1. Migration — `appeals` + `appeal_status` enum + `claims.denial_reason`.
2. `backend/app/agents/appeals.py` — `appeal_basis` / `draft_appeal` /
   `simulate_resolution` + the named simulation constants.
3. `commander._decide_appeal` (AP1–AP12) + `orchestrator.handle_appeal` (+ the
   two hard-`raise` invariant guards).
4. `backend/app/routers/appeals.py` + the `GET /api/claims/{id}` extension.
5. UI — the Appeal section on `ClaimDetailPage`.
6. Tests + `scripts/seed_appeals.py` + `scripts/run_appeals_proof.sh`.

See [`agents/11-appeals-agent.md`](agents/11-appeals-agent.md) for the full data
model, contract, and open decisions.
