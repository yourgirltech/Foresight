"""Phase 6 — the n8n-facing automation endpoints for voice appointment reminders.

`/api/automation/voice-reminders/*`. Every route is gated by `require_automation`
(a static bearer token, NOT a Supabase role — docs/agents/17-voice-reminder-agent.md
§2.3). n8n owns *when* and *sequencing*; the authoritative logic — which
reminders are due, the live consent re-check, variable rendering, every
`voice_reminders` state transition, outcome classification, and escalation
creation — lives here and is org-scoped from the `voice_reminders` row.

17 is NOT a Commander agent. commander.py / orchestrator.py are untouched.
Non-`confirmed` outcomes create a real `escalations` row via the existing
`escalation.escalate()` — the same table 12-escalation-agent writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status

from .. import voice
from ..agents import db, escalation
from ..auth import AutomationPrincipal, require_automation
from ..config import get_settings

router = APIRouter(prefix="/api/automation/voice-reminders", tags=["voice-automation"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def _skip_and_escalate(vr: dict, *, new_status: str, reason_code: str, context: dict) -> None:
    """Set the reminder to a terminal skipped/error status, create one escalation
    (reusing 12's table), and link it back onto the row."""
    org_id = vr["organization_id"]
    await db.update_voice_reminder(org_id, vr["id"], {
        "status": new_status,
        "completed_at": _iso(_now()),
    })
    esc = await escalation.escalate(
        org_id, None,
        reason_code=reason_code,
        context={"voice_reminder_id": vr["id"], "appointment_id": vr["appointment_id"], **context},
        appointment_id=vr["appointment_id"],
        voice_reminder_id=vr["id"],
    )
    await db.update_voice_reminder(org_id, vr["id"], {"escalation_id": esc["id"]})
    await db.insert_activity(
        org_id, None, actor="17-voice-reminder", action=new_status,
        appointment_id=vr["appointment_id"], voice_reminder_id=vr["id"],
        details={"reason_code": reason_code, "escalation_id": esc["id"]},
    )


async def _resolve_live_consent(vr: dict):
    consents = await db.list_patient_consents(vr["organization_id"], vr["patient_contact_id"])
    current = voice.current_voice_consent(consents)
    gate = voice.consent_gate(vr.get("patient_phone_snapshot"), current)
    # keep the row's snapshot honest if the live ledger has moved
    if bool(current.granted) != bool(vr.get("consent_snapshot")):
        await db.update_voice_reminder(vr["organization_id"], vr["id"], {
            "consent_snapshot": bool(current.granted),
            "consent_event_id_snapshot": current.event_id,
            "consent_source_snapshot": current.source,
        })
        await db.insert_activity(
            vr["organization_id"], None, actor="17-voice-reminder",
            action="voice_consent_changed_since_enrollment",
            appointment_id=vr["appointment_id"], voice_reminder_id=vr["id"],
            patient_contact_id=vr["patient_contact_id"],
            details={"granted_now": bool(current.granted), "event_id": current.event_id},
        )
    return current, gate


# --------------------------------------------------------------------------- #
# GET /due  — n8n's schedule workflow polls this
# --------------------------------------------------------------------------- #
@router.get("/due")
async def due(
    limit: int | None = None,
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    settings = get_settings()
    now = _now()
    lim = min(limit or settings.voice_due_batch_limit, settings.voice_due_batch_limit)

    # 1) sweep lost dispatches — 'dispatching' with no mark-calling past the lease.
    #    NOT re-dialed (decision §11.2): recorded 'error' + escalation, a human owns it.
    lease_cutoff = now - timedelta(minutes=settings.voice_dispatch_lease_minutes)
    swept = 0
    for vr in await db.list_stale_dispatching(dispatched_before_iso=_iso(lease_cutoff)):
        await _skip_and_escalate(
            vr, new_status="error", reason_code="voice_reminder_dispatch_lost",
            context={"dispatched_at": vr.get("dispatched_at"), "note": "n8n did not report mark-calling within the lease"},
        )
        swept += 1

    # 2) candidates: pending, past scheduled_call_at, authorized
    stale_cutoff = now - timedelta(hours=settings.voice_due_stale_hours)
    out: list[dict] = []
    for vr in await db.list_due_voice_reminders(before_iso=_iso(now), limit=lim):
        org_id = vr["organization_id"]

        # too far past due -> a very-late reminder call is worse than none
        sched = datetime.fromisoformat(str(vr["scheduled_call_at"]).replace("Z", "+00:00"))
        if sched < stale_cutoff:
            await _skip_and_escalate(
                vr, new_status="error", reason_code="voice_reminder_stale_past_due",
                context={"scheduled_call_at": vr["scheduled_call_at"], "stale_hours": settings.voice_due_stale_hours},
            )
            continue

        current, gate = await _resolve_live_consent(vr)
        if not gate.can_call:
            new_status = "skipped_no_phone" if gate.reason in ("no_phone", "bad_phone_format") else "skipped_no_consent"
            await _skip_and_escalate(
                vr, new_status=new_status,
                reason_code=voice.STATUS_TO_ESCALATION_REASON[new_status],
                context={"consent_reason": gate.reason, "phone": vr.get("patient_phone_snapshot")},
            )
            continue

        org = await db.get_organization(org_id)
        appt = await db.get_appointment_any_org(vr["appointment_id"])
        try:
            variables = voice.resolve_variables(appt or {}, org or {})
        except ValueError as exc:
            await _skip_and_escalate(
                vr, new_status="error", reason_code="voice_reminder_render_failed",
                context={"error": str(exc)},
            )
            continue

        vals = variables.as_variable_values()
        await db.update_voice_reminder(org_id, vr["id"], {
            "status": "dispatching",
            "dispatched_at": _iso(now),
            "variable_values": vals,
            "vapi_assistant_id": settings.vapi_assistant_id or None,
            "vapi_phone_number_id": settings.vapi_phone_number_id or None,
        })
        await db.insert_activity(
            org_id, None, actor="17-voice-reminder", action="dispatching",
            appointment_id=vr["appointment_id"], voice_reminder_id=vr["id"],
            details={"consent_event_id": current.event_id},
        )
        out.append({
            "voice_reminder_id": vr["id"],
            "vapi": {
                "assistant_id": settings.vapi_assistant_id,
                "phone_number_id": settings.vapi_phone_number_id,
                "customer_number": vr["patient_phone_snapshot"],
                "variable_values": vals,
                "recording_enabled": False,
            },
        })

    return {"due": out, "swept": swept}


# --------------------------------------------------------------------------- #
# POST /{id}/mark-calling  — n8n reports the Vapi call was created
# --------------------------------------------------------------------------- #
@router.post("/{vr_id}/mark-calling")
async def mark_calling(
    vr_id: str,
    body: dict = Body(...),
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    vapi_call_id = (body or {}).get("vapi_call_id")
    if not vapi_call_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "vapi_call_id is required")

    vr = await db.get_voice_reminder(vr_id)
    if vr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice reminder not found")
    if vr["status"] == "calling" and vr.get("vapi_call_id") == vapi_call_id:
        return {"status": "calling", "idempotent": True}
    if vr["status"] != "dispatching":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "not_dispatching", "status": vr["status"]})

    # final live consent gate (§2.2 step 3) — closes the /due -> dial race
    _, gate = await _resolve_live_consent(vr)
    if not gate.can_call:
        new_status = "skipped_no_phone" if gate.reason in ("no_phone", "bad_phone_format") else "skipped_no_consent"
        await _skip_and_escalate(
            vr, new_status=new_status,
            reason_code=voice.STATUS_TO_ESCALATION_REASON[new_status],
            context={"consent_reason": gate.reason, "at": "mark_calling"},
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "consent_revoked", "consent_reason": gate.reason})

    org_id = vr["organization_id"]
    await db.update_voice_reminder(org_id, vr_id, {
        "status": "calling",
        "vapi_call_id": vapi_call_id,
        "placed_at": _iso(_now()),
        "outcome_payload": {"vapi_call_status": (body or {}).get("vapi_call_status")},
    })
    await db.insert_activity(
        org_id, None, actor="17-voice-reminder", action="call_placed",
        appointment_id=vr["appointment_id"], voice_reminder_id=vr_id,
        details={"vapi_call_id": vapi_call_id},
    )
    return {"status": "calling"}


