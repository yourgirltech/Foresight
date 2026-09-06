# Phase 3 — Prior Authorization

_Status: **IMPLEMENTED** (2026-09-07). All six review decisions
(`02-prior-auth-agent.md` §13) resolved as recommended. Full spec:
[`agents/02-prior-auth-agent.md`](agents/02-prior-auth-agent.md) + the §13
addendum to [`agents/00-commander.md`](agents/00-commander.md). Code:
`supabase/migrations/20260906000003_prior_authorization.sql`,
`backend/app/agents/prior_auth.py`, `commander._decide_prior_auth` (A1–A11),
`orchestrator.handle_prior_auth`, `backend/app/routers/prior_auth.py`,
`frontend/src/pages/PriorAuth{,Detail}Page.tsx`, `scripts/seed_prior_auth.py`,
`tests/prior_auth_{determiner,commander,orchestrator}_test.py` +
`tests/e2e_prior_auth_test.py` + a slice in `tests/agent_isolation_test.py`._

_**Seeded distribution (deterministic, 2 clinics, 40 prior authorizations):**
determination — required_draft ≈ 54–64 %, not_required ≈ 36–39 %, plus
insufficient_info and emergency_exempt demos; payer response (of those submitted)
— approved ≈ 62–67 %, info_needed ≈ 9–20 %, denied ≈ 20–27 %. A resubmit shifts
the effective bucket down by 18 so `info_needed → auth_approved` on the second
attempt._

