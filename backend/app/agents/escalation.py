"""12 — escalation-agent: the safety net.

Never guesses, never retries what already failed. It writes one `escalations`
row with the full context and an `activity_log` entry, then stops. A human now
owns the claim.

Two callers, distinguished by `reason_code`:
  * error / unrecognised-state escalations (R5, R6, R7, R8, R11, R13, R20)
  * an approved action that a human must perform  (R10 -> reason_code
    'approved_manual_action'); the claim is at `manual_action_required`, not
    `escalated`, and the UI renders it as an approved task, not a problem.
"""
from __future__ import annotations

from . import db


async def escalate(
    org_id: str,
    claim_pk: str | None,
    *,
    reason_code: str,
    context: dict,
    appointment_id: str | None = None,
    eligibility_check_id: str | None = None,
) -> dict:
    esc = await db.insert_escalation(
        org_id,
        claim_pk,
        reason_code=reason_code,
        originating_agent="12-escalation",
        context=context,
        appointment_id=appointment_id,
        eligibility_check_id=eligibility_check_id,
    )
    await db.insert_activity(
        org_id,
        claim_pk,
        actor="12-escalation",
        action="escalated",
        details={"escalation_id": esc["id"], "reason_code": reason_code},
        appointment_id=appointment_id,
        eligibility_check_id=eligibility_check_id,
    )
    return esc
