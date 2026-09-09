"""Phase 6 — staff-facing endpoints for voice appointment reminders (JWT-scoped).

Consent capture (the durable, revocable `patient_consents` ledger) and reminder
enrollment (the recorded human authorization — `authorized_by`). All writes are
backend-mediated with the org from the verified session; reads go through the
caller's RLS scope.

The reminder call itself is placed by the n8n workflow via
`/api/automation/voice-reminders/*` — see docs/agents/17-voice-reminder-agent.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import voice
from ..agents import db
from ..auth import AuthContext, require_organization
from ..config import get_settings
from ..supabase_rest import rest_get

router = APIRouter(tags=["voice-reminders"])

_GRANT_SOURCES = {"intake_form", "patient_portal", "verbal_documented"}
_REVOKE_SOURCES = {"patient_request", "staff_correction", "returned_call_opt_out"}
_ACTIVE = {"pending", "dispatching", "calling"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def _visible_appointment(access_token: str, appt_id: str) -> dict:
    rows = await rest_get(access_token, "/appointments", {"id": f"eq.{appt_id}", "select": "*"})
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "appointment not found")
    return rows[0]


async def _visible_contact(access_token: str, contact_id: str) -> dict:
    rows = await rest_get(access_token, "/patient_contacts", {"id": f"eq.{contact_id}", "select": "*"})
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "patient contact not found")
    return rows[0]


# --------------------------------------------------------------------------- #
# Consent
# --------------------------------------------------------------------------- #
@router.get("/api/patient-contacts")
async def list_contacts(
    patient_name: str, patient_dob: str | None = None,
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    contacts = await db.find_patient_contacts(
        ctx.organization_id, patient_name=patient_name, patient_dob=patient_dob
    )
    out = []
    for c in contacts:
        consents = await db.list_patient_consents(ctx.organization_id, c["id"])
        cur = voice.current_voice_consent(consents)
        out.append({**c, "voice_consent": {
            "granted": cur.granted, "source": cur.source, "recorded_at": cur.recorded_at,
            "event_id": cur.event_id,
        }, "consent_history": consents})
    return {"patient_contacts": out}


class CreateContact(BaseModel):
    patient_name: str
    patient_dob: str | None = None
    phone: str


@router.post("/api/patient-contacts", status_code=status.HTTP_201_CREATED)
async def create_contact(body: CreateContact, ctx: AuthContext = Depends(require_organization)) -> dict:
    if not voice.E164_RE.match(body.phone.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "phone must be E.164, e.g. +14155550142")
    row = await db.insert_patient_contact(ctx.organization_id, {
        "patient_name": body.patient_name.strip(),
        "patient_dob": body.patient_dob,
        "phone": body.phone.strip(),
        "created_by": ctx.user_id,
    })
    return {"patient_contact": row}


class ConsentBody(BaseModel):
    source: str
    note: str | None = None


@router.post("/api/patient-contacts/{contact_id}/consent", status_code=status.HTTP_201_CREATED)
async def grant_consent(contact_id: str, body: ConsentBody, ctx: AuthContext = Depends(require_organization)) -> dict:
    contact = await _visible_contact(ctx.access_token, contact_id)
    src = body.source if body.source in _GRANT_SOURCES else "verbal_documented"
    row = await db.insert_patient_consent(ctx.organization_id, {
        "patient_contact_id": contact["id"], "channel": "voice", "state": "granted",
        "source": src, "note": body.note, "recorded_by": ctx.user_id,
    })
    await db.insert_activity(
        ctx.organization_id, None, actor=f"human:{ctx.user_id}", action="voice_consent_granted",
        patient_contact_id=contact["id"], details={"consent_id": row["id"], "source": src},
    )
    return {"consent": row}


@router.post("/api/patient-contacts/{contact_id}/consent/revoke", status_code=status.HTTP_201_CREATED)
async def revoke_consent(contact_id: str, body: ConsentBody, ctx: AuthContext = Depends(require_organization)) -> dict:
    contact = await _visible_contact(ctx.access_token, contact_id)
    src = body.source if body.source in _REVOKE_SOURCES else "patient_request"
    row = await db.insert_patient_consent(ctx.organization_id, {
        "patient_contact_id": contact["id"], "channel": "voice", "state": "revoked",
        "source": src, "note": body.note, "recorded_by": ctx.user_id,
    })
    await db.insert_activity(
        ctx.organization_id, None, actor=f"human:{ctx.user_id}", action="voice_consent_revoked",
        patient_contact_id=contact["id"], details={"consent_id": row["id"], "source": src},
    )
    return {"consent": row}


# --------------------------------------------------------------------------- #
# Enrollment (the recorded human authorization)
# --------------------------------------------------------------------------- #
class Enroll(BaseModel):
    patient_contact_id: str
    scheduled_call_at: str | None = None


@router.get("/api/voice-reminders")
async def list_voice_reminders(
    status_filter: str | None = None, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """The clinic's reminder call history / monitor. RLS-scoped."""
    params = {"select": "*", "order": "created_at.desc"}
    if status_filter:
        params["status"] = f"eq.{status_filter}"
    rows = await rest_get(ctx.access_token, "/voice_reminders", params)
    return {"organization_id": ctx.organization_id, "voice_reminders": rows}


