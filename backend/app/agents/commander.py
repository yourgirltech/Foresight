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


@dataclass(frozen=True)
class CommanderDecision:
    action: Action
    reason_code: str
    route_to: str | None = None
    next_status: str | None = None


def decide(state: dict, trigger: dict) -> CommanderDecision:
    """First matching rule in 00-commander.md §6 wins. Pure."""
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
