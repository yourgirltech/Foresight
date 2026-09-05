"""09 — followup-agent  and  10 — reminder-agent.

Both execute an action that a human has already approved (the Commander only
routes here on R9, behind the R7/R8 guards). Phase 1 does a **simulated** send —
no real external delivery — and writes a real logged record in `follow_ups`.

Retry policy: bounded retries on a transient send error, immediate give-up on a
non-transient one. Exhausting retries (or a permanent error) raises
`ExecutionFailed`; the orchestrator turns that into an `execution.failed`
trigger and the Commander escalates (R6). 12 never retries what already failed.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from . import db

MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.0, 0.2, 0.5)


class TransientSendError(RuntimeError):
    """A retryable failure (timeout, 503, connection reset)."""


class PermanentSendError(RuntimeError):
    """A non-retryable failure (rejected payload, auth). Do not retry."""


class ExecutionFailed(RuntimeError):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


async def _simulated_send(payload: dict) -> dict:
    """Phase 1 stand-in for real payer/portal delivery. Always succeeds.

    Tests monkeypatch this to raise TransientSendError / PermanentSendError and
    exercise the retry policy.
    """
    return {
        "delivered": True,
        "channel": "simulated",
        "reference": f"SIM-{uuid.uuid4().hex[:12]}",
        "payload": payload,
    }


_ACTION_NOTE = {
    "submit_authorization_request": "Prior-authorization request filed with the payer.",
    "request_documentation": "Requested the missing supporting documentation for this claim.",
    "payer_status_follow_up": "Opened a payer status follow-up on this claim.",
}


async def _execute(
    org_id: str,
    claim: dict,
    rec: dict,
    *,
    agent_name: str,
    kind: str,
    due_days: int,
) -> dict:
    claim_pk = claim["id"]
    note = _ACTION_NOTE.get(rec["action_type"], f"Executed {rec['action_type']}.")
    payload = {
        "claim_id": claim["claim_id"],
        "action_type": rec["action_type"],
        "recommendation_id": rec["id"],
    }

    attempt = 0
    last_err: Exception | None = None
    while attempt < MAX_ATTEMPTS:
        try:
            confirmation = await _simulated_send(payload)
            break
        except PermanentSendError as exc:
            await db.insert_activity(
                org_id, claim_pk, actor=agent_name, action="send_failed",
                details={"transient": False, "attempt": attempt + 1, "error": str(exc)},
            )
            raise ExecutionFailed(str(exc), transient=False) from exc
        except TransientSendError as exc:
            last_err = exc
            await db.insert_activity(
                org_id, claim_pk, actor=agent_name, action="send_retry",
                details={"transient": True, "attempt": attempt + 1, "error": str(exc)},
            )
            attempt += 1
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
    else:
        raise ExecutionFailed(
            f"send failed after {MAX_ATTEMPTS} attempts: {last_err}", transient=True
        )

    now = datetime.now(timezone.utc)
    record = await db.insert_follow_up(
        org_id,
        claim_pk,
        kind=kind,
        note=note,
        due_at=(now + timedelta(days=due_days)).isoformat(),
        originating_agent=agent_name,
        sent_at=now.isoformat(),
    )
    await db.insert_activity(
        org_id, claim_pk, actor=agent_name, action="executed",
        details={
            "follow_up_id": record["id"],
            "kind": kind,
            "attempts": attempt + 1,
            "confirmation_reference": confirmation["reference"],
        },
    )
    return record


async def run_followup(org_id: str, claim: dict, rec: dict) -> dict:
    """09 — a follow-up action on the claim itself."""
    return await _execute(org_id, claim, rec, agent_name="09-followup", kind="follow_up", due_days=7)


async def run_reminder(org_id: str, claim: dict, rec: dict) -> dict:
    """10 — a payer-facing status reminder / tickler."""
    return await _execute(
        org_id, claim, rec, agent_name="10-reminder", kind="payer_reminder", due_days=14
    )