@router.get("/api/appointments/{appointment_id}/voice-reminder")
async def get_appointment_reminder(
    appointment_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    await _visible_appointment(ctx.access_token, appointment_id)
    rows = await rest_get(ctx.access_token, "/voice_reminders", {
        "appointment_id": f"eq.{appointment_id}", "select": "*", "order": "created_at",
    })
    activity = await rest_get(ctx.access_token, "/activity_log", {
        "appointment_id": f"eq.{appointment_id}",
        "select": "actor,action,details,created_at", "order": "created_at",
    })
    vr_activity = [a for a in activity if (a["actor"] or "").startswith("17-voice")
                   or (a["action"] or "").startswith("voice_")]
    return {"voice_reminder": rows[-1] if rows else None, "history": rows, "activity_log": vr_activity}


@router.post("/api/appointments/{appointment_id}/voice-reminder", status_code=status.HTTP_201_CREATED)
async def enroll(appointment_id: str, body: Enroll, ctx: AuthContext = Depends(require_organization)) -> dict:
    org_id = ctx.organization_id
    appt = await _visible_appointment(ctx.access_token, appointment_id)
    contact = await _visible_contact(ctx.access_token, body.patient_contact_id)

    # soft-key match: name + dob (name-only when the appointment has no dob) — §11.14
    if contact["patient_name"].strip().lower() != (appt["patient_name"] or "").strip().lower():
        raise HTTPException(status.HTTP_409_CONFLICT, "contact does not match the appointment's patient")
    if appt.get("patient_dob") and contact.get("patient_dob") and appt["patient_dob"] != contact["patient_dob"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "contact dob does not match the appointment")

    if not appt.get("scheduled_at"):
        raise HTTPException(status.HTTP_409_CONFLICT, "appointment has no scheduled_at")
    scheduled_at = datetime.fromisoformat(str(appt["scheduled_at"]).replace("Z", "+00:00"))
    if scheduled_at <= _now():
        raise HTTPException(status.HTTP_409_CONFLICT, "appointment is in the past")

    existing = await rest_get(ctx.access_token, "/voice_reminders", {
        "appointment_id": f"eq.{appointment_id}", "select": "id,status",
    })
    if any(e["status"] in _ACTIVE for e in existing):
        raise HTTPException(status.HTTP_409_CONFLICT, "an active reminder already exists for this appointment")

    consents = await db.list_patient_consents(org_id, contact["id"])
    cur = voice.current_voice_consent(consents)
    if not cur.granted:
        raise HTTPException(status.HTTP_409_CONFLICT, "no current granted voice consent for this number — capture consent first")

    org = await db.get_organization(org_id)
    lead = timedelta(hours=get_settings().voice_reminder_lead_hours)
    call_at = (
        datetime.fromisoformat(body.scheduled_call_at.replace("Z", "+00:00"))
        if body.scheduled_call_at else scheduled_at - lead
    )

    vr = await db.insert_voice_reminder(org_id, {
        "appointment_id": appointment_id,
        "patient_contact_id": contact["id"],
        "patient_name_snapshot": appt["patient_name"],
        "patient_phone_snapshot": contact["phone"],
        "clinic_name_snapshot": (org or {}).get("name") or "",
        "appointment_at_snapshot": _iso(scheduled_at),
        "timezone_snapshot": (org or {}).get("timezone") or "America/New_York",
        "consent_snapshot": True,
        "consent_event_id_snapshot": cur.event_id,
        "consent_source_snapshot": cur.source,
        "status": "pending",
        "scheduled_call_at": _iso(call_at),
        "authorized_by": ctx.user_id,
        "authorized_at": _iso(_now()),
    })
    await db.insert_activity(
        org_id, None, actor=f"human:{ctx.user_id}", action="voice_reminder_enrolled",
        appointment_id=appointment_id, voice_reminder_id=vr["id"],
        patient_contact_id=contact["id"],
        details={"scheduled_call_at": vr["scheduled_call_at"]},
    )
    return {"voice_reminder": vr}


@router.post("/api/voice-reminders/{vr_id}/cancel", status_code=status.HTTP_201_CREATED)
async def cancel(vr_id: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    rows = await rest_get(ctx.access_token, "/voice_reminders", {"id": f"eq.{vr_id}", "select": "*"})
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice reminder not found")
    if rows[0]["status"] != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"reminder is {rows[0]['status']}, not pending")
    await db.update_voice_reminder(ctx.organization_id, vr_id, {"status": "cancelled", "completed_at": _iso(_now())})
    return {"status": "cancelled"}
