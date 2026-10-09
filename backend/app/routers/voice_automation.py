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

Per-reminder lifecycle (n8n drives it, this module enforces it):

    pending --/due lease--> dispatching --authorize-dial--> (dial_authorized_at set)
        --Vapi call created, mark-calling--> calling --/outcome--> terminal

Every status change is a compare-and-set (`db.transition_voice_reminder`): it
only applies while the row is still in the expected status, so concurrent
writers cannot both win and a terminal reminder can never revert to an active
state. The loser of a race does nothing (no second escalation, no state change).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, status

from .. import voice
from ..agents import db, escalation
from ..auth import AutomationPrincipal, require_automation
from ..config import get_settings

router = APIRouter(prefix="/api/automation/voice-reminders", tags=["voice-automation"])

# Diagnostic strings from n8n / Vapi are stored for debugging, never trusted.
_MAX_DIAGNOSTIC_CHARS = 500
_MAX_CALL_ID_CHARS = 200

# authorize-dial refusal reason -> the skipped status it lands the reminder in
_CONSENT_REFUSALS = {
    "consent_revoked": "skipped_no_consent",
    "no_consent": "skipped_no_consent",
    "bad_phone_format": "skipped_no_phone",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _clip(value) -> str | None:
    if value is None:
        return None
    return str(value)[:_MAX_DIAGNOSTIC_CHARS]


def _call_id(body: dict | None) -> str:
    raw = (body or {}).get("vapi_call_id")
    call_id = raw.strip() if isinstance(raw, str) else ""
    if not call_id or len(call_id) > _MAX_CALL_ID_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "vapi_call_id is required")
    return call_id


async def _skip_and_escalate(
    vr: dict,
    *,
    new_status: str,
    reason_code: str,
    context: dict,
    from_statuses: tuple[str, ...],
    extra_filters: dict[str, str] | None = None,
) -> bool:
    """Move the reminder to a terminal skipped/error status — only if it is still
    in `from_statuses` — then create one escalation (reusing 12's table) and link
    it back. Returns False (and does nothing else) if another writer got there
    first, so a race can never produce a second escalation."""
    org_id = vr["organization_id"]
    moved = await db.transition_voice_reminder(
        org_id, vr["id"],
        from_statuses=from_statuses,
        fields={"status": new_status, "completed_at": _iso(_now())},
        extra_filters=extra_filters,
    )
    if moved is None:
        return False
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
    return True


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


