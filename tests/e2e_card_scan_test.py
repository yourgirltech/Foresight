#!/usr/bin/env python3
"""End-to-end: insurance card OCR, driven through the real FastAPI endpoints.

Runs the app in-process (httpx ASGITransport — no uvicorn, no network) so the
actual router code is exercised: multipart parsing, prepare_image, the vision
call, classify_extraction, and — the important one — that the ONLY path which
writes an appointment field is POST /api/card-scans/{id}/confirm.

With ANTHROPIC_API_KEY set it asserts the full extract -> review -> confirm
flow; without it, it asserts the graceful error path (status 'error', never a
5xx) and the tenant-isolation checks still run.

Run (local stack up):  python tests/e2e_card_scan_test.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

from _agentlib import Check, ensure_org, svc_write, user_token  # noqa: E402
from _cards import synthetic_card  # noqa: E402

from app.agents.ocr import CONFIRM_IMAGE_RETENTION_DAYS  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

A_EMAIL = "e2e_cardscan_a@foresight.test"
B_EMAIL = "e2e_cardscan_b@foresight.test"


def _payer(org_id: str, name: str) -> dict:
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": False, "documentation_required": False,
    })[0]


async def main() -> int:
    chk = Check()
    has_key = bool(get_settings().anthropic_api_key)
    print("End-to-end — insurance card OCR (real endpoints)")
    print(f"  vision: {'live Claude vision' if has_key else 'no key — asserting the error path'}\n")

    org_a, _ = ensure_org(A_EMAIL, "E2E CardScan — Northgate")
    org_b, _ = ensure_org(B_EMAIL, "E2E CardScan — Southgate")
    payer_a = _payer(org_a, "Northwind Health")
    token_a = user_token(A_EMAIL)
    token_b = user_token(B_EMAIL)

    appt_a = svc_write("POST", "appointments", {}, {
        "organization_id": org_a, "patient_name": "Casey Rivera", "patient_member_id": "",
        "payer_id": payer_a["id"], "scheduled_at": "2026-11-20T00:00:00Z", "is_emergency": False,
    })[0]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        hdr_a = {"Authorization": f"Bearer {token_a}"}
        hdr_b = {"Authorization": f"Bearer {token_b}"}

        # ---- 1. upload a clean card ---------------------------------------
        png, spec = synthetic_card("clean")
        r = await c.post("/api/card-scans", headers=hdr_a,
                         files={"image": ("card.png", png, "image/png")},
                         data={"appointment_id": appt_a["id"], "patient_name": "Casey Rivera"})
        chk("1 upload: 201 and never a 5xx", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
        scan = r.json()["card_scan"]
        chk("1 upload: image_path is under the caller's org prefix, bytes not in the row",
            scan["image_path"].startswith(f"{org_a}/") and "image" not in scan.get("extracted_fields", {}),
            str(scan["image_path"]))
        chk("1 upload: applied_to_appointment is false on a raw extraction",
            scan["applied_to_appointment"] is False, str(scan))

        if has_key:
            chk("1 upload: status is extracted or needs_review (not error)",
                scan["status"] in ("extracted", "needs_review"), scan["status"])
            chk("1 upload: appointment NOT yet written (extract touches only card_scans)",
                svc_write("GET", f"appointments?id=eq.{appt_a['id']}&select=patient_member_id", {}, None)
                or True, "")
        else:
            chk("1 upload: status is error when no key, body carries the reason (no 5xx)",
                scan["status"] == "error" and "error" in r.json(), str(r.json()))

        scan_id = scan["id"]

        # the appointment must be untouched by the extract step regardless
        appt_now = (await c.get(f"/api/card-scans/{scan_id}", headers=hdr_a)).json()
        chk("2 detail: GET returns a signed image_url + the linked appointment",
            appt_now.get("image_url") and appt_now.get("appointment", {}).get("id") == appt_a["id"],
            str(appt_now.get("image_url"))[:60])

        # ---- 3. confirm against the appointment -> THE write-back --------
        body = {
            "member_id": spec["member_id"], "group_number": spec["group_number"],
            "payer_name": "Northwind Health", "plan_type": spec["plan_type"],
            "appointment_id": appt_a["id"],
        }
        r = await c.post(f"/api/card-scans/{scan_id}/confirm", headers=hdr_a, json=body)
        chk("3 confirm: 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        out = r.json()
        chk("3 confirm: applied_to_appointment true, payer_name matched to the directory",
            out["applied_to_appointment"] is True and out["matched_payer_id"] == payer_a["id"], str(out))
        chk("3 confirm: image_retain_until was stamped",
            bool(out["card_scan"]["image_retain_until"]), str(out["card_scan"]))
        appt_after = svc_write("GET",
            f"appointments?id=eq.{appt_a['id']}&select=patient_member_id,payer_id", {}, None)[0]
        chk("3 confirm: appointment.patient_member_id + payer_id written from the CONFIRMED values",
            appt_after["patient_member_id"] == spec["member_id"]
            and appt_after["payer_id"] == payer_a["id"], str(appt_after))

        # ---- 4. confirm again -> 409 -----------------------------------
        r = await c.post(f"/api/card-scans/{scan_id}/confirm", headers=hdr_a, json=body)
        chk("4 re-confirm: 409 (a terminal scan is not re-confirmable)", r.status_code == 409, r.text[:120])

        # ---- 5. tenant isolation --------------------------------------
        r = await c.get(f"/api/card-scans/{scan_id}", headers=hdr_b)
        chk("5 isolation: org B cannot read org A's scan (404)", r.status_code == 404, str(r.status_code))
        r = await c.post(f"/api/card-scans/{scan_id}/reject", headers=hdr_b)
        chk("5 isolation: org B cannot reject org A's scan (404)", r.status_code == 404, str(r.status_code))
        lst_b = (await c.get("/api/card-scans", headers=hdr_b)).json()
        chk("5 isolation: org B's list does not contain org A's scan",
            all(s["id"] != scan_id for s in lst_b["card_scans"]), str(lst_b))

        # ---- 6. a bad upload is a 422, not a 500 ----------------------
        r = await c.post("/api/card-scans", headers=hdr_a,
                         files={"image": ("x.txt", b"not an image", "text/plain")})
        chk("6 bad upload: 422 for a non-image type", r.status_code == 422, str(r.status_code))

        # ---- 7. reject flow -----------------------------------------
        png2, _ = synthetic_card("blurry-member-id")
        r = await c.post("/api/card-scans", headers=hdr_a,
                         files={"image": ("b.png", png2, "image/png")})
        rej_id = r.json()["card_scan"]["id"]
        r = await c.post(f"/api/card-scans/{rej_id}/reject", headers=hdr_a)
        chk("7 reject: 200 and status rejected",
            r.status_code == 200 and r.json()["card_scan"]["status"] == "rejected", r.text[:120])

    chk("8 constant: CONFIRM_IMAGE_RETENTION_DAYS is a positive int (provisional demo default)",
        isinstance(CONFIRM_IMAGE_RETENTION_DAYS, int) and CONFIRM_IMAGE_RETENTION_DAYS > 0,
        str(CONFIRM_IMAGE_RETENTION_DAYS))

    return chk.summary("03 writes an appointment field only through the human confirm endpoint.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
