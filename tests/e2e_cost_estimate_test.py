#!/usr/bin/env python3
"""End-to-end: cost estimate / Good Faith Estimate, through the real endpoints.

Runs the app in-process (httpx ASGITransport). No ANTHROPIC_API_KEY needed —
`phrase()` degrades to the deterministic plain template.

  * self-pay patient + priced codes -> 201; row stored; self_pay_confirmed=true;
    subtotal exact; disclaimer_text == the constant; disclaimer_version stamped;
  * `self_pay: false` -> 409, no row;
  * the same patient given an ACTIVE patient_coverages row -> 409, no new row;
  * an unpriced code -> excluded from subtotal, reported in `unpriced_codes`;
  * all-unpriced -> 422;
  * GET the estimate + the appointment's latest;
  * second-org isolation (prices + estimates).

Run (local stack up):  python tests/e2e_cost_estimate_test.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

from _agentlib import Check, ensure_org, svc_write, user_token  # noqa: E402

from app.agents.cost_estimate import NSA_GFE_DISCLAIMER, NSA_GFE_DISCLAIMER_VERSION  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

A_EMAIL = "e2e_ce_a@foresight.test"
B_EMAIL = "e2e_ce_b@foresight.test"


def _prices(org_id: str) -> None:
    for code, desc, price in (
        ("99213", "Office visit, established patient", 145.00),
        ("36415", "Routine venipuncture", 15.25),
        ("80053", "Comprehensive metabolic panel", 49.99),
    ):
        svc_write("POST", "procedure_prices", {}, {
            "organization_id": org_id, "procedure_code": code,
            "description": desc, "base_price": price, "active": True})


async def main() -> int:
    chk = Check()
    print("End-to-end — cost estimate / Good Faith Estimate (real endpoints)\n")

    org_a, _ = ensure_org(A_EMAIL, "E2E CE — Alpha Clinic")
    org_b, _ = ensure_org(B_EMAIL, "E2E CE — Beta Clinic")
    _prices(org_a)
    _prices(org_b)
    hdr_a = {"Authorization": f"Bearer {user_token(A_EMAIL)}"}
    hdr_b = {"Authorization": f"Bearer {user_token(B_EMAIL)}"}

    appt_a = svc_write("POST", "appointments", {}, {
        "organization_id": org_a, "patient_name": "Sam Selfpay", "patient_dob": "1994-07-01",
        "patient_member_id": "", "scheduled_at": "2026-12-10T00:00:00Z", "is_emergency": False,
    })[0]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        url = f"/api/appointments/{appt_a['id']}/cost-estimate"

        # ---- 1. self_pay not affirmed -> 409 --------------------------
        r = await c.post(url, headers=hdr_a, json={"procedure_codes": ["99213"], "self_pay": False})
        chk("1 gate: self_pay:false -> 409", r.status_code == 409 and "self-pay" in r.text.lower(),
            f"{r.status_code} {r.text[:120]}")

        # ---- 2. self-pay, priced codes -> 201 ------------------------
        r = await c.post(url, headers=hdr_a,
                         json={"procedure_codes": ["99213", "36415", "ZZ999"], "self_pay": True})
        chk("2 create: 201", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
        out = r.json()
        est = out["cost_estimate"]
        chk("2 create: subtotal is the exact sum of the PRICED lines (145.00 + 15.25)",
            float(est["subtotal"]) == 160.25, str(est["subtotal"]))
        chk("2 create: the unpriced code is excluded and reported",
            out["unpriced_codes"] == ["ZZ999"] and len(est["line_items"]) == 2, str(out))
        chk("2 create: self_pay_confirmed = true on the stored row",
            est["self_pay_confirmed"] is True, str(est))
        chk("2 create: disclaimer_text is the constant, verbatim",
            est["disclaimer_text"] == NSA_GFE_DISCLAIMER
            and est["disclaimer_version"] == NSA_GFE_DISCLAIMER_VERSION, "")
        chk("2 create: a patient_summary paragraph carries the exact computed total",
            len(est["patient_summary"]) > 40 and "160.25" in est["patient_summary"], est["patient_summary"])
        has_key = bool(get_settings().anthropic_api_key)
        if has_key:
            chk("2 create: model is recorded (phrase() ran)", est["model"], str(est["model"]))
        else:
            chk("2 create: model is null (phrasing degraded to the plain template)",
                est["model"] is None, str(est["model"]))
        est_id = est["id"]

        # ---- 3. GET the estimate + latest ---------------------------
        g = (await c.get(f"/api/cost-estimates/{est_id}", headers=hdr_a)).json()
        chk("3 get: returns the estimate + the clinic name for the document header",
            g["cost_estimate"]["id"] == est_id and g["clinic_name"] == "E2E CE — Alpha Clinic",
            str(g.get("clinic_name")))
        latest = (await c.get(url, headers=hdr_a)).json()
        chk("3 latest: the appointment's latest estimate is this one",
            latest["cost_estimate"]["id"] == est_id, str(latest))

        # ---- 4. give the patient active coverage -> now 409 --------
        svc_write("POST", "patient_coverages", {}, {
            "organization_id": org_a, "patient_name": "Sam Selfpay", "patient_dob": "1994-07-01",
            "payer_name": "Northwind Health", "coverage_type": "employer_active",
            "relationship_to_subscriber": "self", "is_dependent": False,
            "effective_date": "2020-01-01"})
        before = svc_write("GET", f"cost_estimates?organization_id=eq.{org_a}&select=id", {}, None)
        r = await c.post(url, headers=hdr_a, json={"procedure_codes": ["99213"], "self_pay": True})
        after = svc_write("GET", f"cost_estimates?organization_id=eq.{org_a}&select=id", {}, None)
        chk("4 gate: an insured patient -> 409, naming the payer",
            r.status_code == 409 and "Northwind Health" in r.text, f"{r.status_code} {r.text[:160]}")
        chk("4 gate: NO new cost_estimates row was created", len(after) == len(before),
            f"{len(before)} -> {len(after)}")

        # ---- 5. all-unpriced -> 422 -------------------------------
        appt2 = svc_write("POST", "appointments", {}, {
            "organization_id": org_a, "patient_name": "Pat Nocov", "patient_dob": "1988-02-02",
            "patient_member_id": "", "scheduled_at": "2026-12-11T00:00:00Z", "is_emergency": False})[0]
        r = await c.post(f"/api/appointments/{appt2['id']}/cost-estimate", headers=hdr_a,
                         json={"procedure_codes": ["NOPE1", "NOPE2"], "self_pay": True})
        chk("5 create: all codes unpriced -> 422", r.status_code == 422, f"{r.status_code} {r.text[:120]}")

        # ---- 6. tenant isolation --------------------------------
        r = await c.get(f"/api/cost-estimates/{est_id}", headers=hdr_b)
        chk("6 isolation: org B cannot read org A's estimate (404)", r.status_code == 404, str(r.status_code))
        r = await c.post(f"/api/appointments/{appt_a['id']}/cost-estimate", headers=hdr_b,
                         json={"procedure_codes": ["99213"], "self_pay": True})
        chk("6 isolation: org B cannot estimate against org A's appointment (404)",
            r.status_code == 404, str(r.status_code))
        pp = (await c.get("/api/procedure-prices", headers=hdr_b)).json()
        chk("6 isolation: org B's price list is its own (3 rows, org B)",
            len(pp["procedure_prices"]) == 3 and pp["organization_id"] == org_b, str(len(pp["procedure_prices"])))

    return chk.summary("05 gates on self-pay + no coverage, prices deterministically, "
                       "renders the NSA disclaimer verbatim, and stays tenant-scoped.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