**Goal:** the second *patient-access* agent, and the biggest **preventable**
denial category. Given an appointment, its payer, and the planned procedure,
02-prior-auth-agent determines whether prior authorization is required, drafts
the request, and — after **a human approves submitting it** — runs a simulated
submission and records the payer's answer. Phase 3 **simulates** the payer
(deterministic, documented — like 06's rule engine, 01's clearinghouse sim, and
09/10's simulated sends), so the Commander wiring and the care-safety behavior
are proven before any real-integration decision.

**The non-negotiable:** prior authorization **never gates or delays emergency /
urgent care.** Emergency services are exempt from prior authorization by law
(EMTALA) and by every payer contract. For an elective service, 02 runs ahead of
time and a human decides whether to pursue the auth — Foresight *surfaces* the
status, it never sets a state that blocks the visit. For an emergency service, 02
records `emergency_exempt` and stops: no draft, no human approval step, no
escalation. Enforced by the structure of the design (`agents/02-prior-auth-agent.md`
§2, mechanisms P1–P8), not a comment.

## Delivered

| # | Item | Location |
|---|------|----------|
| 1 | `prior_auth_status` enum + `prior_authorizations` table (sidecar: `appointment_id` nullable, `is_emergency` + `place_of_service` snapshotted, resubmits chained by `previous_auth_id`); 3 `payers` sim knobs; `activity_log` / `escalations` gain `prior_authorization_id`. All through `enable_tenant_isolation()` | `supabase/migrations/20260906000003_prior_authorization.sql` |
| 2 | **02 prior-auth** — pure deterministic `determine()` (emergency ⇒ never required — P5), `draft_request()`, `simulate_response()` (SHA-256 bucket vs. per-payer approval threshold). §7 of the agent doc is the contract | `backend/app/agents/prior_auth.py` |
| 3 | **Commander** — a third disjoint rule block (A1–A11), dispatched from `decide()` after the eligibility check, before R1. `next_status is None` for every A-rule; no emergency-context rule routes to submission or to 12; no emergency-context rule reaches `await_human` | `backend/app/agents/commander.py`, spec `docs/agents/00-commander.md` §13 |
| 4 | **Orchestrator** — `handle_prior_auth`. Emergency determination runs **detached** (reuses the Phase 2 `_spawn` machinery). `02.submit` is reachable **only** via A7, behind the `prior_auth_submission_approved` trigger. Hard-raises if a decision carries `next_status`, or routes an emergency to 12 / `await_human` | `backend/app/agents/orchestrator.py` |
| 5 | Endpoints: `GET /api/prior-authorizations`, `GET /api/prior-authorizations/{id}`, `POST /api/prior-authorizations`, `POST /api/prior-authorizations/{id}/approve-submission`, `.../decline-submission`, `.../resubmit` | `backend/app/routers/prior_auth.py` |
| 6 | UI — a **Prior Auth** work queue (`/app/prior-auth`, nav item + badge), a prior-auth detail page with the drafted packet and resubmit chain, and a Prior Authorization card on the appointment detail page (with an explicit "Not required — emergency exemption" line). Dashboard "may be denied" insight re-pointed at real data | `frontend/src/pages/PriorAuthPage.tsx`, `PriorAuthDetailPage.tsx`, `AppointmentDetailPage.tsx` |
| 7 | Seed — payer knobs + scheduled appointments with a seeded RNG (deterministic procedure codes → deterministic determination + response distribution) + one emergency encounter showing `emergency_exempt`, + one `info_needed → resubmit → auth_approved` chain. Prints the distribution | `scripts/seed_prior_auth.py` |
| 8 | Tests — `determine` / `draft_request` / `simulate_response` unit tests + A1–A11 rule cases + an **exhaustive care-safety fuzz** (`next_status is None` for all; emergency never routes to submission / 12 / `await_human`); orchestrator detached-execution + the human-approval gate + no-escalation-for-emergency; real end-to-end; tenant-isolation slice | `tests/prior_auth_determiner_test.py`, `tests/prior_auth_commander_test.py`, `tests/prior_auth_orchestrator_test.py`, `tests/e2e_prior_auth_test.py`, `tests/agent_isolation_test.py` |

## The prior-auth flow

```
prior_auth_requested ──► 00 ──A3──► 02.determine (awaited, ahead of the visit)
                                      ├─ not_required / insufficient_info ──► 00 ──A6──► record, stop
                                      └─ required_draft ──► 00 ──A5──► await_human
                                                                          │
                                              human approves ─► prior_auth_submission_approved
                                                                          │
                                                                    00 ──A7──► 02.submit ──► prior_auth_response_received
                                                                          ├─ approved    ──► 00 ──A9 ──► record (auth_approved)
                                                                          ├─ info_needed ──► 00 ──A10──► 12  (attach clinicals, resubmit)
                                                                          └─ denied      ──► 00 ──A10──► 12  (peer-to-peer / appeal)

prior_auth_emergency ──► 00 ──A1──► 02.determine  (DETACHED — nothing on a care path awaits it)
   (care proceeds, unaffected)          └► prior_auth_determined ──► 00 ──A4──► record, stop
                                           status = emergency_exempt; never a draft, never await_human,
                                           never an escalation
```

## How it was verified

```bash
python tests/prior_auth_determiner_test.py     # 63 — determine / draft / response, pure, + the P5 proof
python tests/prior_auth_commander_test.py       # 32 — A1-A11 + a 23,328-state care-safety fuzz + R1-R20 / E1-E7 regression
python tests/prior_auth_orchestrator_test.py    # 20 — detached emergency exec; the human-approval gate; emergency != escalation; the 3 hard-raises
python tests/e2e_prior_auth_test.py             # 31 — real end to end, tenant-scoped
python tests/agent_isolation_test.py            # 29 — + prior-auth isolation slice
python scripts/seed_prior_auth.py               # reproducible distribution + the emergency + resubmit demos
```

All 11 agent test suites green (305 checks). No `ANTHROPIC_API_KEY` needed
anywhere in Phase 3 — 02 has no LLM step.

## Explicitly out of scope for Phase 3

- **Real payer integration** — the X12 278 transaction or a payer PA portal. 02
  simulates, per the same pattern as 01 and 09/10.
- **A real clinical-justification generator.** `draft_request` uses a fixed
  per-procedure sentence; a grounded narrative (an LLM step, like 07) is a later
  addition.
- **Auto-resubmit** of `info_needed` — Phase 3 routes it to a human.
- **Prior auth feeding the claims pipeline.** An `auth_approved` PA could set
  `claims.authorization_present` automatically; Phase 3 keeps the modules
  separate (the sidecar), like Phase 2. Cross-wiring the patient-access agents
  into claims is a candidate for a dedicated later phase.
- **Procedure-level payer rules as data.** Phase 3 uses documented code sets in
  the agent module (`ALWAYS_AUTH_PROCEDURES` / `ELECTIVE_AUTH_PROCEDURES`), not a
  `payer_procedure_rules` table.
