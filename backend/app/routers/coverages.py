"""Coordination-of-benefits endpoints (Phase 4 / 04-cob-agent).

Reads go through the caller's RLS scope; writes derive `organization_id` from
the verified session. 04 REPORTS, it does not act — `GET /api/coverages` runs
the pure `determine_cob()` over the caller's active coverages and returns the
ordering with each deciding NAIC rule cited. Nothing downstream is triggered by
a COB result; a human can override the order with `manual_order_override`.

04 is NOT a Commander agent — synchronous, invoked while rendering an insurance
summary. commander.py is untouched by Phase 4.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..agents import cob, db
from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["coverages"])

_RELATIONSHIPS = {"self", "spouse", "child", "other"}
_COVERAGE_TYPES = {
    "employer_active", "employer_retiree", "cobra", "individual",
    "medicare", "medicaid", "tricare", "other",
}


def _today() -> date:
    return datetime.now(timezone.utc).date()


@router.get("/api/coverages")
async def list_coverages(
    patient_name: str,
    patient_dob: str | None = None,
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    """That patient's coverages + `determine_cob()` over the active ones, per
    `plan_kind`. Matches on `patient_name` (+ `patient_dob` when given) — the
    codebase's soft patient key."""
    params = {"select": "*", "patient_name": f"eq.{patient_name}", "order": "plan_kind,effective_date"}
    if patient_dob:
        params["patient_dob"] = f"eq.{patient_dob}"
    rows = await rest_get(ctx.access_token, "/patient_coverages", params)

    dob = None
    try:
        dob = date.fromisoformat(patient_dob) if patient_dob else None
    except ValueError:
        dob = None

    return {
        "patient": {"name": patient_name, "dob": patient_dob},
        "coverages": rows,
        "cob": cob.cob_summary(rows, today=_today(), patient_dob=dob),
    }


class CoverageBody(BaseModel):
    patient_name: str
    patient_dob: str | None = None
    appointment_id: str | None = None
    payer_id: str | None = None
    payer_name: str
    member_id: str = ""
    group_number: str = ""
    plan_kind: str = "medical"
    coverage_type: str = "employer_active"
    relationship_to_subscriber: str = "self"
    is_dependent: bool = False
    subscriber_name: str = ""
    subscriber_dob: str | None = None
    effective_date: str
    termination_date: str | None = None
    manual_order_override: int | None = None


def _validated(fields: dict) -> dict:
    if fields.get("coverage_type") and fields["coverage_type"] not in _COVERAGE_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid coverage_type {fields['coverage_type']!r}")
    if fields.get("relationship_to_subscriber") and fields["relationship_to_subscriber"] not in _RELATIONSHIPS:
        raise HTTPException(
            status_code=422, detail=f"invalid relationship {fields['relationship_to_subscriber']!r}"
        )
    mo = fields.get("manual_order_override")
    if mo is not None and not (1 <= int(mo) <= 9):
        raise HTTPException(status_code=422, detail="manual_order_override must be between 1 and 9")
    return fields


@router.post("/api/coverages", status_code=status.HTTP_201_CREATED)
async def create_coverage(body: CoverageBody, ctx: AuthContext = Depends(require_organization)) -> dict:
    org_id = ctx.organization_id
    fields = _validated(body.model_dump(exclude_none=False))

    if fields.get("appointment_id"):
        visible = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{fields['appointment_id']}", "select": "id"}
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appointment not found")

    if fields.get("payer_id") and not fields.get("payer_name"):
        prows = await rest_get(ctx.access_token, "/payers", {"id": f"eq.{fields['payer_id']}", "select": "name"})
        fields["payer_name"] = prows[0]["name"] if prows else fields.get("payer_name", "")

    row = await db.insert_patient_coverage(org_id, fields)
    return {"coverage": row}


class CoveragePatch(BaseModel):
    payer_name: str | None = None
    member_id: str | None = None
    group_number: str | None = None
    coverage_type: str | None = None
    relationship_to_subscriber: str | None = None
    is_dependent: bool | None = None
    subscriber_name: str | None = None
    subscriber_dob: str | None = None
    effective_date: str | None = None
    termination_date: str | None = None
    plan_kind: str | None = None
    # explicit null clears the pin — send {"manual_order_override": null}
    manual_order_override: int | None = None
    clear_override: bool = False


async def _load_visible(access_token: str, coverage_id: str) -> dict:
    rows = await rest_get(access_token, "/patient_coverages", {"id": f"eq.{coverage_id}", "select": "*"})
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="coverage not found")
    return rows[0]


@router.patch("/api/coverages/{coverage_id}")
async def update_coverage(
    coverage_id: str, body: CoveragePatch, ctx: AuthContext = Depends(require_organization)
) -> dict:
    await _load_visible(ctx.access_token, coverage_id)
    fields = {k: v for k, v in body.model_dump().items()
              if v is not None and k != "clear_override"}
    if body.clear_override:
        fields["manual_order_override"] = None
    fields = _validated(fields)
    if not fields:
        raise HTTPException(status_code=422, detail="no fields to update")
    row = await db.update_patient_coverage(ctx.organization_id, coverage_id, fields)
    return {"coverage": row}


@router.delete("/api/coverages/{coverage_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_coverage(coverage_id: str, ctx: AuthContext = Depends(require_organization)) -> None:
    await _load_visible(ctx.access_token, coverage_id)
    await db.delete_patient_coverage(ctx.organization_id, coverage_id)