# --------------------------------------------------------------------------- #
# POST /outcome  — n8n's webhook workflow forwards Vapi's end-of-call-report
# --------------------------------------------------------------------------- #
@router.post("/outcome")
async def outcome(
    body: dict = Body(...),
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    vapi_call_id = (body or {}).get("vapi_call_id")
    message = (body or {}).get("message") or {}
    if not vapi_call_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "vapi_call_id is required")

    vr = await db.voice_reminder_by_call_id(vapi_call_id)
    if vr is None:
        # §2.4 — never trust the payload for tenancy; an unmatched call id is ignored
        return {"ignored": True, "reason": "unmatched_call_id"}

    org_id = vr["organization_id"]
    result = voice.classify_outcome(message)

    # idempotent: a replayed callback on an already-terminal row does nothing new
    terminal = {"confirmed", "reschedule_requested", "wrong_person", "out_of_scope",
                "no_answer", "call_failed", "error"}
    if vr["status"] in terminal:
        return {"status": vr["status"], "escalated": vr.get("escalation_id") is not None, "idempotent": True}

    await db.update_voice_reminder(org_id, vr["id"], {
        "status": result.status,
        "outcome": result.outcome,
        "outcome_payload": result.payload,
        "completed_at": _iso(_now()),
    })
    await db.insert_activity(
        org_id, None, actor="17-voice-reminder", action="outcome",
        appointment_id=vr["appointment_id"], voice_reminder_id=vr["id"],
        details={"status": result.status, "outcome": result.outcome},
    )

    if result.status == voice.CONFIRMED_OUTCOME:
        return {"status": "confirmed", "escalated": False}

    reason_code = voice.STATUS_TO_ESCALATION_REASON.get(result.status, "voice_reminder_outcome_needs_human")
    esc = await escalation.escalate(
        org_id, None,
        reason_code=reason_code,
        context={"voice_reminder_id": vr["id"], "appointment_id": vr["appointment_id"],
                 "outcome": result.outcome, "ended_reason": result.payload.get("ended_reason")},
        appointment_id=vr["appointment_id"],
        voice_reminder_id=vr["id"],
    )
    await db.update_voice_reminder(org_id, vr["id"], {"escalation_id": esc["id"]})
    return {"status": result.status, "escalated": True, "escalation_id": esc["id"]}


# --------------------------------------------------------------------------- #
# POST /{id}/dispatch-failed  — n8n could not place the Vapi call
# --------------------------------------------------------------------------- #
@router.post("/{vr_id}/dispatch-failed")
async def dispatch_failed(
    vr_id: str,
    body: dict = Body(default={}),
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    vr = await db.get_voice_reminder(vr_id)
    if vr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice reminder not found")
    if vr["status"] != "dispatching":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "not_dispatching", "status": vr["status"]})
    await _skip_and_escalate(
        vr, new_status="call_failed", reason_code="voice_reminder_call_failed",
        context={"error": (body or {}).get("error"), "vapi_status": (body or {}).get("vapi_status")},
    )
    return {"status": "call_failed"}
