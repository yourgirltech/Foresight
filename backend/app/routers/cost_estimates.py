"""Cost estimate / Good Faith Estimate endpoints (Phase 4 / 05-cost-estimate-agent).

Reads go through the caller's RLS scope; the single write derives
`organization_id` from the verified session.

THE NON-NEGOTIABLE (docs/agents/05-cost-estimate-agent.md §2): `POST` runs
`cost_estimate.gate_reason()` FIRST — a GFE under the No Surprises Act is for
uninsured / self-pay individuals only. It needs both `self_pay: true` (an
explicit staff affirmation) and no active `patient_coverages` row; either failing
is a 409 and no row is created. The dollar amount is computed by `price()`
(pure) before the model is called; the model only phrases it and its response is
never parsed for a number. The disclaimer is a versioned constant snapshotted
verbatim.

05 is NOT a Commander agent. commander.py is untouched by Phase 4.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..agents import cob, cost_estimate, db
from ..agents.cost_estimate import NSA_GFE_DISCLAIMER, NSA_GFE_DISCLAIMER_VERSION
from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["cost-estimates"])


async def _visible_appointment(access_token: str, appointment_id: str) -> dict:
    rows = await rest_get(
        access_token, "/appointments", {"id": f"eq.{appointment_id}", "select": "*"}
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appointment not found")
    return rows[0]


async def _active_coverages(access_token: str, appt: dict) -> list[dict]:
    params = {"select": "*", "patient_name": f"eq.{appt['patient_name']}"}
    if appt.get("patient_dob"):
        params["patient_dob"] = f"eq.{appt['patient_dob']}"
    rows = await rest_get(access_token, "/patient_coverages", params)
    today = datetime.now(timezone.utc).date()
    return [c for c in rows if cob.is_active(c, today)]


@router.get("/api/procedure-prices")
async def list_procedure_prices(ctx: AuthContext = Depends(require_organization)) -> dict:
    rows = await rest_get(
        ctx.access_token, "/procedure_prices",
        {"select": "*", "active": "eq.true", "order": "procedure_code"},
    )
    return {"organization_id": ctx.organization_id, "procedure_prices": rows}


class CostEstimateRequest(BaseModel):
    procedure_codes: list[str]
    self_pay: bool = False


@router.post("/api/appointments/{appointment_id}/cost-estimate",
             status_code=status.HTTP_201_CREATED)
async def create_cost_estimate(
    appointment_id: str,
    body: CostEstimateRequest,
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    org_id = ctx.organization_id
    appt = await _visible_appointment(ctx.access_token, appointment_id)

    # ---- THE GATE (E1) — before anything else -------------------------------
    active = await _active_coverages(ctx.access_token, appt)
    refusal = cost_estimate.gate_reason(body.self_pay, active)
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)

    # ---- 1. price() — deterministic, before the model ---------------------
    price_rows = await rest_get(
        ctx.access_token, "/procedure_prices", {"select": "*", "active": "eq.true"}
    )
    priced = cost_estimate.price(body.procedure_codes, price_rows)
    if not priced.lines:
        raise HTTPException(
            status_code=422,
            detail=f"none of the requested codes has a price on file: {priced.unpriced_codes}",
        )

    # ---- 2. phrase() — AI, wording only; degrades to a plain template ----
    org_rows = await rest_get(
        ctx.access_token, "/organizations", {"id": f"eq.{org_id}", "select": "name"}
    )
    clinic_name = org_rows[0]["name"] if org_rows else "Our clinic"
    try:
        summary, model = await cost_estimate.phrase(
            priced, patient_name=appt["patient_name"], clinic_name=clinic_name
        )
    except cost_estimate.PhrasingUnavailable:
        summary = cost_estimate.plain_template(priced, patient_name=appt["patient_name"])
        model = None

    # ---- 3. store the document -----------------------------------------
    row = await db.insert_cost_estimate(org_id, {
        "appointment_id": appointment_id,
        "patient_name": appt["patient_name"],
        "patient_dob": appt.get("patient_dob"),
        "line_items": [line.as_dict() for line in priced.lines],
        "subtotal": priced.subtotal,
        "currency": "USD",
        "patient_summary": summary,
        "disclaimer_text": NSA_GFE_DISCLAIMER,
        "disclaimer_version": NSA_GFE_DISCLAIMER_VERSION,
        "self_pay_confirmed": True,
        "model": model,
        "generated_by": ctx.user_id,
    })
    return {"cost_estimate": row, "unpriced_codes": priced.unpriced_codes}


@router.get("/api/cost-estimates/{estimate_id}")
async def get_cost_estimate(
    estimate_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    rows = await rest_get(
        ctx.access_token, "/cost_estimates", {"id": f"eq.{estimate_id}", "select": "*"}
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cost estimate not found")
    estimate = rows[0]
    appointment = None
    if estimate.get("appointment_id"):
        appt = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{estimate['appointment_id']}", "select": "*"}
        )
        appointment = appt[0] if appt else None
    org_rows = await rest_get(
        ctx.access_token, "/organizations", {"id": f"eq.{ctx.organization_id}", "select": "name"}
    )
    return {
        "cost_estimate": estimate,
        "appointment": appointment,
        "clinic_name": org_rows[0]["name"] if org_rows else None,
    }


@router.get("/api/appointments/{appointment_id}/cost-estimate")
async def latest_cost_estimate(
    appointment_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    rows = await rest_get(
        ctx.access_token, "/cost_estimates",
        {"appointment_id": f"eq.{appointment_id}", "select": "*",
         "order": "created_at.desc", "limit": "1"},
    )
    return {"cost_estimate": rows[0] if rows else None}
