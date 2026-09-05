#!/usr/bin/env python3
"""00 — Commander rule-table tests.

One case per rule R1-R20 (docs/agents/00-commander.md §6), the key scenarios
from §10, and a determinism fuzz that asserts the execution-agent invariant:
nothing reaches 09/10 without a human.approved trigger + an approved,
non-low-confidence recommendation, and `resubmit_corrected_coding` never reaches
an executor at all.

Stdlib only. Run:  python tests/commander_test.py
"""
from __future__ import annotations

import pathlib
import random
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


def st(status="received", rec=None, issues=None) -> dict:
    return {
        "claim": {"id": "c1", "organization_id": "o1", "status": status},
        "payer": {},
        "issues": [] if issues is None else issues,
        "recommendation": rec,
    }


def rc(action_type="submit_authorization_request", approval_status="pending", low_confidence=False) -> dict:
    return {
        "action_type": action_type,
        "confidence": "Low" if low_confidence else "High",
        "low_confidence": low_confidence,
        "approval_status": approval_status,
    }


ISSUE = {"issue_type": "code_mismatch", "severity": "medium", "description": "x", "evidence": {}}
APPROVED = "human.approved"

# name, state, trigger, (action, reason_code, route_to, next_status)
CASES = [
    ("R1 terminal denied", st("denied"), {"type": "analysis.completed"},
     ("no_action", "claim_terminal", None, None)),
    ("R2 escalated -> human owns", st("escalated"), {"type": "reasoning.completed"},
     ("no_action", "human_owns_claim", None, None)),
    ("R2 manual_action_required -> human owns", st("manual_action_required"), {"type": APPROVED},
     ("no_action", "human_owns_claim", None, None)),
    ("R3 actioned", st("actioned"), {"type": "execution.completed"},
     ("no_action", "already_actioned", None, None)),
    ("R4 executing + non-execution trigger", st("executing"), {"type": "recommendation.completed"},
     ("no_action", "execution_in_progress", None, None)),
    ("R5 agent.error -> escalate", st("analyzed"), {"type": "agent.error"},
     ("route", "agent_error", "12-escalation", "escalated")),
    ("R6 execution.failed -> escalate", st("executing"), {"type": "execution.failed"},
     ("route", "execution_failed", "12-escalation", "escalated")),
    ("R7 approved, no recommendation", st("received"), {"type": APPROVED},
     ("route", "approval_without_recommendation", "12-escalation", "escalated")),
    ("R7 approved, recommendation still pending", st("awaiting_approval", rc(approval_status="pending")),
     {"type": APPROVED},
     ("route", "approval_without_recommendation", "12-escalation", "escalated")),
    ("R7 approved but wrong status", st("received", rc(approval_status="approved")), {"type": APPROVED},
     ("route", "approval_without_recommendation", "12-escalation", "escalated")),
    ("R8 approved but low_confidence",
     st("awaiting_approval", rc(approval_status="approved", low_confidence=True)), {"type": APPROVED},
     ("route", "low_confidence_cannot_be_approved", "12-escalation", "escalated")),
    ("R8 approved low_confidence resubmit_corrected_coding (trace 15)",
     st("awaiting_approval", rc("resubmit_corrected_coding", "approved", low_confidence=True)),
     {"type": APPROVED},
     ("route", "low_confidence_cannot_be_approved", "12-escalation", "escalated")),
    ("R9 approved -> 09 (submit_authorization_request)",
     st("awaiting_approval", rc("submit_authorization_request", "approved")), {"type": APPROVED},
     ("route", "approved_followup", "09-followup", "executing")),
    ("R9 approved -> 09 (request_documentation)",
     st("awaiting_approval", rc("request_documentation", "approved")), {"type": APPROVED},
     ("route", "approved_followup", "09-followup", "executing")),
    ("R9 approved -> 10 (payer_status_follow_up)",
     st("awaiting_approval", rc("payer_status_follow_up", "approved")), {"type": APPROVED},
     ("route", "approved_reminder", "10-reminder", "executing")),
    ("R10 approved -> manual (resubmit_corrected_coding)",
     st("awaiting_approval", rc("resubmit_corrected_coding", "approved")), {"type": APPROVED},
     ("route", "approved_manual_action", "12-escalation", "manual_action_required")),
    ("R11 approved, unknown action_type",
     st("awaiting_approval", rc("bogus_action", "approved")), {"type": APPROVED},
     ("route", "unknown_action_type", "12-escalation", "escalated")),
    ("R12 human.declined -> soft stop", st("awaiting_approval", rc()), {"type": "human.declined"},
     ("no_action", "declined_by_human", None, "declined")),
    ("R13 recommendation.completed low_confidence -> escalate",
     st("reasoned", rc(low_confidence=True)), {"type": "recommendation.completed"},
     ("route", "low_confidence_recommendation", "12-escalation", "escalated")),
    ("R14 recommendation.completed -> await human",
     st("reasoned", rc()), {"type": "recommendation.completed"},
     ("await_human", "awaiting_human_approval", None, "awaiting_approval")),
    ("R15 claim.ingested -> 06", st("received"), {"type": "claim.ingested"},
     ("route", "needs_analysis", "06-analyzer", None)),
    ("R15 claim.reanalyze -> 06", st("cleared"), {"type": "claim.reanalyze"},
     ("route", "needs_analysis", "06-analyzer", None)),
    ("R16 analysis.completed, 0 issues -> cleared", st("received", issues=[]),
     {"type": "analysis.completed"}, ("no_action", "no_issues_found", None, "cleared")),
    ("R17 analysis.completed, issues -> 07", st("received", issues=[ISSUE]),
     {"type": "analysis.completed"}, ("route", "needs_reasoning", "07-reasoning", "analyzed")),
    ("R18 reasoning.completed -> 08", st("analyzed", issues=[ISSUE]), {"type": "reasoning.completed"},
     ("route", "needs_recommendation", "08-recommendation", "reasoned")),
    ("R19 execution.completed -> actioned", st("executing"), {"type": "execution.completed"},
     ("no_action", "execution_complete", None, "actioned")),
    ("R20 impossible ordering -> escalate", st("analyzed"), {"type": "execution.completed"},
     ("route", "unrecognized_state", "12-escalation", "escalated")),
]

