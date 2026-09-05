# Phase 1 — Claims & Billing

**Goal:** the first real feature on top of the Phase 0 foundation. A claim is
analyzed by a deterministic rule engine, explained in plain language, and given a
recommended action. A human approves or declines. Only after approval does an
agent execute — and the one action that resubmits a claim to a payer is never
agent-executed at all. Every new table and every agent respects `organization_id`
scoping via the same `enable_tenant_isolation()` pattern as Phase 0.

**Core principle, unchanged:** AI recommends. A human approves anything
consequential. Automation executes only after approval.

## Delivered

| # | Item | Location |
|---|------|----------|
| 1 | 7 tables (`payers`, `claims`, `claim_issues`, `recommendations`, `follow_ups`, `escalations`, `activity_log`) + 9 enums, each table through `enable_tenant_isolation()` | `supabase/migrations/20260902000001_claims_and_billing.sql` |
| 2 | **06 analyzer** — deterministic rule engine, no LLM. Weighted risk score + level. Formula documented in `architecture.md` §6.1 | `backend/app/agents/rules.py` |
| 3 | **00 Commander** — pure decision node, ordered rule table R1–R20, terminal + safety + human-approval guards | `backend/app/agents/commander.py`, spec `docs/agents/00-commander.md` |
| 4 | **07 reasoning** — plain-language explanation, strictly grounded, calls the Claude API (`ANTHROPIC_API_KEY`, never hardcoded) | `backend/app/agents/reasoning.py` |
| 5 | **08 recommendation** — action + confidence band + `low_confidence`, deterministic (formula in `architecture.md` §6.2) | `backend/app/agents/recommendation.py` |
| 6 | **09 followup / 10 reminder** — execute an approved action (simulated send + logged record), bounded transient-retry policy | `backend/app/agents/executors.py` |
| 7 | **12 escalation** — safety net; logs full context, never retries | `backend/app/agents/escalation.py` |
| 8 | Orchestrator — loads state, logs every Commander decision, runs the routed agent, re-invokes the Commander; 12-invocation loop cap | `backend/app/agents/orchestrator.py` |
| 9 | Service-role PostgREST client — every method takes `org_id` explicitly and filters on it | `backend/app/agents/db.py` |
| 10 | Endpoints: `GET /api/claims`, `GET /api/claims/{id}`, `POST /api/claims/{id}/approve\|decline\|reanalyze` | `backend/app/routers/claims.py` |
| 11 | Human review UI — claim list + claim detail (findings, reasoning, recommendation, real Approve/Decline, activity timeline) | `frontend/src/pages/ClaimsPage.tsx`, `ClaimDetailPage.tsx` |
| 12 | Seed script — synthetic claims, genuine random risk spread, five deterministic demo claims, prints the real distribution | `scripts/seed_claims.py` |
| 13 | Tests — rule engine, Commander rule table, agent tenant-isolation, end-to-end | `tests/rules_test.py`, `commander_test.py`, `agent_isolation_test.py`, `e2e_claim_test.py` |

## The claim lifecycle

```
received ─(06)→ analyzed ─(07)→ reasoned ─(08)→ awaiting_approval
   │                                                  │
   └─(06, no issues)→ cleared                approve   │   decline
                                    ┌─────────────────┼──────────────┐
                        action ∈ executable      action = resubmit    │
                                    │             _corrected_coding   │
                              (09 / 10)                 │             │
                                    ▼                   ▼             ▼
                                actioned      manual_action_required  declined

any agent error / low-confidence recommendation / unrecognised state ──→ escalated
```

Low-confidence recommendations never reach `awaiting_approval` — the Commander
(R13) routes them straight to `escalated`. `denied` / `paid` / `rejected` are
external terminals set only by the seed in Phase 1.

## How it was verified

### Deterministic tests (no API key)

```bash
python tests/rules_test.py        # 06 risk formula — 14 checks
python tests/commander_test.py     # 00 rule table R1–R20 + 2000-case determinism/invariant fuzz — 34 checks
python tests/agent_isolation_test.py   # agent pipeline never crosses a tenant boundary — 21 checks (local stack up)
python tests/e2e_claim_test.py     # deterministic slice always; full approval path needs a key
```

`commander_test.py`'s fuzz asserts the structural invariant: no result routes to
`09`/`10` without a `human.approved` trigger + an approved, non-low-confidence
recommendation, and `resubmit_corrected_coding` never routes to an executor.

### Full end-to-end (needs `ANTHROPIC_API_KEY`)

```bash
# in backend/.env:  ANTHROPIC_API_KEY=sk-ant-...
python tests/e2e_claim_test.py
```

Seeds three claims, runs each through the Commander end to end:

| claim | path | asserted outcome |
|-------|------|------------------|
| `CLM-E2E-AUTH` | missing auth → `submit_authorization_request` → **approve** | 09 executes; status `actioned`; one `follow_ups` row (agent `09-followup`, org-scoped, sent); activity_log shows the full chain |
| `CLM-E2E-CODING` | code mismatch → `resubmit_corrected_coding` → **approve** | R10 → 12 logs it; status `manual_action_required`; **no** `follow_ups` row; one `escalations` row (`approved_manual_action`) |
| `CLM-E2E-DECLINE` | missing docs → `request_documentation` → **decline** | status `declined`; no execution, no escalation |

## Running the module locally

```bash
# 1. Stack + migrations (Docker running)
npx supabase start && npx supabase db reset

# 2. Seed synthetic claims (prints the real risk distribution)
python scripts/seed_claims.py

# 3. Backend + frontend (see README.md quick start)
#    Log in as the seed admin printed by the script and open /claims.
```

`scripts/seed_claims.py` creates two seed clinics via the real signup/bootstrap
path, wipes prior seed claims, generates ~30 + ~12 synthetic claims with
independently-rolled evidence (nothing hardcoded to pass), adds five deterministic
demo claims, drives them all through the real pipeline, and prints:

```
risk level (written by 06-analyzer):
  Low        22   48.9%
  Medium     14   31.1%
  High        9   20.0%
```

The `CLM-*-DEMO-CODING` claim exercises the `manual_action_required` path so it
can be reviewed in the UI. Without a key, the pipeline still runs 06 (so the risk
distribution is real) but claims with issues escalate at the reasoning step.
