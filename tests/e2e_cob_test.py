#!/usr/bin/env python3
"""End-to-end: coordination of benefits, through the real FastAPI endpoints.

Runs the app in-process (httpx ASGITransport). Exercises:
  * POST /api/coverages -> the row is created, nothing computed yet;
  * GET /api/coverages -> determine_cob() over the active coverages, per plan_kind,
    with the deciding rule cited;
  * an inactive (terminated) coverage shows in `coverages` but not in `cob`;
  * PATCH sets / clears manual_order_override -> R0 then back to a rule;
  * DELETE removes a coverage;
  * GET /api/appointments/{id} carries `coverages` + `cob` inline;
  * tenant isolation: org B never sees / edits org A's coverages.

No ANTHROPIC_API_KEY needed. Run (local stack up):  python tests/e2e_cob_test.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import date, timedelta

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

from _agentlib import Check, ensure_org, svc_write, user_token  # noqa: E402

from app.main import app  # noqa: E402

A_EMAIL = "e2e_cob_a@foresight.test"
B_EMAIL = "e2e_cob_b@foresight.test"
PATIENT = "Dana Coordination"
DOB = "1990-04-15"
TODAY = date.today()


def _coverage(**kw) -> dict:
    body = {
        "patient_name": PATIENT, "patient_dob": DOB,
        "payer_name": "Payer", "effective_date": "2020-01-01",
        "coverage_type": "employer_active", "relationship_to_subscriber": "self",
        "is_dependent": False, "plan_kind": "medical",
    }
    body.update(kw)
    return body


async def main() -> int:
    chk = Check()
    print("End-to-end — coordination of benefits (real endpoints)\n")

    org_a, _ = ensure_org(A_EMAIL, "E2E COB — Alpha")
    org_b, _ = ensure_org(B_EMAIL, "E2E COB — Beta")
    hdr_a = {"Authorization": f"Bearer {user_token(A_EMAIL)}"}
    hdr_b = {"Authorization": f"Bearer {user_token(B_EMAIL)}"}

    appt_a = svc_write("POST", "appointments", {}, {
        "organization_id": org_a, "patient_name": PATIENT, "patient_dob": DOB,
        "patient_member_id": "", "scheduled_at": "2026-12-05T00:00:00Z", "is_emergency": False,
    })[0]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # ---- 1. add three coverages ------------------------------------
        own = (await c.post("/api/coverages", headers=hdr_a, json=_coverage(
            payer_name="Own Health", appointment_id=appt_a["id"]))).json()["coverage"]
        spouse = (await c.post("/api/coverages", headers=hdr_a, json=_coverage(
            payer_name="Spouse Group", relationship_to_subscriber="spouse", is_dependent=True))).json()["coverage"]
        mcd = (await c.post("/api/coverages", headers=hdr_a, json=_coverage(
            payer_name="State Medicaid", coverage_type="medicaid"))).json()["coverage"]
        chk("1 add: three coverage rows created", all(x.get("id") for x in (own, spouse, mcd)),
            str([own, spouse, mcd]))

        # a terminated dental coverage — should show but never rank
        term = (await c.post("/api/coverages", headers=hdr_a, json=_coverage(
            payer_name="Old Dental", plan_kind="dental",
            effective_date="2019-01-01",
            termination_date=(TODAY - timedelta(days=30)).isoformat()))).json()["coverage"]

        # ---- 2. GET /api/coverages -> the ordering ---------------------
        r = await c.get("/api/coverages", headers=hdr_a,
                        params={"patient_name": PATIENT, "patient_dob": DOB})
        body = r.json()
        chk("2 get: 200, all four coverages returned", r.status_code == 200
            and len(body["coverages"]) == 4, str(r.status_code))
        med = {p["coverage_id"]: p for p in body["cob"].get("medical", [])}
        chk("2 get: own plan primary (R1)",
            med.get(own["id"], {}).get("order") == "primary" and med[own["id"]]["rule"] == "R1", str(med))
        chk("2 get: spouse plan secondary (R1)",
            med.get(spouse["id"], {}).get("order") == "secondary" and med[spouse["id"]]["rule"] == "R1",
            str(med))
        chk("2 get: Medicaid tertiary (R2), rationale mentions last resort",
            med.get(mcd["id"], {}).get("order") == "tertiary" and med[mcd["id"]]["rule"] == "R2"
            and "last resort" in med[mcd["id"]]["rationale"].lower(), str(med))
        chk("2 get: exactly one primary in the medical group",
            sum(1 for p in body["cob"]["medical"] if p["order"] == "primary") == 1, str(body["cob"]))
        chk("2 get: the terminated dental coverage is NOT ranked",
            "dental" not in body["cob"], str(body["cob"]))

        # ---- 3. pin Medicaid to primary -> R0 ------------------------
        await c.patch(f"/api/coverages/{mcd['id']}", headers=hdr_a, json={"manual_order_override": 1})
        body = (await c.get("/api/coverages", headers=hdr_a,
                            params={"patient_name": PATIENT, "patient_dob": DOB})).json()
        med = {p["coverage_id"]: p for p in body["cob"]["medical"]}
        chk("3 pin: Medicaid is now primary, cited R0",
            med[mcd["id"]]["order"] == "primary" and med[mcd["id"]]["rule"] == "R0", str(med))
        chk("3 pin: still exactly one primary",
            sum(1 for p in body["cob"]["medical"] if p["order"] == "primary") == 1, str(med))

        # ---- 4. clear the pin -> back to a rule ---------------------
        await c.patch(f"/api/coverages/{mcd['id']}", headers=hdr_a, json={"clear_override": True})
        body = (await c.get("/api/coverages", headers=hdr_a,
                            params={"patient_name": PATIENT, "patient_dob": DOB})).json()
        med = {p["coverage_id"]: p for p in body["cob"]["medical"]}
        chk("4 clear: Medicaid back to tertiary (R2)",
            med[mcd["id"]]["order"] == "tertiary" and med[mcd["id"]]["rule"] == "R2", str(med))

        # ---- 5. appointment detail carries coverages + cob ----------
        appt = (await c.get(f"/api/appointments/{appt_a['id']}", headers=hdr_a)).json()
        chk("5 appt: detail includes coverages + a cob summary",
            len(appt.get("coverages", [])) == 4 and appt["cob"]["medical"][0]["order"] == "primary",
            str(appt.get("cob")))

        # ---- 6. delete ----------------------------------------------
        d = await c.delete(f"/api/coverages/{term['id']}", headers=hdr_a)
        chk("6 delete: 204", d.status_code == 204, str(d.status_code))
        body = (await c.get("/api/coverages", headers=hdr_a,
                            params={"patient_name": PATIENT})).json()
        chk("6 delete: coverage is gone", all(x["id"] != term["id"] for x in body["coverages"]),
            str(len(body["coverages"])))

        # ---- 7. tenant isolation ----------------------------------
        b_body = (await c.get("/api/coverages", headers=hdr_b,
                              params={"patient_name": PATIENT, "patient_dob": DOB})).json()
        chk("7 isolation: org B sees no coverages for the same patient key",
            b_body["coverages"] == [] and b_body["cob"] == {}, str(b_body))
        r = await c.patch(f"/api/coverages/{own['id']}", headers=hdr_b, json={"member_id": "HACK"})
        chk("7 isolation: org B cannot PATCH org A's coverage (404)", r.status_code == 404, str(r.status_code))
        r = await c.delete(f"/api/coverages/{own['id']}", headers=hdr_b)
        chk("7 isolation: org B cannot DELETE org A's coverage (404)", r.status_code == 404, str(r.status_code))
        still = svc_write("GET", f"patient_coverages?id=eq.{own['id']}&select=member_id", {}, None)[0]
        chk("7 isolation: org A's coverage is unchanged", still["member_id"] == "", str(still))

        # ---- 8. validation ---------------------------------------
        r = await c.post("/api/coverages", headers=hdr_a,
                         json=_coverage(coverage_type="bogus"))
        chk("8 validation: an unknown coverage_type is 422", r.status_code == 422, str(r.status_code))

    return chk.summary("04 orders coverages deterministically with every rule cited; tenants stay isolated.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
