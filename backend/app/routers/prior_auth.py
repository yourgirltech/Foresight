"""Prior-authorization endpoints (Phase 3).

Reads go straight through the caller's RLS scope (their own JWT forwarded to
PostgREST). Writes derive `organization_id` from the verified session — never
from the request body — then create the row through the service-role agent
client and hand off to `orchestrator.handle_prior_auth`.

THE CARE-SAFETY RULE (docs/agents/02-prior-auth-agent.md §2): an `is_emergency`
flag (or `place_of_service = 'emergency'`) from the request can only make the
flow *more* non-blocking. There is no code path here in which a prior-auth
result gates the creation of anything — the row is created first and
unconditionally, then the determination is kicked off. Foresight never sets a
state that blocks a visit; it only records auth status.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..agents import db, orchestrator
from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["prior-auth"])

_PLACES = {"office", "outpatient", "inpatient", "emergency"}
_NEEDS_HUMAN = {"required_draft", "auth_denied", "info_needed"}


def _chain(rows: list[dict], head: dict) -> list[dict]:
    """Walk the append-only resubmit chain in creation order, given all the
    org's PA rows and one row in the chain."""
    by_id = {r["id"]: r for r in rows}
    prev_of = {r["previous_auth_id"]: r for r in rows if r.get("previous_auth_id")}
    root = head
    while root.get("previous_auth_id") and root["previous_auth_id"] in by_id:
        root = by_id[root["previous_auth_id"]]
    out = [root]
    while out[-1]["id"] in prev_of:
        out.append(prev_of[out[-1]["id"]])
    return out