HAPPY_PATH = [
    ("received", {"type": "claim.ingested"}, ("route", "needs_analysis", "06-analyzer", None)),
    ("received", {"type": "analysis.completed"}, ("route", "needs_reasoning", "07-reasoning", "analyzed")),
    ("analyzed", {"type": "reasoning.completed"}, ("route", "needs_recommendation", "08-recommendation", "reasoned")),
    ("reasoned", {"type": "recommendation.completed"},
     ("await_human", "awaiting_human_approval", None, "awaiting_approval")),
]


def _as_tuple(d) -> tuple:
    return (d.action, d.reason_code, d.route_to, d.next_status)


def run() -> int:
    print("00 commander — rule table\n")

    for name, state, trigger, expected in CASES:
        got = _as_tuple(decide(state, trigger))
        check(name, got == expected, f"expected {expected}, got {got}")

    print("\n  happy path: received -> ... -> awaiting_approval")
    for status, trigger, expected in HAPPY_PATH:
        issues = [ISSUE] if status in ("received", "analyzed") else []
        rec = rc() if status == "reasoned" else None
        got = _as_tuple(decide(st(status, rec, issues), trigger))
        check(f"    {status} + {trigger['type']}", got == expected, f"expected {expected}, got {got}")

    # ---- determinism + execution-agent invariant fuzz ----
    print("\n  determinism + invariants (2000 random cases)")
    statuses = ["received", "analyzed", "cleared", "reasoned", "awaiting_approval", "executing",
                "actioned", "manual_action_required", "declined", "escalated", "denied", "paid", "rejected"]
    triggers = ["claim.ingested", "claim.reanalyze", "analysis.completed", "reasoning.completed",
                "recommendation.completed", "human.approved", "human.declined", "execution.completed",
                "execution.failed", "agent.error", "totally.bogus"]
    actions = list(EXECUTABLE_ACTIONS) + list(MANUAL_ACTIONS) + ["bogus_action"]
    rng = random.Random(20260902)
    nondet = bad_exec = bad_resubmit = 0

    for _ in range(2000):
        status = rng.choice(statuses)
        has_rec = rng.random() < 0.7
        rec = None
        if has_rec:
            rec = rc(
                action_type=rng.choice(actions),
                approval_status=rng.choice(["pending", "approved", "declined"]),
                low_confidence=rng.random() < 0.4,
            )
        issues = [ISSUE] * rng.randint(0, 3)
        state = st(status, rec, issues)
        trigger = {"type": rng.choice(triggers)}

        d1 = decide(state, trigger)
        d2 = decide(state, trigger)
        if _as_tuple(d1) != _as_tuple(d2):
            nondet += 1

        if d1.route_to in ("09-followup", "10-reminder"):
            ok = (
                trigger["type"] == "human.approved"
                and isinstance(rec, dict)
                and rec["approval_status"] == "approved"
                and rec["low_confidence"] is False
                and rec["action_type"] in EXECUTABLE_ACTIONS
            )
            if not ok:
                bad_exec += 1

        if isinstance(rec, dict) and rec["action_type"] == "resubmit_corrected_coding":
            if d1.route_to in ("09-followup", "10-reminder"):
                bad_resubmit += 1

    check("decide is deterministic over 2000 random cases", nondet == 0, f"{nondet} nondeterministic")
    check("no route to 09/10 without approved non-low-confidence human.approved", bad_exec == 0,
          f"{bad_exec} violations")
    check("resubmit_corrected_coding never routes to an executor", bad_resubmit == 0,
          f"{bad_resubmit} violations")

    print()
    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — Commander rule table holds.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
