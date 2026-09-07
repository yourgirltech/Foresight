"""Appointments & eligibility endpoints (Phase 2).

Reads go straight through the caller's RLS scope (their own JWT forwarded to
PostgREST). Writes derive `organization_id` from the verified session — never
from the request body — then create the row through the service-role agent
client and hand off to `orchestrator.handle_eligibility`.

THE CARE-SAFETY RULE (docs/agents/01-eligibility-agent.md §2): an
`is_emergency` flag from the request can only make verification *more*
non-blocking. There is no code path here in which an eligibility result gates
the creation of a registration or an appointment — the row is created first and
unconditionally, then the check is kicked off.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..agents import cob, db, orchestrator
from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["eligibility"])


def _latest_per_chain(checks: list[dict]) -> list[dict]:
    """Given eligibility_checks rows, return only the newest of each re-check
    chain (a row that no other row points at via previous_check_id)."""
    superseded = {c["previous_check_id"] for c in checks if c.get("previous_check_id")}
    return [c for c in checks if c["id"] not in superseded]


@router.get("/api/eligibility")
async def list_eligibility(ctx: AuthContext = Depends(require_organization)) -> dict:
    appointments = await rest_get(
        ctx.access_token, "/appointments",
        {"select": "*", "order": "scheduled_at.asc.nullslast,created_at.desc"},
    )
    checks = await rest_get(
        ctx.access_token, "/eligibility_checks",
        {"select": "*", "order": "created_at.desc"},
    )

    # checks arrive newest-first; keep only the newest of each re-check chain
    current = _latest_per_chain(checks)
    latest_by_appt: dict[str, dict] = {}
    unscheduled: list[dict] = []
    for c in current:
        if c.get("appointment_id"):
            latest_by_appt.setdefault(c["appointment_id"], c)
        else:
            unscheduled.append(c)

    return {
        "organization_id": ctx.organization_id,
        "appointments": [
            {**a, "latest_check": latest_by_appt.get(a["id"])} for a in appointments
        ],
        "unscheduled_checks": unscheduled,
    }


@router.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    rows = await rest_get(
        ctx.access_token, "/appointments",
        {"id": f"eq.{appointment_id}", "select": "*,payers(id,name,eligibility_verification_supported,eligibility_active_threshold)"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appointment not found")
    appt = rows[0]
    payer = appt.pop("payers", None)

    checks = await rest_get(
        ctx.access_token, "/eligibility_checks",
        {"appointment_id": f"eq.{appointment_id}", "select": "*", "order": "created_at.asc"},
    )
    pas = await rest_get(
        ctx.access_token, "/prior_authorizations",
        {"appointment_id": f"eq.{appointment_id}", "select": "*", "order": "created_at.asc"},
    )
    # only the newest of each resubmit chain
    pa_superseded = {p["previous_auth_id"] for p in pas if p.get("previous_auth_id")}
    prior_authorizations = [p for p in pas if p["id"] not in pa_superseded]
    activity = await rest_get(
        ctx.access_token, "/activity_log",
        {"appointment_id": f"eq.{appointment_id}", "select": "actor,action,details,created_at",
         "order": "created_at"},
    )

    # Phase 4: the insurance summary — this patient's coverages + COB ordering.
    # Match by the soft patient key (name + dob), like the rest of the codebase.
    cov_params = {"select": "*", "patient_name": f"eq.{appt['patient_name']}",
                  "order": "plan_kind,effective_date"}
    if appt.get("patient_dob"):
        cov_params["patient_dob"] = f"eq.{appt['patient_dob']}"
    coverages = await rest_get(ctx.access_token, "/patient_coverages", cov_params)
    dob = None
    if appt.get("patient_dob"):
        try:
            dob = date.fromisoformat(str(appt["patient_dob"])[:10])
        except ValueError:
            dob = None
    cob_summary = cob.cob_summary(coverages, today=datetime.now(timezone.utc).date(), patient_dob=dob)

    return {"appointment": appt, "payer": payer, "checks": checks,
            "prior_authorizations": prior_authorizations, "activity_log": activity,
            "coverages": coverages, "cob": cob_summary}


@router.get("/api/eligibility-checks/{check_id}")
async def get_eligibility_check(check_id: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    rows = await rest_get(
        ctx.access_token, "/eligibility_checks", {"id": f"eq.{check_id}", "select": "*"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="eligibility check not found")
    check = rows[0]
    # walk the append-only re-check chain both directions from this row
    everything = await rest_get(
        ctx.access_token, "/eligibility_checks", {"select": "*", "order": "created_at.asc"},
    )
    by_id = {c["id"]: c for c in everything}
    prev_of = {c["previous_check_id"]: c for c in everything if c.get("previous_check_id")}
    root = check
    while root.get("previous_check_id") and root["previous_check_id"] in by_id:
        root = by_id[root["previous_check_id"]]
    chain = [root]
    while chain[-1]["id"] in prev_of:
        chain.append(prev_of[chain[-1]["id"]])
    activity = await rest_get(
        ctx.access_token, "/activity_log",
        {"eligibility_check_id": f"eq.{check_id}", "select": "actor,action,details,created_at",
         "order": "created_at"},
    )
    return {"check": check, "chain": chain, "activity_log": activity}


class CreateAppointment(BaseModel):
    patient_name: str
    patient_member_id: str = ""
    payer_id: str | None = None
    scheduled_at: str | None = None
    is_emergency: bool = False


@router.post("/api/appointments", status_code=status.HTTP_201_CREATED)
async def create_appointment(body: CreateAppointment, ctx: AuthContext = Depends(require_organization)) -> dict:
    """Create a scheduled appointment (is_emergency=false) or an emergency
    registration (is_emergency=true — NO appointment row, per the Phase 2
    review). Then kick off verification. The row is created unconditionally;
    the eligibility result never feeds back into whether it exists."""
    org_id = ctx.organization_id
    payer_name = None
    if body.payer_id:
        prows = await rest_get(ctx.access_token, "/payers", {"id": f"eq.{body.payer_id}", "select": "name"})
        payer_name = prows[0]["name"] if prows else None

    appointment_id = None
    if not body.is_emergency:
        appt = await db.insert_appointment(org_id, {
            "patient_name": body.patient_name,
            "patient_member_id": body.patient_member_id,
            "payer_id": body.payer_id,
            "scheduled_at": body.scheduled_at,
            "is_emergency": False,
        })
        appointment_id = appt["id"]

    check = await db.insert_eligibility_check(org_id, {
        "appointment_id": appointment_id,
        "patient_name": body.patient_name,
        "patient_member_id": body.patient_member_id,
        "payer_id": body.payer_id,
        "payer_name": payer_name,
        "is_emergency": body.is_emergency,
        "status": "pending",
    })

    trigger_type = "emergency_patient_registered" if body.is_emergency else "appointment_scheduled"
    decision = await orchestrator.handle_eligibility(check["id"], {
        "type": trigger_type, "payload": {"user_id": ctx.user_id},
    })
    return {
        "appointment_id": appointment_id,
        "eligibility_check_id": check["id"],
        "decision": {"action": decision.action, "reason_code": decision.reason_code,
                     "route_to": decision.route_to},
    }


class Recheck(BaseModel):
    patient_name: str | None = None
    patient_member_id: str | None = None


@router.post("/api/eligibility-checks/{check_id}/recheck", status_code=status.HTTP_201_CREATED)
async def recheck(check_id: str, body: Recheck, ctx: AuthContext = Depends(require_organization)) -> dict:
    """Append a NEW eligibility check for the same patient/appointment (Q3:
    append-only history), optionally with corrected identity, and re-run
    verification. Used by the 'more info added' flow."""
    visible = await rest_get(ctx.access_token, "/eligibility_checks", {"id": f"eq.{check_id}", "select": "*"})
    if not visible:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="eligibility check not found")
    prior = visible[0]
    org_id = ctx.organization_id

    new_check = await db.insert_eligibility_check(org_id, {
        "appointment_id": prior.get("appointment_id"),
        "previous_check_id": prior["id"],
        "patient_name": body.patient_name or prior["patient_name"],
        "patient_member_id": (
            body.patient_member_id if body.patient_member_id is not None
            else prior.get("patient_member_id", "")
        ),
        "payer_id": prior.get("payer_id"),
        "payer_name": prior.get("payer_name"),
        "is_emergency": prior.get("is_emergency", False),
        "status": "pending",
    })
    trigger_type = (
        "emergency_patient_registered" if prior.get("is_emergency") else "appointment_scheduled"
    )
    decision = await orchestrator.handle_eligibility(new_check["id"], {
        "type": trigger_type, "payload": {"user_id": ctx.user_id, "recheck_of": check_id},
    })
    return {
        "eligibility_check_id": new_check["id"],
        "decision": {"action": decision.action, "reason_code": decision.reason_code,
                     "route_to": decision.route_to},
    }
