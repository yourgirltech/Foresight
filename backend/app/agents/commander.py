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

Phase 3 adds a THIRD, disjoint rule table for prior authorization (A1-A11,
docs/agents/00-commander.md §13 and docs/agents/02-prior-auth-agent.md).
`decide()` dispatches to `_decide_prior_auth` when the trigger is in
PRIOR_AUTH_TRIGGERS, after the eligibility check and before R1. Prior auth MAY
gate an elective service (correctly); it must NEVER gate emergency / urgent care.
The Commander enforces the emergency half structurally: every A-rule returns
`next_status is None`, and no rule reachable with an emergency signal routes to
`12-escalation` OR to `await_human` OR to a submission.

Phase 5 adds a FOURTH, disjoint rule table for appeals (AP1-AP12,
docs/agents/00-commander.md §14 and docs/agents/11-appeals-agent.md).
`decide()` dispatches to `_decide_appeal` when the trigger is in APPEAL_TRIGGERS,
after the prior-auth check and before R1. Appeals is a BILLING flow — no
care-safety concern — so unlike E/A an appeal rule MAY carry a next_status, but
the two invariants it is held to instead are structural: (1) the ONLY rule
routing to 11.submit is AP6, gated on an `appeal_submission_approved` trigger
plus `appeal.status == 'drafted'`; (2) the ONLY rule with a non-None next_status
is AP9 (`denied` -> `paid`), and only on a won resolution. Both hard-raised in
orchestrator.handle_appeal.
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

# --- Phase 3: the prior-auth trigger family (00-commander.md §13) --------------
# A third disjoint rule table (A1-A11). decide() dispatches here after the
# eligibility check and BEFORE R1; a trigger belongs to exactly one family.
PRIOR_AUTH_TRIGGERS = {
    "prior_auth_requested",
    "prior_auth_emergency",
    "prior_auth_determined",
    "prior_auth_submission_approved",
    "prior_auth_submission_declined",
    "prior_auth_response_received",
}
PRIOR_AUTH_NEEDS_HUMAN_RESPONSES = {"info_needed", "denied"}

# --- Phase 5: the appeals trigger family (00-commander.md §14) -----------------
# A fourth disjoint rule table (AP1-AP12). decide() dispatches here after the
# prior-auth check and BEFORE R1; a trigger belongs to exactly one family.
APPEAL_TRIGGERS = {
    "claim_denied",
    "appeal_resubmitted",
    "appeal_drafted",
    "appeal_submission_approved",
    "appeal_submission_declined",
    "appeal_resolution_received",
    "appeal_error",
}
APPEAL_DRAFT_TRIGGERS = {"claim_denied", "appeal_resubmitted"}
# appeal statuses from which nothing further will happen without a human action
APPEAL_TERMINAL_STATUSES = {
    "insufficient_basis", "submission_declined",
    "appeal_approved", "appeal_partial", "appeal_denied", "error",
}
APPEAL_EXHAUSTED_RESOLUTIONS = {"partial", "denied"}


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


def _is_prior_auth_emergency_context(state: dict, trigger: dict) -> bool:
    """Resolve whether this prior-auth trigger concerns emergency / urgent care.
    Ambiguity ALWAYS resolves to True — the non-blocking, no-auth path
    (00-commander.md §13.4). The only way to get False is an unambiguous,
    appointment-backed, non-emergency, non-'emergency'-place-of-service row."""
    if (trigger or {}).get("type") == "prior_auth_emergency":
        return True
    pa = state.get("prior_authorization")
    if isinstance(pa, dict):
        if pa.get("is_emergency") is True:
            return True
        if pa.get("place_of_service") == "emergency":
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


