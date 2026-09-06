# Phase 2 — Eligibility Verification

**Goal:** the first *patient-access* agent. Given a patient and their payer,
01-eligibility-agent returns a coverage status. Phase 2 **simulates** the
clearinghouse (deterministic, documented — like 06's rule engine and 09/10's
simulated sends), so the Commander wiring and the EMTALA-required behavior are
proven before any real-integration decision.

**The non-negotiable:** eligibility verification **never gates or delays
emergency care.** For a scheduled appointment it runs ahead of time and informs
staff. For an emergency/unscheduled patient it runs *in parallel with care* — it
never blocks, delays, or is a precondition for anything. This is enforced by the
structure of the design (see `agents/01-eligibility-agent.md` §2), not a comment.

## Delivered

| # | Item | Location |
|---|------|----------|
| 1 | `eligibility_status` enum + `appointments` + `eligibility_checks` tables (sidecar: `appointment_id` nullable, `is_emergency` snapshotted, re-checks chained by `previous_check_id`); 2 `payers` sim knobs; `activity_log`/`escalations` gain `appointment_id`/`eligibility_check_id`. All through `enable_tenant_isolation()` | `supabase/migrations/20260906000001_eligibility_verification.sql` |
| 2 | **01 eligibility** — pure deterministic `simulate(patient, payer)`. SHA-256 member bucket vs. per-payer threshold; identity gate → `insufficient_info`; off-network payer → `check_failed`. §7 of the agent doc is the contract | `backend/app/agents/eligibility.py` |
| 3 | **Commander** — a second disjoint rule block (E1–E7), dispatched from `decide()` before R1. `next_status is None` for every E-rule; no emergency-context rule routes to 12 | `backend/app/agents/commander.py`, spec `docs/agents/00-commander.md` §12 |
| 4 | **Orchestrator** — `handle_eligibility`. Emergency check runs **detached** (`_spawn`, never awaited, exceptions swallowed to a recorded `check_failed`). Hard-raises if a Commander decision ever carries `next_status` | `backend/app/agents/orchestrator.py` |
| 5 | Endpoints: `GET /api/eligibility`, `GET /api/appointments/{id}`, `GET /api/eligibility-checks/{id}`, `POST /api/appointments`, `POST /api/eligibility-checks/{id}/recheck` | `backend/app/routers/appointments.py` |
| 6 | UI — Appointments list (scheduled + a separate "Emergency — care not gated" section), appointment detail + eligibility-check detail with the append-only check history and a re-check form | `frontend/src/pages/AppointmentsPage.tsx`, `AppointmentDetailPage.tsx`, `EligibilityCheckDetailPage.tsx` |
| 7 | Seed — payer knobs + scheduled appointments (seeded RNG → reproducible distribution) + the emergency `insufficient_info → (id added) → verified_active` progression; prints the distribution | `scripts/seed_eligibility.py` |
| 8 | Tests — E1–E7 rule cases + a **648-state exhaustive care-safety fuzz** (`next_status is None` for all; emergency never routes to 12); orchestrator detached-execution + no-escalation-for-emergency; real end-to-end; tenant-isolation slice | `tests/eligibility_commander_test.py`, `tests/eligibility_orchestrator_test.py`, `tests/e2e_eligibility_test.py`, `tests/agent_isolation_test.py` |

## The eligibility flow

```
appointment_scheduled ──► 00 ──E3──► 01 (awaited, ahead of the visit)
                                       └► eligibility_check_completed ──► 00 ──E5──► record, stop
                                       └► eligibility_check_failed    ──► 00 ──E6──► 12 (operational re-verify)

emergency_patient_registered ──► 00 ──E1──► 01  (DETACHED — nothing on a care path awaits it)
   (care proceeds, unaffected)              └► *_completed / *_failed ──► 00 ──E4──► record, stop
                                               insufficient_info / check_failed => recheck_recommended,
                                               NEVER escalated
```

## How it was verified

```bash
python tests/eligibility_commander_test.py     # E1-E7 + 648-state fuzz + R1-R20 regression
python tests/eligibility_orchestrator_test.py   # detached emergency exec; emergency check_failed != escalation
python tests/e2e_eligibility_test.py            # real end to end, tenant-scoped
python tests/agent_isolation_test.py            # + eligibility isolation slice
python scripts/seed_eligibility.py              # reproducible status distribution + the ER progression
```

No `ANTHROPIC_API_KEY` needed anywhere in Phase 2 — 01 has no LLM step.

## Still not built

- Real clearinghouse integration (01 simulates, per the same pattern as 09/10).
- A scheduled job that re-runs `recheck_recommended` checks automatically.
- Eligibility results feeding the claims pipeline (Phase 2 keeps them fully
  separate — the sidecar).
