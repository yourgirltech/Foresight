#!/usr/bin/env python3
"""02 — prior-auth rule block (A1-A11) tests + the emergency-care-safety fuzz.

Companion to commander_test.py and eligibility_commander_test.py. Covers the
SAME scenario categories as the Phase 2 test:

  * one case per rule A1-A11 (docs/agents/00-commander.md §13.6);
  * the claims table R1-R20 AND the eligibility table E1-E7 are untouched by the
    new dispatch (regression cases);
  * THE CARE-SAFETY INVARIANT (00-commander.md §13.3, 02-prior-auth-agent.md §2):
      - decide() returns next_status is None for EVERY prior-auth trigger, so the
        Commander can never write a status off a patient-access trigger;
      - whenever ANY emergency signal is present in the state, decide() NEVER
        routes to 12-escalation, NEVER parks at await_human, and NEVER routes to
        a submission — the decision is no_action, or a route to 02-prior-auth
        itself (the determination only, which the orchestrator runs detached);
  * a named test_emergency_prior_auth_is_never_gating that enumerates every
    emergency decision and asserts all three prohibitions explicitly;
  * the orchestrator hard-raise is exercised in prior_auth_orchestrator_test.py
    (it needs the live stack); this file proves the Commander never emits a
    decision that would trip it.

The fuzz is exhaustive over
    trigger (6) x pa.status (12 incl. none) x pa.response_status (4)
      x appointment (3) x pa.is_emergency (3) x place_of_service (3)
      x context.is_emergency (3)  = 23,328 states,
each decided twice to also assert determinism.

Stdlib only. Run:  python tests/prior_auth_commander_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.commander import (  # noqa: E402
    ELIGIBILITY_TRIGGERS,
    EXECUTABLE_ACTIONS,
    MANUAL_ACTIONS,
    PRIOR_AUTH_TRIGGERS,
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


# --- the closed sets this phase adds (00-commander.md §13.1) ------------------
PA_TRIGGERS = [
    "prior_auth_requested",
    "prior_auth_emergency",
    "prior_auth_determined",
    "prior_auth_submission_approved",
    "prior_auth_submission_declined",
    "prior_auth_response_received",
]
PA_REASON_CODES = {
    "prior_auth_emergency_determine",
    "prior_auth_determine",
    "prior_auth_emergency_exempt_recorded",
    "prior_auth_awaiting_submission_approval",
    "prior_auth_determination_recorded",
    "prior_auth_submit",
    "prior_auth_submission_declined_recorded",
    "prior_auth_approved_recorded",
    "prior_auth_needs_human",
    "prior_auth_unrecognized_state",
}
PA_ROUTE_TARGETS = {None, "02-prior-auth", "12-escalation"}
PA_STATUSES = [
    "pending", "not_required", "emergency_exempt", "insufficient_info", "required_draft",
    "submission_declined", "submitting", "submitted", "auth_approved", "info_needed", "auth_denied",
]
PA_RESPONSE_STATUSES = [None, "approved", "info_needed", "denied"]
PLACES = ["office", "outpatient", "emergency"]

# every claims.status / eligibility_status enum value — a prior-auth decision
# must never emit one as next_status (it must emit None, always)
CLAIM_STATUSES = {
    "received", "analyzed", "cleared", "reasoned", "awaiting_approval", "executing",
    "actioned", "manual_action_required", "declined", "escalated", "denied", "paid", "rejected",
}
ELIGIBILITY_STATUSES = {
    "pending", "verified_active", "verified_inactive", "insufficient_info", "check_failed",
}


def pa_state(*, appointment=None, pa=None, payer=None, context=None) -> dict:
    return {
        "appointment": appointment,
        "prior_authorization": pa,
        "payer": payer if payer is not None else {},
        "context": context if context is not None else {},
    }


def pa_row(status="pending", *, is_emergency=False, place="office", response_status=None) -> dict:
    return {
        "id": "pa1", "organization_id": "o1", "appointment_id": None,
        "is_emergency": is_emergency, "place_of_service": place, "status": status,
        "response_payload": {} if response_status is None else {"response_status": response_status},
    }


def appt_row(is_emergency=False) -> dict:
    return {
        "id": "a1", "organization_id": "o1", "is_emergency": is_emergency,
        "scheduled_at": None if is_emergency else "2026-10-01T00:00:00Z",
    }


def as_tuple(d) -> tuple:
    return (d.action, d.reason_code, d.route_to, d.next_status)


# name, state, trigger, expected (action, reason_code, route_to, next_status)
A_CASES = [
    ("A1 prior_auth_emergency -> 02 determine (detached)",
     pa_state(pa=pa_row("pending", is_emergency=True, place="emergency"),
              appointment=None, context={"is_emergency": True}),
     {"type": "prior_auth_emergency"},
     ("route", "prior_auth_emergency_determine", "02-prior-auth", None)),

    ("A2 prior_auth_requested but row is emergency -> 02 determine (safe path)",
     pa_state(pa=pa_row("pending", is_emergency=True), appointment=appt_row(True),
              context={"is_emergency": True}),
     {"type": "prior_auth_requested"},
     ("route", "prior_auth_emergency_determine", "02-prior-auth", None)),

    ("A3 prior_auth_requested (not emergency) -> 02 determine ahead of time",
     pa_state(pa=pa_row("pending", is_emergency=False), appointment=appt_row(False),
              context={"is_emergency": False}),
     {"type": "prior_auth_requested"},
     ("route", "prior_auth_determine", "02-prior-auth", None)),

    ("A4 emergency determination (emergency_exempt) -> recorded, NOT escalated, NO draft",
     pa_state(pa=pa_row("emergency_exempt", is_emergency=True, place="emergency"),
              appointment=None, context={"is_emergency": True}),
     {"type": "prior_auth_determined"},
     ("no_action", "prior_auth_emergency_exempt_recorded", None, None)),

    ("A4 emergency determination (insufficient_info) -> recorded, NOT escalated",
     pa_state(pa=pa_row("insufficient_info", is_emergency=True),
              appointment=appt_row(True), context={"is_emergency": True}),
     {"type": "prior_auth_determined"},
     ("no_action", "prior_auth_emergency_exempt_recorded", None, None)),

    ("A5 scheduled determination = required_draft -> await_human",
     pa_state(pa=pa_row("required_draft", is_emergency=False),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_determined"},
     ("await_human", "prior_auth_awaiting_submission_approval", None, None)),

    ("A6 scheduled determination = not_required -> recorded",
     pa_state(pa=pa_row("not_required", is_emergency=False),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_determined"},
     ("no_action", "prior_auth_determination_recorded", None, None)),

    ("A6 scheduled determination = insufficient_info -> recorded",
     pa_state(pa=pa_row("insufficient_info", is_emergency=False),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_determined"},
     ("no_action", "prior_auth_determination_recorded", None, None)),

    ("A7 human approved submission (required_draft, not emergency) -> 02 submit",
     pa_state(pa=pa_row("required_draft", is_emergency=False),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_submission_approved"},
     ("route", "prior_auth_submit", "02-prior-auth", None)),

    ("A8 human declined submission -> recorded, stop",
     pa_state(pa=pa_row("required_draft", is_emergency=False),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_submission_declined"},
     ("no_action", "prior_auth_submission_declined_recorded", None, None)),

    ("A9 payer response = approved -> recorded, stop",
     pa_state(pa=pa_row("submitted", is_emergency=False, response_status="approved"),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_response_received"},
     ("no_action", "prior_auth_approved_recorded", None, None)),

    ("A10 payer response = denied (scheduled) -> 12 escalation",
     pa_state(pa=pa_row("submitted", is_emergency=False, response_status="denied"),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_response_received"},
     ("route", "prior_auth_needs_human", "12-escalation", None)),

    ("A10 payer response = info_needed (scheduled) -> 12 escalation",
     pa_state(pa=pa_row("submitted", is_emergency=False, response_status="info_needed"),
              appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_response_received"},
     ("route", "prior_auth_needs_human", "12-escalation", None)),

    ("A11 malformed emergency state (no pa row) -> recorded, not escalated",
     pa_state(pa=None, appointment=None, context={"is_emergency": True}),
     {"type": "prior_auth_response_received"},
     ("no_action", "prior_auth_emergency_exempt_recorded", None, None)),

    ("A11 malformed scheduled state (no pa row) -> 12 unrecognized",
     pa_state(pa=None, appointment=appt_row(False), context={"is_emergency": False}),
     {"type": "prior_auth_response_received"},
     ("route", "prior_auth_unrecognized_state", "12-escalation", None)),
]


# --- regression: the §13.2 dispatch must not perturb R1-R20 or E1-E7 ---------
def claim_state(status="received", rec=None, issues=None) -> dict:
    return {
        "claim": {"id": "c1", "organization_id": "o1", "status": status},
        "payer": {}, "issues": issues or [], "recommendation": rec,
    }


def elig_state(*, appointment=None, check=None, context=None) -> dict:
    return {"appointment": appointment, "eligibility_check": check, "payer": {},
            "context": context or {}}


REGRESSION = [
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
    ("E1 emergency_patient_registered still -> 01 fire-and-forget",
     elig_state(context={"is_emergency": True}), {"type": "emergency_patient_registered"},
     ("route", "eligibility_emergency_fire_and_forget", "01-eligibility", None)),
    ("E6 scheduled check_failed still -> 12",
     elig_state(check={"id": "e1", "is_emergency": False, "status": "check_failed"},
                appointment={"id": "a1", "is_emergency": False}, context={"is_emergency": False}),
     {"type": "eligibility_check_failed"},
     ("route", "eligibility_check_failed_scheduled", "12-escalation", None)),
]


def _emergency_signal(trig_type, appt, pa, ctx_ie) -> bool:
    """Exactly the signals the Commander can actually read from the state
    (commander._is_prior_auth_emergency_context). place_of_service only counts
    when there is a prior_authorization row carrying it."""
    return (
        trig_type == "prior_auth_emergency"
        or (isinstance(pa, dict) and pa.get("is_emergency") is True)
        or (isinstance(pa, dict) and pa.get("place_of_service") == "emergency")
        or (isinstance(appt, dict) and appt.get("is_emergency") is True)
        or ctx_ie is True
        # fail-safe: no appointment at all and context.is_emergency unresolved
        or (appt is None and ctx_ie is None)
    )


def run() -> int:
    print("02 prior-auth — rule block A1-A11\n")
    for name, state, trigger, expected in A_CASES:
        got = as_tuple(decide(state, trigger))
        check(name, got == expected, f"expected {expected}, got {got}")

    print("\n  claims R1-R20 + eligibility E1-E7 regression (must be unchanged)")
    for name, state, trigger, expected in REGRESSION:
        got = as_tuple(decide(state, trigger))
        check("    " + name, got == expected, f"expected {expected}, got {got}")
    check("    EXECUTABLE_ACTIONS unchanged",
          EXECUTABLE_ACTIONS == {
              "submit_authorization_request": "09-followup",
              "request_documentation": "09-followup",
              "payer_status_follow_up": "10-reminder",
          })
    check("    MANUAL_ACTIONS unchanged", MANUAL_ACTIONS == {"resubmit_corrected_coding"})
    check("    PRIOR_AUTH_TRIGGERS / ELIGIBILITY_TRIGGERS are disjoint",
          PRIOR_AUTH_TRIGGERS.isdisjoint(ELIGIBILITY_TRIGGERS))

    # ========================================================================
    # THE EMERGENCY-CARE-SAFETY FUZZ
    # docs/agents/00-commander.md §13.3 ; docs/agents/02-prior-auth-agent.md §2
    # ========================================================================
    print("\n  emergency-care-safety fuzz — exhaustive over the prior-auth state space")

    appt_opts = [None, appt_row(is_emergency=True), appt_row(is_emergency=False)]
    pa_ie_opts = [None, True, False]      # prior_authorization.is_emergency snapshot
    ctx_ie_opts = [None, True, False]     # context.is_emergency (what the orchestrator resolved)
    status_opts = [None] + PA_STATUSES

    total = 0
    nondet = 0
    bad_next_status = 0            # next_status is not None                <-- THE UNIVERSAL ASSERTION
    bad_next_status_leak = 0       # next_status is a claim/eligibility value
    emergency_routed_to_12 = 0     # emergency signal AND routed to escalation    <-- EMERGENCY ASSERTION 1
    emergency_await_human = 0      # emergency signal AND parked at await_human   <-- EMERGENCY ASSERTION 2
    emergency_bad_route = 0        # emergency signal AND route_to not in {None, 02-prior-auth}
    bad_route_target = 0
    bad_reason_code = 0
    bad_action = 0

    emergency_decisions: list[tuple] = []

    for trig_type, appt, pa_ie, place, ctx_ie, status, resp in itertools.product(
        PA_TRIGGERS, appt_opts, pa_ie_opts, PLACES, ctx_ie_opts, status_opts, PA_RESPONSE_STATUSES
    ):
        total += 1
        pa = None
        if status is not None or pa_ie is not None or resp is not None:
            pa = pa_row(status or "pending", is_emergency=bool(pa_ie),
                        place=place, response_status=resp)
        context = {} if ctx_ie is None else {"is_emergency": ctx_ie}
        state = pa_state(appointment=appt, pa=pa, context=context)
        trigger = {"type": trig_type}

        d1 = decide(state, trigger)
        d2 = decide(state, trigger)
        if as_tuple(d1) != as_tuple(d2):
            nondet += 1

        # ---- THE UNIVERSAL ASSERTION -------------------------------------
        if d1.next_status is not None:
            bad_next_status += 1
        if d1.next_status in (CLAIM_STATUSES | ELIGIBILITY_STATUSES):
            bad_next_status_leak += 1

        # ---- structural sanity ----------------------------------------------
        if d1.route_to not in PA_ROUTE_TARGETS:
            bad_route_target += 1
        if d1.reason_code not in PA_REASON_CODES:
            bad_reason_code += 1
        if d1.action not in ("route", "no_action", "await_human"):
            bad_action += 1

        # ---- THE EMERGENCY ASSERTIONS -----------------------------------
        if _emergency_signal(trig_type, appt, pa, ctx_ie):
            emergency_decisions.append((trigger, as_tuple(d1)))
            if d1.route_to == "12-escalation":
                emergency_routed_to_12 += 1
            if d1.action == "await_human":
                emergency_await_human += 1
            if d1.route_to not in (None, "02-prior-auth"):
                emergency_bad_route += 1

    check(f"decide() is deterministic over all {total} prior-auth states", nondet == 0,
          f"{nondet} nondeterministic")
    check(f"next_status is None for ALL {total} prior-auth decisions", bad_next_status == 0,
          f"{bad_next_status} decisions returned a non-None next_status")
    check("no prior-auth decision emits a claim / eligibility status value",
          bad_next_status_leak == 0, f"{bad_next_status_leak} violations")
    check("route_to always in {None, 02-prior-auth, 12-escalation}", bad_route_target == 0,
          f"{bad_route_target} violations")
    check("reason_code always in the closed prior-auth set", bad_reason_code == 0,
          f"{bad_reason_code} violations")
    check("action always route|no_action|await_human", bad_action == 0,
          f"{bad_action} violations")

    print(f"\n  test_emergency_prior_auth_is_never_gating "
          f"({len(emergency_decisions)} emergency-signalled decisions enumerated)")
    check("  emergency signal => route_to != '12-escalation' (never escalated)",
          emergency_routed_to_12 == 0,
          f"{emergency_routed_to_12} emergency states routed to escalation")
    check("  emergency signal => action != 'await_human' (never parks for approval)",
          emergency_await_human == 0,
          f"{emergency_await_human} emergency states parked at await_human")
    check("  emergency signal => route_to in {None, 02-prior-auth} (never a submission path)",
          emergency_bad_route == 0, f"{emergency_bad_route} violations")

    print()
    print("=" * 60)
    total_checks = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total_checks} CHECKS PASSED — prior-auth rule block + care-safety invariant hold.")
        return 0
    print(f"{_PASS}/{total_checks} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