def _decide_prior_auth(state: dict, trigger: dict) -> CommanderDecision:
    """The prior-auth rule table A1-A11 (00-commander.md §13.6). Pure.

    INVARIANT: every return here has next_status=None — the Commander never
    writes a status off a patient-access trigger (identical to eligibility).
    And no branch reachable with `emergency` True routes to 12-escalation, to
    await_human, or to a submission (02.submit is A7 only, guarded not-emergency).
    """
    ttype = (trigger or {}).get("type")
    emergency = _is_prior_auth_emergency_context(state, trigger)
    pa = state.get("prior_authorization")
    has_pa = isinstance(pa, dict)
    pa_status = pa.get("status") if has_pa else None
    response_status = (pa.get("response_payload") or {}).get("response_status") if has_pa else None

    # A1 — an emergency registration: determine detached, before anything else
    if ttype == "prior_auth_emergency":
        return CommanderDecision(
            "route", "prior_auth_emergency_determine", "02-prior-auth", None
        )

    # A2 — a scheduled request on an emergency-flagged row: take the safe path
    if ttype == "prior_auth_requested" and emergency:
        return CommanderDecision(
            "route", "prior_auth_emergency_determine", "02-prior-auth", None
        )

    # A3 — a normal scheduled request: determine ahead of time
    if ttype == "prior_auth_requested":
        return CommanderDecision(
            "route", "prior_auth_determine", "02-prior-auth", None
        )

    # A4 — ANY emergency determination: record and stop. Never a draft, never
    # await_human, never an escalation. (By P5 an emergency determination can
    # only be emergency_exempt / insufficient_info anyway — A4 makes it explicit.)
    if ttype == "prior_auth_determined" and emergency and has_pa:
        return CommanderDecision(
            "no_action", "prior_auth_emergency_exempt_recorded", None, None
        )

    # A5 — a scheduled determination that auth IS required: park for a human
    if ttype == "prior_auth_determined" and pa_status == "required_draft":
        return CommanderDecision(
            "await_human", "prior_auth_awaiting_submission_approval", None, None
        )

    # A6 — a scheduled determination of not_required / insufficient_info: record
    if ttype == "prior_auth_determined" and has_pa:
        return CommanderDecision(
            "no_action", "prior_auth_determination_recorded", None, None
        )

    # A7 — a human approved submitting a drafted request. The ONLY route to
    # 02.submit, behind three guards: the approval trigger, status required_draft,
    # and not emergency. The structural analogue of R9 behind R7/R8.
    if (
        ttype == "prior_auth_submission_approved"
        and pa_status == "required_draft"
        and not emergency
    ):
        return CommanderDecision("route", "prior_auth_submit", "02-prior-auth", None)

    # A8 — a human declined submitting: record and stop
    if ttype == "prior_auth_submission_declined":
        return CommanderDecision(
            "no_action", "prior_auth_submission_declined_recorded", None, None
        )

    # A9 — the payer approved: record and stop
    if ttype == "prior_auth_response_received" and response_status == "approved":
        return CommanderDecision(
            "no_action", "prior_auth_approved_recorded", None, None
        )

    # A10 — the payer denied / needs info (scheduled): a real human task. The
    # ONLY prior-auth rule that routes to 12, and unreachable when emergency
    # (guarded, and by P5/P6 an emergency PA never has a submission).
    if (
        ttype == "prior_auth_response_received"
        and response_status in PRIOR_AUTH_NEEDS_HUMAN_RESPONSES
        and not emergency
    ):
        return CommanderDecision(
            "route", "prior_auth_needs_human", "12-escalation", None
        )

    # A11 — malformed prior-auth state (mirror of R20 / E7), split so an
    # emergency-context fallthrough is recorded, never escalated.
    if emergency:
        return CommanderDecision(
            "no_action", "prior_auth_emergency_exempt_recorded", None, None
        )
    return CommanderDecision(
        "route", "prior_auth_unrecognized_state", "12-escalation", None
    )