def _skipped_status(gate) -> str:
    return "skipped_no_phone" if gate.reason in ("no_phone", "bad_phone_format") else "skipped_no_consent"


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
    lease_cutoff = _iso(now - timedelta(minutes=settings.voice_dispatch_lease_minutes))
    swept = 0
    for vr in await db.list_stale_dispatching(dispatched_before_iso=lease_cutoff):
        if await _skip_and_escalate(
            vr, new_status="error", reason_code="voice_reminder_dispatch_lost",
            context={
                "dispatched_at": vr.get("dispatched_at"),
                "dial_authorized_at": vr.get("dial_authorized_at"),
                # authorized but never marked calling: a call MAY have been placed
                "call_may_have_been_placed": vr.get("dial_authorized_at") is not None,
                "note": "n8n did not report mark-calling within the lease",
            },
            from_statuses=("dispatching",),
            extra_filters={"dispatched_at": f"lt.{lease_cutoff}"},
        ):
            swept += 1

    # 1b) sweep lost outcomes — 'calling' with no end-of-call-report past the
    #     timeout (webhook lost / backend down when Vapi called back). Never left
    #     'calling' forever: recorded 'error' + escalation, a human owns it.
    outcome_cutoff = _iso(now - timedelta(minutes=settings.voice_outcome_timeout_minutes))
    for vr in await db.list_stale_calling(placed_before_iso=outcome_cutoff):
        if await _skip_and_escalate(
            vr, new_status="error", reason_code="voice_reminder_outcome_missing",
            context={"vapi_call_id": vr.get("vapi_call_id"), "placed_at": vr.get("placed_at"),
                     "note": "no end-of-call-report received within the outcome timeout"},
            from_statuses=("calling",),
            extra_filters={"placed_at": f"lt.{outcome_cutoff}"},
        ):
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
                from_statuses=("pending",),
            )
            continue

        current, gate = await _resolve_live_consent(vr)
        if not gate.can_call:
            new_status = _skipped_status(gate)
            await _skip_and_escalate(
                vr, new_status=new_status,
                reason_code=voice.STATUS_TO_ESCALATION_REASON[new_status],
                context={"consent_reason": gate.reason, "phone": vr.get("patient_phone_snapshot")},
                from_statuses=("pending",),
            )
            continue

        org = await db.get_organization(org_id)
        appt = await db.get_appointment_any_org(vr["appointment_id"])
        try:
            variables = voice.resolve_variables(appt or {}, org or {})
        except ValueError as exc:
            await _skip_and_escalate(
                vr, new_status="error", reason_code="voice_reminder_render_failed",
                context={"error": _clip(exc)},
                from_statuses=("pending",),
            )
            continue

        vals = variables.as_variable_values()
        # lease: compare-and-set pending -> dispatching. If two /due polls
        # overlap, only one wins the row; the other must not hand it to n8n.
        leased = await db.transition_voice_reminder(
            org_id, vr["id"],
            from_statuses=("pending",),
            fields={
                "status": "dispatching",
                "dispatched_at": _iso(now),
                "variable_values": vals,
                "vapi_assistant_id": settings.vapi_assistant_id or None,
                "vapi_phone_number_id": settings.vapi_phone_number_id or None,
            },
        )
        if leased is None:
            continue
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
# POST /{id}/authorize-dial  — n8n calls this IMMEDIATELY before Vapi
# --------------------------------------------------------------------------- #
@router.post("/{vr_id}/authorize-dial")
async def authorize_dial(
    vr_id: str,
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    """The pre-dial consent gate (§2.2). Atomic + single-use in Postgres
    (public.voice_authorize_dial). n8n must place the Vapi call ONLY when this
    returns {"authorized": true}; any other response — including an error or no
    response — means do not dial."""
    vr = await db.get_voice_reminder(vr_id)
    if vr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice reminder not found")

    settings = get_settings()
    lease_cutoff = _iso(_now() - timedelta(minutes=settings.voice_dispatch_lease_minutes))
    result = await db.authorize_voice_dial(vr_id, lease_cutoff_iso=lease_cutoff)
    reason = result.get("reason") or ""

    if result.get("authorized") is True:
        await db.insert_activity(
            vr["organization_id"], None, actor="17-voice-reminder", action="dial_authorized",
            appointment_id=vr["appointment_id"], voice_reminder_id=vr_id, details={},
        )
        return {"authorized": True, "voice_reminder_id": vr_id}

    if reason in _CONSENT_REFUSALS:
        new_status = _CONSENT_REFUSALS[reason]
        await _skip_and_escalate(
            vr, new_status=new_status,
            reason_code=voice.STATUS_TO_ESCALATION_REASON[new_status],
            context={"consent_reason": reason, "at": "authorize_dial"},
            from_statuses=("dispatching",),
        )
    # not_dispatching / already_authorized / lease_expired / not_authorized:
    # no state change here — the row is either already owned by another path or
    # will be picked up by the lost-dispatch sweep.
    return {"authorized": False, "reason": reason, "voice_reminder_id": vr_id}


# --------------------------------------------------------------------------- #
# POST /{id}/mark-calling  — n8n reports the Vapi call was created
# --------------------------------------------------------------------------- #
@router.post("/{vr_id}/mark-calling")
async def mark_calling(
    vr_id: str,
    body: dict = Body(...),
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    """Any non-200 tells n8n to END the just-created call (fail closed)."""
    vapi_call_id = _call_id(body)

    vr = await db.get_voice_reminder(vr_id)
    if vr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice reminder not found")
    if vr["status"] == "calling" and vr.get("vapi_call_id") == vapi_call_id:
        return {"status": "calling", "idempotent": True}
    if vr["status"] != "dispatching":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "not_dispatching", "status": vr["status"]})

    # A call placed without the pre-dial gate is a workflow defect: never let it
    # become a valid 'calling' reminder. Escalate; n8n ends the call.
    if not vr.get("dial_authorized_at"):
        await _skip_and_escalate(
            vr, new_status="error", reason_code="voice_reminder_dialled_without_authorization",
            context={"vapi_call_id": vapi_call_id, "at": "mark_calling"},
            from_statuses=("dispatching",),
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "not_dial_authorized"})

    # final live consent gate — catches a revocation committed after authorize-dial
    _, gate = await _resolve_live_consent(vr)
    if not gate.can_call:
        new_status = _skipped_status(gate)
        await _skip_and_escalate(
            vr, new_status=new_status,
            reason_code=voice.STATUS_TO_ESCALATION_REASON[new_status],
            context={"consent_reason": gate.reason, "at": "mark_calling",
                     "vapi_call_id": vapi_call_id, "call_was_placed": True},
            from_statuses=("dispatching",),
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "consent_revoked", "consent_reason": gate.reason})

    org_id = vr["organization_id"]
    try:
        moved = await db.transition_voice_reminder(
            org_id, vr_id,
            from_statuses=("dispatching",),
            fields={
                "status": "calling",
                "vapi_call_id": vapi_call_id,
                "placed_at": _iso(_now()),
                "outcome_payload": {"vapi_call_status": _clip((body or {}).get("vapi_call_status"))},
            },
        )
    except httpx.HTTPStatusError as exc:
        # unique index voice_reminders_vapi_call_id_idx: this call id is already
        # bound to another reminder — never rebind it.
        if exc.response.status_code == status.HTTP_409_CONFLICT:
            raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "duplicate_call_id"}) from exc
        raise
    if moved is None:
        # lost a race (e.g. the lease sweep moved it to 'error' first)
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "not_dispatching"})

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
    vapi_call_id = _call_id(body)
    message = (body or {}).get("message") or {}
    if not isinstance(message, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "message must be an object")

    # only the FINAL report may move a reminder to a terminal status; a mid-call
    # status-update / transcript / tool message must never close a live call.
    msg_type = message.get("type")
    if msg_type is not None and msg_type != "end-of-call-report":
        return {"ignored": True, "reason": "not_end_of_call_report"}

    # the forwarded id must be the id inside the report itself
    reported_id = (message.get("call") or {}).get("id")
    if reported_id is not None and reported_id != vapi_call_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"reason": "call_id_mismatch"})

    vr = await db.voice_reminder_by_call_id(vapi_call_id)
    if vr is None:
        # §2.4 — never trust the payload for tenancy; an unmatched call id is ignored
        return {"ignored": True, "reason": "unmatched_call_id"}

    org_id = vr["organization_id"]
    result = voice.classify_outcome(message)

    # compare-and-set calling -> terminal. A replayed callback, or one for a row
    # already moved by another path, changes nothing.
    moved = await db.transition_voice_reminder(
        org_id, vr["id"],
        from_statuses=("calling",),
        fields={
            "status": result.status,
            "outcome": result.outcome,
            "outcome_payload": result.payload,
            "completed_at": _iso(_now()),
        },
        extra_filters={"vapi_call_id": f"eq.{vapi_call_id}"},
    )
    if moved is None:
        current = await db.get_voice_reminder(vr["id"]) or vr
        return {"status": current["status"], "escalated": current.get("escalation_id") is not None,
                "idempotent": True}

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
    moved = await _skip_and_escalate(
        vr, new_status="call_failed", reason_code="voice_reminder_call_failed",
        context={"error": _clip((body or {}).get("error")),
                 "vapi_status": _clip((body or {}).get("vapi_status")),
                 "detail": _clip((body or {}).get("detail"))},
        from_statuses=("dispatching",),
    )
    if not moved:
        current = await db.get_voice_reminder(vr_id) or vr
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"reason": "not_dispatching", "status": current["status"]})
    return {"status": "call_failed"}


