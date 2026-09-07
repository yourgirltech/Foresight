"""Insurance card OCR endpoints (Phase 4 / 03-ocr-agent).

Reads go straight through the caller's RLS scope (their own JWT forwarded to
PostgREST). Writes derive `organization_id` from the verified session — never
from the request body — then go through the service-role agent client.

THE NON-NEGOTIABLE (docs/agents/03-ocr-agent.md §2): 03 is a DATA-ENTRY AID, not
an autonomous action. `POST /api/card-scans` writes ONLY to card_scans. The sole
path that writes an appointment field is `POST /api/card-scans/{id}/confirm`,
which needs an explicit human action and carries the human's corrected values.
`applied_to_appointment` starts false and only that endpoint flips it.

03 is NOT a Commander agent (docs/PHASE-4.md): synchronous request/response, no
workflow state, no routing. commander.py is untouched by Phase 4.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..agents import db, ocr
from ..agents.ocr import CONFIRM_IMAGE_RETENTION_DAYS
from ..auth import AuthContext, require_organization
from ..config import get_settings
from ..storage import create_signed_url, delete_object, upload_object
from ..supabase_rest import rest_get

router = APIRouter(tags=["card-scans"])

BUCKET = "card-scans"
_TERMINAL = {"confirmed", "rejected"}


async def _load_visible(access_token: str, scan_id: str) -> dict:
    rows = await rest_get(access_token, "/card_scans", {"id": f"eq.{scan_id}", "select": "*"})
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card scan not found")
    return rows[0]


@router.get("/api/card-scans")
async def list_card_scans(
    status_filter: str | None = None,
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    params = {"select": "*", "order": "created_at.desc"}
    if status_filter:
        params["status"] = f"eq.{status_filter}"
    rows = await rest_get(ctx.access_token, "/card_scans", params)
    return {
        "organization_id": ctx.organization_id,
        "card_scans": rows,
        "needs_review": [r for r in rows if r["status"] == "needs_review"],
    }


@router.post("/api/card-scans", status_code=status.HTTP_201_CREATED)
async def create_card_scan(
    image: UploadFile = File(...),
    appointment_id: str | None = Form(None),
    patient_name: str = Form(""),
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    """Store the uploaded card image (private bucket), run the Claude vision
    extraction, classify it, and return the row. A vision failure is recorded as
    `status = error` with a 200/201 body — never a 5xx — and no field is guessed.
    Nothing here writes an appointment or patient record."""
    org_id = ctx.organization_id
    raw = await image.read()
    try:
        img_bytes, mime, ext = ocr.prepare_image(raw, image.content_type or "")
    except ocr.UnreadableImage as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if appointment_id:
        visible = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{appointment_id}", "select": "id"}
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appointment not found")

    scan_id = str(uuid.uuid4())
    image_path = f"{org_id}/{scan_id}.{ext}"
    await upload_object(BUCKET, image_path, img_bytes, mime)

    row = await db.insert_card_scan(org_id, {
        "id": scan_id,
        "appointment_id": appointment_id,
        "patient_name": patient_name or "",
        "image_path": image_path,
        "image_mime": mime,
        "status": "pending",
    })

    try:
        extraction = await ocr.extract(img_bytes, mime)
    except ocr.OcrUnavailable as exc:
        row = await db.update_card_scan(org_id, scan_id, {
            "status": "error",
            "extracted_fields": {"error": str(exc)},
            "field_confidence": {},
        })
        return {"card_scan": row, "error": str(exc)}

    floor = get_settings().ocr_confidence_floor
    new_status = ocr.classify_extraction(extraction, floor=floor)
    row = await db.update_card_scan(org_id, scan_id, {
        "status": new_status,
        "extracted_fields": extraction.fields,
        "field_confidence": extraction.confidence,
        "model": extraction.model,
    })
    return {"card_scan": row}


@router.get("/api/card-scans/{scan_id}")
async def get_card_scan(
    scan_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    scan = await _load_visible(ctx.access_token, scan_id)
    image_url: str | None = None
    try:
        image_url = await create_signed_url(BUCKET, scan["image_path"])
    except Exception:  # noqa: BLE001 — a missing image must not 500 the detail view
        image_url = None

    appointment = None
    if scan.get("appointment_id"):
        appt = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{scan['appointment_id']}", "select": "*"}
        )
        appointment = appt[0] if appt else None
    return {"card_scan": scan, "image_url": image_url, "appointment": appointment}


class ConfirmCardScan(BaseModel):
    member_id: str | None = None
    group_number: str | None = None
    payer_name: str | None = None
    plan_type: str | None = None
    appointment_id: str | None = None


@router.post("/api/card-scans/{scan_id}/confirm")
async def confirm_card_scan(
    scan_id: str, body: ConfirmCardScan, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """A human confirms the (possibly corrected) fields. THE ONLY write-back
    path. Writes `appointments.patient_member_id` — and `payer_id` when
    `payer_name` exactly matches a payer in the caller's directory — only if an
    `appointment_id` is given and visible to the caller. Sets the scan
    `confirmed`, records the reviewer, flips `applied_to_appointment`, and stamps
    `image_retain_until` (see ocr.CONFIRM_IMAGE_RETENTION_DAYS)."""
    org_id = ctx.organization_id
    scan = await _load_visible(ctx.access_token, scan_id)
    if scan["status"] in _TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"card scan is already {scan['status']}",
        )

    confirmed = {
        "member_id": (body.member_id or "").strip() or None,
        "group_number": (body.group_number or "").strip() or None,
        "payer_name": (body.payer_name or "").strip() or None,
        "plan_type": (body.plan_type or "").strip() or None,
    }

    appointment_id = body.appointment_id or scan.get("appointment_id")
    applied = False
    matched_payer_id: str | None = None
    if appointment_id:
        visible = await rest_get(
            ctx.access_token, "/appointments", {"id": f"eq.{appointment_id}", "select": "id"}
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appointment not found")

        appt_update: dict = {}
        if confirmed["member_id"]:
            appt_update["patient_member_id"] = confirmed["member_id"]
        if confirmed["payer_name"]:
            want = confirmed["payer_name"].strip().lower()
            payers = await rest_get(ctx.access_token, "/payers", {"select": "id,name"})
            matched_payer_id = next(
                (p["id"] for p in payers if (p.get("name") or "").strip().lower() == want), None
            )
            if matched_payer_id:
                appt_update["payer_id"] = matched_payer_id
        if appt_update:
            await db.update_appointment(org_id, appointment_id, appt_update)
            applied = True

    now = datetime.now(timezone.utc)
    row = await db.update_card_scan(org_id, scan_id, {
        "status": "confirmed",
        "extracted_fields": {**(scan.get("extracted_fields") or {}), "confirmed": confirmed},
        "appointment_id": appointment_id,
        "reviewed_by": ctx.user_id,
        "reviewed_at": now.isoformat(),
        "applied_to_appointment": applied,
        "image_retain_until": (now + timedelta(days=CONFIRM_IMAGE_RETENTION_DAYS)).isoformat(),
    })
    await db.insert_activity(
        org_id, None, actor=f"human:{ctx.user_id}", action="card_scan_confirmed",
        appointment_id=appointment_id,
        details={"card_scan_id": scan_id, "applied_to_appointment": applied,
                 "matched_payer_id": matched_payer_id},
    )
    return {
        "card_scan": row,
        "applied_to_appointment": applied,
        "appointment_id": appointment_id,
        "matched_payer_id": matched_payer_id,
        "image_retain_until": row.get("image_retain_until") if row else None,
    }


@router.post("/api/card-scans/{scan_id}/reject")
async def reject_card_scan(
    scan_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """A human discards the scan. The stored image is deleted immediately
    (retention 0 on reject — only confirm keeps the image, briefly)."""
    org_id = ctx.organization_id
    scan = await _load_visible(ctx.access_token, scan_id)
    if scan["status"] == "confirmed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="card scan is already confirmed"
        )
    try:
        await delete_object(BUCKET, scan["image_path"])
    except Exception:  # noqa: BLE001 — the row transition matters more than the object
        pass
    row = await db.update_card_scan(org_id, scan_id, {
        "status": "rejected",
        "reviewed_by": ctx.user_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.insert_activity(
        org_id, None, actor=f"human:{ctx.user_id}", action="card_scan_rejected",
        appointment_id=scan.get("appointment_id"), details={"card_scan_id": scan_id},
    )
    return {"card_scan": row}