@router.get("/api/prior-authorizations")
async def list_prior_authorizations(
    status_filter: str | None = None,
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    params = {"select": "*", "order": "created_at.desc"}
    if status_filter:
        params["status"] = f"eq.{status_filter}"
    rows = await rest_get(ctx.access_token, "/prior_authorizations", params)

    # keep only the newest of each resubmit chain for the queue view
    superseded = {r["previous_auth_id"] for r in rows if r.get("previous_auth_id")}
    current = [r for r in rows if r["id"] not in superseded]
    return {
        "organization_id": ctx.organization_id,
        "prior_authorizations": current,
        "needs_action": [r for r in current if r["status"] in _NEEDS_HUMAN],
    }


@router.get("/api/prior-authorizations/{pa_id}")
async def get_prior_authorization(
    pa_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    rows = await rest_get(
        ctx.access_token, "/prior_authorizations",
        {"id": f"eq.{pa_id}", "select": "*"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="prior authorization not found")
    pa = rows[0]

    everything = await rest_get(
        ctx.access_token, "/prior_authorizations", {"select": "*", "order": "created_at.asc"},
    )
    chain = _chain(everything, pa)

    activity = await rest_get(
        ctx.access_token, "/activity_log",
        {"prior_authorization_id": f"eq.{pa_id}", "select": "actor,action,details,created_at",
         "order": "created_at"},
    )
    appointment = None
    if pa.get("appointment_id"):
        appt_rows = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{pa['appointment_id']}", "select": "*"},
        )
        appointment = appt_rows[0] if appt_rows else None
    return {"prior_authorization": pa, "chain": chain, "activity_log": activity, "appointment": appointment}


class CreatePriorAuth(BaseModel):
    appointment_id: str | None = None
    patient_name: str
    patient_member_id: str = ""
    payer_id: str | None = None
    procedure_code: str
    procedure_description: str = ""
    place_of_service: str = "office"
    is_emergency: bool = False


@router.post("/api/prior-authorizations", status_code=status.HTTP_201_CREATED)
async def create_prior_authorization(
    body: CreatePriorAuth, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """Create a prior-authorization workflow row for a planned procedure and kick
    off the determination. The row is created unconditionally; the determination
    result never feeds back into whether it exists. An emergency flag / POS can
    only make the flow more non-blocking."""
    org_id = ctx.organization_id
    place = body.place_of_service if body.place_of_service in _PLACES else "office"

    payer_name = None
    if body.payer_id:
        prows = await rest_get(ctx.access_token, "/payers", {"id": f"eq.{body.payer_id}", "select": "name"})
        payer_name = prows[0]["name"] if prows else None

    if body.appointment_id:
        visible = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{body.appointment_id}", "select": "id"},
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appointment not found")

    is_emergency = bool(body.is_emergency) or place == "emergency"
    pa = await db.insert_prior_authorization(org_id, {
        "appointment_id": body.appointment_id,
        "patient_name": body.patient_name,
        "patient_member_id": body.patient_member_id,
        "payer_id": body.payer_id,
        "payer_name": payer_name,
        "procedure_code": body.procedure_code,
        "procedure_description": body.procedure_description,
        "place_of_service": place,
        "is_emergency": is_emergency,
        "status": "pending",
    })

    trigger_type = "prior_auth_emergency" if is_emergency else "prior_auth_requested"
    decision = await orchestrator.handle_prior_auth(pa["id"], {
        "type": trigger_type, "payload": {"user_id": ctx.user_id},
    })
    return {
        "prior_authorization_id": pa["id"],
        "decision": {"action": decision.action, "reason_code": decision.reason_code,
                     "route_to": decision.route_to},
    }


async def _load_visible_pa(access_token: str, pa_id: str) -> dict:
    rows = await rest_get(access_token, "/prior_authorizations", {"id": f"eq.{pa_id}", "select": "*"})
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="prior authorization not found")
    return rows[0]


@router.post("/api/prior-authorizations/{pa_id}/approve-submission", status_code=status.HTTP_201_CREATED)
async def approve_submission(pa_id: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    """A human approves submitting the drafted request. Only valid on a row that
    is `required_draft` and visible to the caller."""
    pa = await _load_visible_pa(ctx.access_token, pa_id)
    if pa["status"] != "required_draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"prior authorization is {pa['status']}, not required_draft",
        )
    await db.update_prior_authorization(ctx.organization_id, pa_id, {"decided_by": ctx.user_id})
    await db.insert_activity(
        ctx.organization_id, None, actor=f"human:{ctx.user_id}", action="prior_auth_submission_approved",
        prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"), details={},
    )
    decision = await orchestrator.handle_prior_auth(pa_id, {
        "type": "prior_auth_submission_approved", "payload": {"user_id": ctx.user_id},
    })
    return {"decision": {"action": decision.action, "reason_code": decision.reason_code,
                         "route_to": decision.route_to}}


@router.post("/api/prior-authorizations/{pa_id}/decline-submission", status_code=status.HTTP_201_CREATED)
async def decline_submission(pa_id: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    """A human declines submitting the drafted request."""
    pa = await _load_visible_pa(ctx.access_token, pa_id)
    if pa["status"] != "required_draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"prior authorization is {pa['status']}, not required_draft",
        )
    from datetime import datetime, timezone
    await db.update_prior_authorization(ctx.organization_id, pa_id, {
        "status": "submission_declined",
        "decided_by": ctx.user_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.insert_activity(
        ctx.organization_id, None, actor=f"human:{ctx.user_id}", action="prior_auth_submission_declined",
        prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"), details={},
    )
    decision = await orchestrator.handle_prior_auth(pa_id, {
        "type": "prior_auth_submission_declined", "payload": {"user_id": ctx.user_id},
    })
    return {"decision": {"action": decision.action, "reason_code": decision.reason_code,
                         "route_to": decision.route_to}}


class Resubmit(BaseModel):
    procedure_code: str | None = None
    procedure_description: str | None = None
    added_clinical_note: str | None = None


@router.post("/api/prior-authorizations/{pa_id}/resubmit", status_code=status.HTTP_201_CREATED)
async def resubmit(pa_id: str, body: Resubmit, ctx: AuthContext = Depends(require_organization)) -> dict:
    """Append a NEW chained prior-authorization row (optionally with a corrected
    procedure code / added clinicals) and re-run — the info_needed / denied
    follow-up flow. History is preserved."""
    prior = await _load_visible_pa(ctx.access_token, pa_id)
    org_id = ctx.organization_id

    new_pa = await db.insert_prior_authorization(org_id, {
        "appointment_id": prior.get("appointment_id"),
        "previous_auth_id": prior["id"],
        "patient_name": prior["patient_name"],
        "patient_member_id": prior.get("patient_member_id", ""),
        "payer_id": prior.get("payer_id"),
        "payer_name": prior.get("payer_name"),
        "procedure_code": body.procedure_code or prior.get("procedure_code", ""),
        "procedure_description": body.procedure_description or prior.get("procedure_description", ""),
        "place_of_service": prior.get("place_of_service", "office"),
        "is_emergency": prior.get("is_emergency", False),
        "status": "pending",
    })
    trigger_type = (
        "prior_auth_emergency" if prior.get("is_emergency") else "prior_auth_requested"
    )
    decision = await orchestrator.handle_prior_auth(new_pa["id"], {
        "type": trigger_type,
        "payload": {"user_id": ctx.user_id, "resubmit_of": pa_id,
                    "added_clinical_note": body.added_clinical_note},
    })
    return {
        "prior_authorization_id": new_pa["id"],
        "decision": {"action": decision.action, "reason_code": decision.reason_code,
                     "route_to": decision.route_to},
    }