# --------------------------------------------------------------------------- #
# POST /{id}/end-call-failed  — n8n tried to END a call and Vapi did not confirm
# --------------------------------------------------------------------------- #
@router.post("/{vr_id}/end-call-failed")
async def end_call_failed(
    vr_id: str,
    body: dict = Body(default={}),
    _: AutomationPrincipal = Depends(require_automation),
) -> dict:
    """A call that should have been ended (consent revoked / not confirmed) may
    still be live. No state change — the reminder is already terminal or owned
    by the sweep — but a human must know NOW, so this always escalates."""
    vr = await db.get_voice_reminder(vr_id)
    if vr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice reminder not found")
    org_id = vr["organization_id"]
    esc = await escalation.escalate(
        org_id, None,
        reason_code="voice_reminder_end_call_failed",
        context={"voice_reminder_id": vr_id, "appointment_id": vr["appointment_id"],
                 "vapi_call_id": _clip((body or {}).get("vapi_call_id")),
                 "error": _clip((body or {}).get("error")),
                 "reminder_status": vr["status"]},
        appointment_id=vr["appointment_id"],
        voice_reminder_id=vr_id,
    )
    await db.insert_activity(
        org_id, None, actor="17-voice-reminder", action="end_call_failed",
        appointment_id=vr["appointment_id"], voice_reminder_id=vr_id,
        details={"escalation_id": esc["id"]},
    )
    return {"escalated": True, "escalation_id": esc["id"]}