def _decide_appeal(state: dict, trigger: dict) -> CommanderDecision:
    """The appeals rule table AP1-AP12 (00-commander.md §14.5). Pure.

    INVARIANTS (unlike E/A there is no care-safety concern, so an appeal rule MAY
    write a status — but):
      * the ONLY rule routing to 11-appeals with reason `appeal_submit` is AP6,
        gated on an `appeal_submission_approved` trigger + appeal.status ==
        'drafted' — no appeal submits without a recorded human approval;
      * the ONLY rule with a non-None next_status is AP9 — exactly the pair
        (`appeal_won_claim_reversed`, `paid`), on a won resolution.
    orchestrator.handle_appeal hard-raises on any violation of either.
    """
    ttype = (trigger or {}).get("type")
    ap = state.get("appeal")
    has_ap = isinstance(ap, dict)
    ap_status = ap.get("status") if has_ap else None
    ap_resolution = (ap.get("resolution_payload") or {}).get("outcome") if has_ap else None

    # AP1 — re-entrancy: a claim_denied while an appeal is already live is a no-op.
    # `appeal_resubmitted` deliberately skips this (it wants a fresh draft).
    if (
        ttype == "claim_denied"
        and has_ap
        and ap_status not in APPEAL_TERMINAL_STATUSES
    ):
        return CommanderDecision("no_action", "appeal_already_in_progress", None, None)

    # AP2 — draft (or re-draft, for a second-level appeal)
    if ttype in APPEAL_DRAFT_TRIGGERS:
        return CommanderDecision("route", "appeal_draft", "11-appeals", None)

    # AP3 — 11 found nothing citable. A denial is always money at risk, so a human
    # decides (appeal manually / write off). Same shape as A10 / R10.
    if ttype == "appeal_drafted" and ap_status == "insufficient_basis":
        return CommanderDecision(
            "route", "appeal_no_basis_needs_human", "12-escalation", None
        )

    # AP4 — the letter is drafted and grounded: park for a human's approval to send
    if ttype == "appeal_drafted" and ap_status == "drafted":
        return CommanderDecision(
            "await_human", "appeal_awaiting_submission_approval", None, None
        )

    # AP5 — an appeal_drafted trigger with a bad status (mirror of a bad draft state)
    if ttype == "appeal_drafted":
        return CommanderDecision(
            "route", "appeal_draft_unrecognized", "12-escalation", None
        )

    # AP6 — a human approved sending the drafted letter. The ONLY route to
    # 11.submit, behind two guards: the approval trigger and status 'drafted'.
    # The structural analogue of R9 behind R7/R8 and of A7 behind its guards.
    if ttype == "appeal_submission_approved" and ap_status == "drafted":
        return CommanderDecision("route", "appeal_submit", "11-appeals", None)

    # AP7 — an approval trigger for an appeal that is not 'drafted' (spoof / race).
    # Escalate; never submit (mirror of R7).
    if ttype == "appeal_submission_approved":
        return CommanderDecision(
            "route", "appeal_approval_without_draft", "12-escalation", None
        )

    # AP8 — a human declined sending it: record and stop
    if ttype == "appeal_submission_declined":
        return CommanderDecision(
            "no_action", "appeal_submission_declined_recorded", None, None
        )

    # AP9 — a WON appeal. The ONLY rule with a non-None next_status: reverse the
    # claim `denied` -> `paid`, simulated.
    if ttype == "appeal_resolution_received" and ap_resolution == "approved":
        return CommanderDecision("no_action", "appeal_won_claim_reversed", None, "paid")

    # AP10 — a partial or upheld resolution. The automated path is exhausted; a
    # human owns the next move. The claim stays `denied`; 12 only logs. Mirror of A10.
    if (
        ttype == "appeal_resolution_received"
        and ap_resolution in APPEAL_EXHAUSTED_RESOLUTIONS
    ):
        return CommanderDecision(
            "route", "appeal_exhausted_needs_human", "12-escalation", None
        )

    # AP11 — the drafting model was unreachable. A hollow appeal is worse than
    # none (mirror of R5 for 07's agent.error).
    if ttype == "appeal_error":
        return CommanderDecision("route", "appeal_agent_error", "12-escalation", None)

    # AP12 — malformed appeal state (mirror of R20 / E7 / A11)
    return CommanderDecision(
        "route", "appeal_unrecognized_state", "12-escalation", None
    )


def decide(state: dict, trigger: dict) -> CommanderDecision:
    """First matching rule wins. Pure.

    Dispatch: an eligibility trigger goes to the E1-E7 table (§12.6); a prior-auth
    trigger goes to the A1-A11 table (§13.6); an appeal trigger goes to the
    AP1-AP12 table (§14.5); everything else goes to the claims table R1-R20 (§6).
    The four never interleave.
    """
    ttype_dispatch = (trigger or {}).get("type")
    if ttype_dispatch in ELIGIBILITY_TRIGGERS:
        return _decide_eligibility(state, trigger)
    if ttype_dispatch in PRIOR_AUTH_TRIGGERS:
        return _decide_prior_auth(state, trigger)
    if ttype_dispatch in APPEAL_TRIGGERS:
        return _decide_appeal(state, trigger)

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
