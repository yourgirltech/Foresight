#!/usr/bin/env python3
"""01 — eligibility rule block (E1-E7) tests + the emergency-care-safety fuzz.

Companion to commander_test.py. Proves:
  * one case per rule E1-E7 (docs/agents/00-commander.md §12.6);
  * the claims table R1-R20 is untouched by the new dispatch (regression cases);
  * THE CARE-SAFETY INVARIANT (00-commander.md §12.3, 01-eligibility-agent.md §2):
      - decide() returns next_status is None for EVERY eligibility trigger, so the
        Commander can never transition an appointment / encounter / care object;
      - whenever any emergency signal is present in the state, decide() NEVER
        routes to 12-escalation — the decision is no_action, or a route to
        01-eligibility itself.

The fuzz is exhaustive over
    trigger (4) x appointment (3) x check.is_emergency (3)
      x context.is_emergency (3) x check.status (6)  = 648 states,
each decided twice to also assert determinism.

Stdlib only. Run:  python tests/eligibility_commander_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.commander import (  # noqa: E402
    EXECUTABLE_ACTIONS,
    MANUAL_ACTIONS,
    decide,
)

_PASS = _FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
    if detail and not ok:
        line += f"\n         -> {detail}"
    print(line)


# --- the closed sets this phase adds (00-commander.md §12.1) -------------------
ELIG_TRIGGERS = [
    "appointment_scheduled",
    "emergency_patient_registered",
    "eligibility_check_completed",
    "eligibility_check_failed",
]
ELIG_REASON_CODES = {
    "eligibility_emergency_fire_and_forget",
    "eligibility_scheduled_ahead_of_time",
    "eligibility_emergency_recorded",
    "eligibility_scheduled_recorded",
    "eligibility_check_failed_scheduled",
    "eligibility_unrecognized_state",
}
ELIG_ROUTE_TARGETS = {None, "01-eligibility", "12-escalation"}
ELIG_STATUSES = [
    "pending", "verified_active", "verified_inactive", "insufficient_info", "check_failed",
]
# every claims.status enum value — an eligibility decision must never emit one
CLAIM_STATUSES = {
    "received", "analyzed", "cleared", "reasoned", "awaiting_approval", "executing",
    "actioned", "manual_action_required", "declined", "escalated", "denied", "paid", "rejected",
}


def elig_state(*, appointment=None, check=None, payer=None, context=None) -> dict:
    return {
        "appointment": appointment,
        "eligibility_check": check,
        "payer": payer if payer is not None else {},
        "context": context if context is not None else {},
    }


def chk_row(status="pending", is_emergency=False) -> dict:
    return {
        "id": "elig1", "organization_id": "o1", "appointment_id": None,
        "is_emergency": is_emergency, "status": status,
    }


def appt_row(is_emergency=False) -> dict:
    return {
        "id": "a1", "organization_id": "o1", "is_emergency": is_emergency,
        "scheduled_at": None if is_emergency else "2026-10-01T00:00:00Z",
    }


def as_tuple(d) -> tuple:
    return (d.action, d.reason_code, d.route_to, d.next_status)


# name, state, trigger, expected (action, reason_code, route_to, next_status)
E_CASES = [
    ("E1 emergency_patient_registered -> 01 fire-and-forget",
     elig_state(context={"is_emergency": True}),
     {"type": "emergency_patient_registered"},
     ("route", "eligibility_emergency_fire_and_forget", "01-eligibility", None)),

    ("E2 appointment_scheduled but row is emergency -> 01 fire-and-forget",
     elig_state(appointment=appt_row(is_emergency=True), context={"is_emergency": True}),
     {"type": "appointment_scheduled"},
     ("route", "eligibility_emergency_fire_and_forget", "01-eligibility", None)),

    ("E3 appointment_scheduled (not emergency) -> 01 ahead of time",
     elig_state(appointment=appt_row(is_emergency=False), context={"is_emergency": False}),
     {"type": "appointment_scheduled"},
     ("route", "eligibility_scheduled_ahead_of_time", "01-eligibility", None)),

    ("E4 emergency check completed / insufficient_info -> recorded, not escalated",
     elig_state(check=chk_row("insufficient_info", is_emergency=True), context={"is_emergency": True}),
     {"type": "eligibility_check_completed"},
     ("no_action", "eligibility_emergency_recorded", None, None)),

    ("E4 emergency check FAILED -> recorded, NOT escalated (contrast E6)",
     elig_state(check=chk_row("check_failed", is_emergency=True), context={"is_emergency": True}),
     {"type": "eligibility_check_failed"},
     ("no_action", "eligibility_emergency_recorded", None, None)),

    ("E5 scheduled check completed / verified_inactive -> recorded",
     elig_state(check=chk_row("verified_inactive", is_emergency=False),
                appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "eligibility_check_completed"},
     ("no_action", "eligibility_scheduled_recorded", None, None)),

    ("E6 scheduled check FAILED -> 12 escalation (operational re-verify)",
     elig_state(check=chk_row("check_failed", is_emergency=False),
                appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "eligibility_check_failed"},
     ("route", "eligibility_check_failed_scheduled", "12-escalation", None)),

    ("E7 malformed emergency state -> recorded, not escalated",
     elig_state(check=None, appointment=None, context={"is_emergency": True}),
     {"type": "eligibility_check_completed"},
     ("no_action", "eligibility_emergency_recorded", None, None)),

    ("E7 malformed scheduled state -> 12 unrecognized",
     elig_state(check=None, appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "eligibility_check_completed"},
     ("route", "eligibility_unrecognized_state", "12-escalation", None)),
]


# claims table regression — the §12.2 dispatch must not perturb R1-R20
def claim_state(status="received", rec=None, issues=None) -> dict:
    return {
        "claim": {"id": "c1", "organization_id": "o1", "status": status},
        "payer": {}, "issues": issues or [], "recommendation": rec,
    }


R_REGRESSION = [
    ("R15 claim.ingested still -> 06", claim_state("received"), {"type": "claim.ingested"},
     ("route", "needs_analysis", "06-analyzer", None)),
    ("R1 terminal denied still -> no_action", claim_state("denied"), {"type": "analysis.completed"},
     ("no_action", "claim_terminal", None, None)),
    ("R9 approved followup still -> 09",
     claim_state("awaiting_approval",
                 {"action_type": "submit_authorization_request", "approval_status": "approved",
                  "low_confidence": False, "confidence": "High"}),
     {"type": "human.approved"},
     ("route", "approved_followup", "09-followup", "executing")),
    ("R10 resubmit_corrected_coding still -> manual, never an executor",
     claim_state("awaiting_approval",
                 {"action_type": "resubmit_corrected_coding", "approval_status": "approved",
                  "low_confidence": False, "confidence": "High"}),
     {"type": "human.approved"},
     ("route", "approved_manual_action", "12-escalation", "manual_action_required")),
]


def run() -> int:
    print("01 eligibility — rule block E1-E7\n")
    for name, state, trigger, expected in E_CASES:
        got = as_tuple(decide(state, trigger))
        check(name, got == expected, f"expected {expected}, got {got}")

    print("\n  claims table R1-R20 regression (must be unchanged)")
    for name, state, trigger, expected in R_REGRESSION:
        got = as_tuple(decide(state, trigger))
        check("    " + name, got == expected, f"expected {expected}, got {got}")
    check("    EXECUTABLE_ACTIONS unchanged",
          EXECUTABLE_ACTIONS == {
              "submit_authorization_request": "09-followup",
              "request_documentation": "09-followup",
              "payer_status_follow_up": "10-reminder",
          })
    check("    MANUAL_ACTIONS unchanged", MANUAL_ACTIONS == {"resubmit_corrected_coding"})

    # ========================================================================
    # THE EMERGENCY-CARE-SAFETY FUZZ
    # docs/agents/00-commander.md §12.3 ; docs/agents/01-eligibility-agent.md §2
    # ========================================================================
    print("\n  emergency-care-safety fuzz — exhaustive over the eligibility state space")

    appt_opts = [None, appt_row(is_emergency=True), appt_row(is_emergency=False)]
    chk_ie_opts = [None, True, False]      # eligibility_check.is_emergency snapshot
    ctx_ie_opts = [None, True, False]      # context.is_emergency (what the orchestrator resolved)
    status_opts = [None] + ELIG_STATUSES

    total = 0
    nondet = 0
    bad_next_status = 0          # next_status is not None                    <-- THE UNIVERSAL ASSERTION
    bad_next_status_claim = 0    # next_status is a claims.status value
    emergency_routed_to_12 = 0   # emergency signal present AND routed to escalation  <-- THE EMERGENCY ASSERTION
    bad_route_target = 0
    bad_reason_code = 0
    bad_action = 0

    for trig_type, appt, chk_ie, ctx_ie, status in itertools.product(
        ELIG_TRIGGERS, appt_opts, chk_ie_opts, ctx_ie_opts, status_opts
    ):
        total += 1
        check_row = None
        if status is not None or chk_ie is not None:
            check_row = chk_row(status or "pending", is_emergency=bool(chk_ie))
        context = {} if ctx_ie is None else {"is_emergency": ctx_ie}
        state = elig_state(appointment=appt, check=check_row, context=context)
        trigger = {"type": trig_type}

        d1 = decide(state, trigger)
        d2 = decide(state, trigger)
        if as_tuple(d1) != as_tuple(d2):
            nondet += 1

        # ---- THE UNIVERSAL ASSERTION -------------------------------------
        # No eligibility decision may ever transition a care object.
        if d1.next_status is not None:
            bad_next_status += 1
        if d1.next_status in CLAIM_STATUSES:
            bad_next_status_claim += 1

        # ---- structural sanity ----------------------------------------------
        if d1.route_to not in ELIG_ROUTE_TARGETS:
            bad_route_target += 1
        if d1.reason_code not in ELIG_REASON_CODES:
            bad_reason_code += 1
        if d1.action not in ("route", "no_action"):
            bad_action += 1

        # ---- THE EMERGENCY ASSERTION --------------------------------------
        # Any emergency signal in the state => the decision never escalates.
        emergency_signal = (
            trig_type == "emergency_patient_registered"
            or (check_row is not None and check_row["is_emergency"] is True)
            or (isinstance(appt, dict) and appt["is_emergency"] is True)
            or ctx_ie is True
        )
        if emergency_signal and d1.route_to == "12-escalation":
            emergency_routed_to_12 += 1

    check(f"decide() is deterministic over all {total} eligibility states", nondet == 0,
          f"{nondet} nondeterministic")
    check(f"next_status is None for ALL {total} eligibility decisions", bad_next_status == 0,
          f"{bad_next_status} decisions returned a non-None next_status")
    check("no eligibility decision emits a claims.status value", bad_next_status_claim == 0,
          f"{bad_next_status_claim} violations")
    check("every emergency-signalled state -> route_to != '12-escalation'",
          emergency_routed_to_12 == 0,
          f"{emergency_routed_to_12} emergency states routed to escalation")
    check("route_to always in {None, 01-eligibility, 12-escalation}", bad_route_target == 0,
          f"{bad_route_target} violations")
    check("reason_code always in the closed eligibility set", bad_reason_code == 0,
          f"{bad_reason_code} violations")
    check("action always route|no_action (never await_human)", bad_action == 0,
          f"{bad_action} violations")

    print()
    print("=" * 60)
    total_checks = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total_checks} CHECKS PASSED — eligibility rule block + care-safety invariant hold.")
        return 0
    print(f"{_PASS}/{total_checks} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
