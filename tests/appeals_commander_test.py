#!/usr/bin/env python3
"""11 — appeals rule block (AP1-AP12) tests + the two structural-invariant fuzzes.

Companion to commander_test.py / eligibility_commander_test.py /
prior_auth_commander_test.py. Covers the same categories:

  * one case per rule AP1-AP12 (docs/agents/00-commander.md §14.5);
  * the claims table R1-R20, the eligibility table E1-E7, and the prior-auth
    table A1-A11 are ALL untouched by the fourth dispatch (regression cases);
  * THE TWO STRUCTURAL INVARIANTS (00-commander.md §14.3):
      1. the human-approval gate — `route_to == "11-appeals"` with
         `reason_code == "appeal_submit"` occurs on EXACTLY one rule (AP6), and
         only when `trigger.type == "appeal_submission_approved"` AND
         `appeal.status == "drafted"`;
      2. the single claim transition — `next_status is not None` occurs on
         EXACTLY one rule (AP9), and only as the exact tuple
         (`appeal_won_claim_reversed`, `paid`), on
         `trigger.type == "appeal_resolution_received"` with the resolution
         `approved`. `next_status` is otherwise ALWAYS None.
  * named test_appeal_never_submits_without_a_recorded_approval and
    test_appeal_touches_claims_status_only_on_a_win.

The fuzz is exhaustive over
    trigger (7) x appeal.status (11 incl. none) x appeal.resolution (4)
      x previous_appeal_id (2)  = 616 states,
each decided twice to also assert determinism.

Stdlib only.  Run:  python tests/appeals_commander_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.commander import (  # noqa: E402
    APPEAL_TRIGGERS,
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


# --- the closed sets this phase adds (00-commander.md §14.4 / §14.5) ----------
AP_TRIGGERS = [
    "claim_denied",
    "appeal_resubmitted",
    "appeal_drafted",
    "appeal_submission_approved",
    "appeal_submission_declined",
    "appeal_resolution_received",
    "appeal_error",
]
AP_REASON_CODES = {
    "appeal_already_in_progress",
    "appeal_draft",
    "appeal_no_basis_needs_human",
    "appeal_awaiting_submission_approval",
    "appeal_draft_unrecognized",
    "appeal_submit",
    "appeal_approval_without_draft",
    "appeal_submission_declined_recorded",
    "appeal_won_claim_reversed",
    "appeal_exhausted_needs_human",
    "appeal_agent_error",
    "appeal_unrecognized_state",
}
AP_ROUTE_TARGETS = {None, "11-appeals", "12-escalation"}
AP_STATUSES = [
    "pending", "drafted", "insufficient_basis", "submission_declined", "submitting",
    "submitted", "appeal_approved", "appeal_partial", "appeal_denied", "error",
]
AP_RESOLUTIONS = [None, "approved", "partial", "denied"]

# every claims.status enum value except `paid` — an appeal decision must never
# emit one as next_status (only AP9 -> "paid" is allowed)
CLAIM_STATUSES_NOT_PAID = {
    "received", "analyzed", "cleared", "reasoned", "awaiting_approval", "executing",
    "actioned", "manual_action_required", "declined", "escalated", "denied", "rejected",
}


def ap_state(*, appeal=None) -> dict:
    return {"claim": {"id": "c1", "organization_id": "o1", "status": "denied"},
            "payer": {}, "appeal": appeal}


def ap_row(status="pending", *, resolution=None, previous=None, reviewed_by=None) -> dict:
    return {
        "id": "ap1", "organization_id": "o1", "claim_id": "c1", "status": status,
        "previous_appeal_id": previous, "reviewed_by": reviewed_by,
        "resolution_payload": {} if resolution is None else {"outcome": resolution},
    }


def as_tuple(d) -> tuple:
    return (d.action, d.reason_code, d.route_to, d.next_status)


# name, state, trigger, expected (action, reason_code, route_to, next_status)
AP_CASES = [
    ("AP1 claim_denied while an appeal is already live -> no-op",
     ap_state(appeal=ap_row("drafted")), {"type": "claim_denied"},
     ("no_action", "appeal_already_in_progress", None, None)),

    ("AP2 claim_denied, no appeal yet -> route 11 (draft)",
     ap_state(appeal=None), {"type": "claim_denied"},
     ("route", "appeal_draft", "11-appeals", None)),

    ("AP2 appeal_resubmitted (fresh pending row) -> route 11 (draft), AP1 skipped",
     ap_state(appeal=ap_row("pending", previous="ap0")), {"type": "appeal_resubmitted"},
     ("route", "appeal_draft", "11-appeals", None)),

    ("AP3 appeal_drafted + insufficient_basis -> route 12",
     ap_state(appeal=ap_row("insufficient_basis")), {"type": "appeal_drafted"},
     ("route", "appeal_no_basis_needs_human", "12-escalation", None)),

    ("AP4 appeal_drafted + drafted -> await_human",
     ap_state(appeal=ap_row("drafted")), {"type": "appeal_drafted"},
     ("await_human", "appeal_awaiting_submission_approval", None, None)),

    ("AP5 appeal_drafted + a bad status -> route 12 (unrecognized)",
     ap_state(appeal=ap_row("pending")), {"type": "appeal_drafted"},
     ("route", "appeal_draft_unrecognized", "12-escalation", None)),

    ("AP6 appeal_submission_approved + drafted -> route 11 (submit)",
     ap_state(appeal=ap_row("drafted", reviewed_by="u1")),
     {"type": "appeal_submission_approved"},
     ("route", "appeal_submit", "11-appeals", None)),

    ("AP7 appeal_submission_approved + NOT drafted (double click) -> route 12, never submits",
     ap_state(appeal=ap_row("submitted")), {"type": "appeal_submission_approved"},
     ("route", "appeal_approval_without_draft", "12-escalation", None)),

    ("AP8 appeal_submission_declined -> recorded, stop",
     ap_state(appeal=ap_row("drafted")), {"type": "appeal_submission_declined"},
     ("no_action", "appeal_submission_declined_recorded", None, None)),

    ("AP9 appeal_resolution_received + approved -> no_action, next_status paid",
     ap_state(appeal=ap_row("appeal_approved", resolution="approved")),
     {"type": "appeal_resolution_received"},
     ("no_action", "appeal_won_claim_reversed", None, "paid")),

    ("AP10 appeal_resolution_received + partial -> route 12, claim stays denied",
     ap_state(appeal=ap_row("appeal_partial", resolution="partial")),
     {"type": "appeal_resolution_received"},
     ("route", "appeal_exhausted_needs_human", "12-escalation", None)),

    ("AP10 appeal_resolution_received + denied -> route 12, claim stays denied",
     ap_state(appeal=ap_row("appeal_denied", resolution="denied")),
     {"type": "appeal_resolution_received"},
     ("route", "appeal_exhausted_needs_human", "12-escalation", None)),

    ("AP11 appeal_error -> route 12 (agent error)",
     ap_state(appeal=ap_row("error")), {"type": "appeal_error"},
     ("route", "appeal_agent_error", "12-escalation", None)),

    ("AP12 appeal_resolution_received with no appeal row -> route 12 (unrecognized)",
     ap_state(appeal=None), {"type": "appeal_resolution_received"},
     ("route", "appeal_unrecognized_state", "12-escalation", None)),
]


# --- regression: the §14.2 dispatch must not perturb R1-R20 / E1-E7 / A1-A11 --
def claim_state(status="received", rec=None, issues=None) -> dict:
    return {"claim": {"id": "c1", "organization_id": "o1", "status": status},
            "payer": {}, "issues": issues or [], "recommendation": rec}


def elig_state(*, appointment=None, check=None, context=None) -> dict:
    return {"appointment": appointment, "eligibility_check": check, "payer": {},
            "context": context or {}}


def pa_state(*, appointment=None, pa=None, context=None) -> dict:
    return {"appointment": appointment, "prior_authorization": pa, "payer": {},
            "context": context or {}}


REGRESSION = [
    ("R15 claim.ingested still -> 06", claim_state("received"), {"type": "claim.ingested"},
     ("route", "needs_analysis", "06-analyzer", None)),
    ("R1 terminal denied still -> no_action (claims table)", claim_state("denied"),
     {"type": "analysis.completed"}, ("no_action", "claim_terminal", None, None)),
    ("R9 approved followup still -> 09",
     claim_state("awaiting_approval",
                 {"action_type": "submit_authorization_request", "approval_status": "approved",
                  "low_confidence": False, "confidence": "High"}),
     {"type": "human.approved"},
     ("route", "approved_followup", "09-followup", "executing")),
    ("E1 emergency_patient_registered still -> 01 fire-and-forget",
     elig_state(context={"is_emergency": True}), {"type": "emergency_patient_registered"},
     ("route", "eligibility_emergency_fire_and_forget", "01-eligibility", None)),
    ("A3 prior_auth_requested (scheduled) still -> 02 determine",
     pa_state(pa={"id": "pa1", "is_emergency": False, "place_of_service": "office",
                  "status": "pending", "response_payload": {}},
              appointment={"id": "a1", "is_emergency": False}, context={"is_emergency": False}),
     {"type": "prior_auth_requested"},
     ("route", "prior_auth_determine", "02-prior-auth", None)),
]


def run() -> int:  # noqa: PLR0915
    print("11 appeals — rule block AP1-AP12\n")
    for name, state, trigger, expected in AP_CASES:
        got = as_tuple(decide(state, trigger))
        check(name, got == expected, f"expected {expected}, got {got}")

    print("\n  claims R1-R20 + eligibility E1-E7 + prior-auth A1-A11 regression (must be unchanged)")
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
    check("    APPEAL_TRIGGERS disjoint from ELIGIBILITY_TRIGGERS and PRIOR_AUTH_TRIGGERS",
          APPEAL_TRIGGERS.isdisjoint(ELIGIBILITY_TRIGGERS)
          and APPEAL_TRIGGERS.isdisjoint(PRIOR_AUTH_TRIGGERS))

    # ========================================================================
    # THE STRUCTURAL-INVARIANT FUZZ — 00-commander.md §14.3
    # ========================================================================
    print("\n  structural-invariant fuzz — exhaustive over the appeal state space")

    status_opts = [None] + AP_STATUSES
    total = nondet = 0
    bad_next_status = 0            # next_status not in {None, "paid"}
    bad_next_status_leak = 0       # next_status is a non-paid claim status
    submit_without_gate = 0        # appeal_submit not fully gated
    win_without_gate = 0          # next_status "paid" not fully gated
    bad_route_target = bad_reason_code = bad_action = 0
    submit_decisions: list[tuple] = []
    paid_decisions: list[tuple] = []

    for trig_type, status, resolution, has_prev in itertools.product(
        AP_TRIGGERS, status_opts, AP_RESOLUTIONS, (False, True)
    ):
        total += 1
        appeal = None
        if status is not None:
            appeal = ap_row(status, resolution=resolution,
                            previous="ap0" if has_prev else None,
                            reviewed_by="u1")
        state = ap_state(appeal=appeal)
        trigger = {"type": trig_type}

        d1 = decide(state, trigger)
        d2 = decide(state, trigger)
        if as_tuple(d1) != as_tuple(d2):
            nondet += 1

        # ---- invariant 2: the single claim transition ----------------------
        if d1.next_status not in (None, "paid"):
            bad_next_status += 1
        if d1.next_status in CLAIM_STATUSES_NOT_PAID:
            bad_next_status_leak += 1
        if d1.next_status is not None:
            paid_decisions.append((trig_type, status, resolution, as_tuple(d1)))
            if not (
                d1.reason_code == "appeal_won_claim_reversed"
                and d1.next_status == "paid"
                and trig_type == "appeal_resolution_received"
                and resolution == "approved"
            ):
                win_without_gate += 1

        # ---- invariant 1: the human-approval gate --------------------------
        if d1.route_to == "11-appeals" and d1.reason_code == "appeal_submit":
            submit_decisions.append((trig_type, status, as_tuple(d1)))
            if not (trig_type == "appeal_submission_approved" and status == "drafted"):
                submit_without_gate += 1

        # ---- structural sanity --------------------------------------------
        if d1.route_to not in AP_ROUTE_TARGETS:
            bad_route_target += 1
        if d1.reason_code not in AP_REASON_CODES:
            bad_reason_code += 1
        if d1.action not in ("route", "no_action", "await_human"):
            bad_action += 1

    check(f"decide() is deterministic over all {total} appeal states", nondet == 0,
          f"{nondet} nondeterministic")
    check(f"next_status in {{None, 'paid'}} for ALL {total} appeal decisions", bad_next_status == 0,
          f"{bad_next_status} decisions returned a forbidden next_status")
    check("no appeal decision emits a non-paid claim status", bad_next_status_leak == 0,
          f"{bad_next_status_leak} violations")
    check("route_to always in {None, 11-appeals, 12-escalation}", bad_route_target == 0,
          f"{bad_route_target} violations")
    check("reason_code always in the closed appeal set", bad_reason_code == 0,
          f"{bad_reason_code} violations")
    check("action always route|no_action|await_human", bad_action == 0, f"{bad_action} violations")

    print(f"\n  test_appeal_never_submits_without_a_recorded_approval "
          f"({len(submit_decisions)} appeal_submit decisions enumerated)")
    check("  every appeal_submit decision requires trigger appeal_submission_approved + status drafted",
          submit_without_gate == 0, f"{submit_without_gate} ungated submits")
    check("  appeal_submit appears on EXACTLY one rule shape (AP6)",
          len({s[0] for s in submit_decisions}) <= 1
          and all(s[0] == "appeal_submission_approved" for s in submit_decisions),
          str(submit_decisions[:5]))

    print(f"\n  test_appeal_touches_claims_status_only_on_a_win "
          f"({len(paid_decisions)} next_status decisions enumerated)")
    check("  every non-None next_status is exactly ('appeal_won_claim_reversed', 'paid') on an "
          "approved resolution", win_without_gate == 0, f"{win_without_gate} ungated transitions")
    check("  next_status 'paid' appears on EXACTLY one rule shape (AP9)",
          all(p[0] == "appeal_resolution_received" and p[2] == "approved" for p in paid_decisions),
          str(paid_decisions[:5]))

    print()
    print("=" * 60)
    total_checks = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total_checks} CHECKS PASSED — appeals rule block + both structural invariants hold.")
        return 0
    print(f"{_PASS}/{total_checks} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
