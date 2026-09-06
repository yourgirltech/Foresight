"""00 — Commander: the single decision node.

Pure function. No tools, no I/O, no LLM. Same (state, trigger) in -> same
CommanderDecision out. The decision is the first matching row of the ordered
rule table in docs/agents/00-commander.md §6 (R1-R20). Terminal and safety
guards sit at the top; the fallthrough (R20) escalates rather than falling off
the end silently.

The invariant this enforces: R9 is the ONLY rule that routes to an execution
agent (09/10), it sits behind R7 (must have an `approved` recommendation on an
`awaiting_approval` claim) and R8 (must not be low-confidence), and the one
action that resubmits a claim to a payer (`resubmit_corrected_coding`) has no
agent-execution path at all — R10 hands it to a human via 12.

Phase 2 adds a SECOND, disjoint rule table for eligibility verification
(E1-E7, docs/agents/00-commander.md §12 and docs/agents/01-eligibility-agent.md).
`decide()` dispatches to `_decide_eligibility` on the first line when the trigger
is in ELIGIBILITY_TRIGGERS, before R1. The care-safety invariant it enforces:
every E-rule returns `next_status is None` (the Commander never transitions a
care object off an eligibility trigger), and no rule reachable with an emergency
signal routes to `12-escalation`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["route", "await_human", "no_action"]

# --- rule-table constants (kept in lockstep with 00-commander.md §6) ----------
TERMINAL_EXTERNAL = {"denied", "paid", "rejected"}
HUMAN_OWNS = {"escalated", "manual_action_required"}
EXECUTION_TRIGGERS = {"execution.completed", "execution.failed", "agent.error"}

# action_type -> the agent that executes it, after human approval
EXECUTABLE_ACTIONS: dict[str, str] = {
    "submit_authorization_request": "09-followup",
    "request_documentation": "09-followup",
    "payer_status_follow_up": "10-reminder",
}
# action_type -> approved, but NO agent executes it; a human does (§6.3.1)
MANUAL_ACTIONS = {"resubmit_corrected_coding"}

REANALYZE_TRIGGERS = {"claim.ingested", "claim.reanalyze"}

# --- Phase 2: the eligibility trigger family (00-commander.md §12) -------------
# A disjoint rule table (E1-E7). decide() dispatches here BEFORE R1; a claims
# trigger never reaches an E-rule and an eligibility trigger never reaches R1.
ELIGIBILITY_TRIGGERS = {
    "appointment_scheduled",
    "emergency_patient_registered",
    "eligibility_check_completed",
    "eligibility_check_failed",
}
ELIGIBILITY_COMPLETION_TRIGGERS = {"eligibility_check_completed", "eligibility_check_failed"}


@dataclass(frozen=True)
class CommanderDecision:
    action: Action
    reason_code: str
    route_to: str | None = None
    next_status: str | None = None


def _is_emergency_context(state: dict, trigger: dict) -> bool:
    """Resolve whether this eligibility trigger concerns emergency care.
    Ambiguity ALWAYS resolves to True — the non-blocking path (00-commander.md
    §12.4). The only way to get False is an unambiguous, appointment-backed,
    non-emergency booking."""
    if (trigger or {}).get("type") == "emergency_patient_registered":
        return True
    chk = state.get("eligibility_check")
    if isinstance(chk, dict) and chk.get("is_emergency") is True:
        return True
    appt = state.get("appointment")
    if isinstance(appt, dict) and appt.get("is_emergency") is True:
        return True
    ctx = state.get("context") or {}
    if ctx.get("is_emergency") is True:
        return True
    # fail-safe: no appointment at all, or the flag was never resolved
    if appt is None and ctx.get("is_emergency") is None:
        return True
    return False


def _decide_eligibility(state: dict, trigger: dict) -> CommanderDecision:
    """The eligibility rule table E1-E7 (00-commander.md §12.6). Pure.

    INVARIANT: every return here has next_status=None — the Commander never
    transitions an appointment / encounter / care object off an eligibility
    trigger. And no branch reachable with `emergency` True routes to
    12-escalation.
    """
    ttype = (trigger or {}).get("type")
    emergency = _is_emergency_context(state, trigger)
    has_check = isinstance(state.get("eligibility_check"), dict)

    # E1 — an emergency registration is fire-and-forget, before anything else
    if ttype == "emergency_patient_registered":
        return CommanderDecision(
            "route", "eligibility_emergency_fire_and_forget", "01-eligibility", None
        )

    # E2 — a scheduled trigger on an emergency-flagged row: take the safe path
    if ttype == "appointment_scheduled" and emergency:
        return CommanderDecision(
            "route", "eligibility_emergency_fire_and_forget", "01-eligibility", None
        )

    # E3 — a normal scheduled appointment: verify ahead of time
    if ttype == "appointment_scheduled":
        return CommanderDecision(
            "route", "eligibility_scheduled_ahead_of_time", "01-eligibility", None
        )

    # E4 — ANY emergency completion/failure: record and stop. verified_active,
    # verified_inactive, insufficient_info and check_failed are all handled
    # identically. Never an escalation.
    if ttype in ELIGIBILITY_COMPLETION_TRIGGERS and emergency and has_check:
        return CommanderDecision("no_action", "eligibility_emergency_recorded", None, None)

    # E5 — a scheduled check that completed (any non-failure terminal): record
    if ttype == "eligibility_check_completed" and has_check:
        return CommanderDecision("no_action", "eligibility_scheduled_recorded", None, None)

    # E6 — a scheduled check that FAILED: operational re-verify task for a human.
    # The ONLY eligibility rule that routes to 12, and unreachable when emergency
    # (E4 caught it). next_status still None — 12 only logs.
    if ttype == "eligibility_check_failed" and has_check:
        return CommanderDecision(
            "route", "eligibility_check_failed_scheduled", "12-escalation", None
        )

    # E7 — malformed eligibility state (e.g. a completion trigger with no check
    # row). Mirror of R20, split: an emergency-context fallthrough is recorded,
    # never escalated.
    if emergency:
        return CommanderDecision("no_action", "eligibility_emergency_recorded", None, None)
    return CommanderDecision(
        "route", "eligibility_unrecognized_state", "12-escalation", None
    )


def decide(state: dict, trigger: dict) -> CommanderDecision:
    """First matching rule wins. Pure.

    Dispatch: an eligibility trigger goes to the E1-E7 table (§12.6); everything
    else goes to the claims table R1-R20 (§6). The two never interleave.
    """
    if (trigger or {}).get("type") in ELIGIBILITY_TRIGGERS:
        return _decide_eligibility(state, trigger)

    claim = state.get("claim") or {}
    rec = state.get("recommendation")
    issues = state.get("issues") or []
    status = claim.get("status")
    ttype = (trigger or {}).get("type")

    # ===== 6.1 Terminal & re-entrancy guards =================================
    if status in TERMINAL_EXTERNAL:                                       # R1
        return CommanderDecision("no_action", "claim_terminal")
    if status in HUMAN_OWNS:                                              # R2
        return CommanderDecision("no_action", "human_owns_claim")
    if status == "actioned":                                             # R3
        return CommanderDecision("no_action", "already_actioned")
    if status == "executing" and ttype not in EXECUTION_TRIGGERS:        # R4
        return CommanderDecision("no_action", "execution_in_progress")

    # ===== 6.2 Safety guards ================================================
    if ttype == "agent.error":                                           # R5
        return CommanderDecision("route", "agent_error", "12-escalation", "escalated")
    if ttype == "execution.failed":                                      # R6
        return CommanderDecision("route", "execution_failed", "12-escalation", "escalated")

    # ===== 6.3 The human-approval hard guard ================================
    if ttype == "human.approved":
        has_approved_rec = (
            status == "awaiting_approval"
            and isinstance(rec, dict)
            and rec.get("approval_status") == "approved"
        )
        if not has_approved_rec:                                         # R7
            return CommanderDecision(
                "route", "approval_without_recommendation", "12-escalation", "escalated"
            )
        if rec.get("low_confidence") is True:                            # R8
            return CommanderDecision(
                "route", "low_confidence_cannot_be_approved", "12-escalation", "escalated"
            )
        action_type = rec.get("action_type")
        if action_type in EXECUTABLE_ACTIONS:                            # R9
            target = EXECUTABLE_ACTIONS[action_type]
            reason = "approved_followup" if target == "09-followup" else "approved_reminder"
            return CommanderDecision("route", reason, target, "executing")
        if action_type in MANUAL_ACTIONS:                                # R10
            return CommanderDecision(
                "route", "approved_manual_action", "12-escalation", "manual_action_required"
            )
        return CommanderDecision(                                        # R11
            "route", "unknown_action_type", "12-escalation", "escalated"
        )

    # ===== 6.4 Human decline ===============================================
    if ttype == "human.declined":                                        # R12
        return CommanderDecision("no_action", "declined_by_human", None, "declined")

    # ===== 6.5 Recommendation routing ======================================
    if ttype == "recommendation.completed":
        if isinstance(rec, dict) and rec.get("low_confidence") is True:  # R13
            return CommanderDecision(
                "route", "low_confidence_recommendation", "12-escalation", "escalated"
            )
        return CommanderDecision(                                        # R14
            "await_human", "awaiting_human_approval", None, "awaiting_approval"
        )

    # ===== 6.6 Analysis pipeline progression ===============================
    if ttype in REANALYZE_TRIGGERS:                                      # R15
        return CommanderDecision("route", "needs_analysis", "06-analyzer", None)
    if ttype == "analysis.completed":
        if not issues:                                                   # R16
            return CommanderDecision("no_action", "no_issues_found", None, "cleared")
        return CommanderDecision(                                        # R17
            "route", "needs_reasoning", "07-reasoning", "analyzed"
        )
    if ttype == "reasoning.completed":                                   # R18
        return CommanderDecision("route", "needs_recommendation", "08-recommendation", "reasoned")
    if ttype == "execution.completed" and status == "executing":         # R19
        return CommanderDecision("no_action", "execution_complete", None, "actioned")

    # ===== 6.7 Fallthrough =================================================
    return CommanderDecision(                                            # R20
        "route", "unrecognized_state", "12-escalation", "escalated"
    )
