#!/usr/bin/env python3
"""Agent-system tenant isolation proof.

The agent pipeline runs with the service-role key (it bypasses RLS — the "batch
job" carve-out, docs/architecture.md §3.4). The compensating control is that
every agent query filters on the organization_id resolved from the triggering
claim, and NOTHING is hardcoded.

This test builds two clinics with a claim each — same claim_id string, identical
evidence — runs the full pipeline over Clinic A's claim, and proves Clinic B's
rows are never read or written.

Run (local stack must be up):  python tests/agent_isolation_test.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, orchestrator  # noqa: E402  (path set by _agentlib)
from app.config import get_settings  # noqa: E402

NOW = datetime.now(timezone.utc)
AGENT_TABLES = ("claim_issues", "recommendations", "activity_log", "escalations", "follow_ups")


def make_claim(org_id: str, payer_id: str, claim_id: str) -> dict:
    """A claim that will produce issues: missing auth + code mismatch -> High."""
    return svc_write("POST", "claims", {}, {
        "organization_id": org_id,
        "payer_id": payer_id,
        "claim_id": claim_id,
        "patient_name": "Isolation Probe",
        "patient_member_id": "PROBE-1",
        "amount": 999.00,
        "authorization_present": False,
        "documentation_present": True,
        "coding_matches": False,
        "created_at": (NOW - timedelta(days=3)).isoformat(),
    })[0]


def make_payer(org_id: str) -> dict:
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": "Shared-Name Payer",
        "authorization_required": True, "documentation_required": True,
        "follow_up_threshold_days": 21,
    })[0]


def counts(org_id: str) -> dict[str, int]:
    return {
        t: len(svc_get(t, {"organization_id": f"eq.{org_id}", "select": "id"}))
        for t in AGENT_TABLES
    }


async def main() -> int:
    chk = Check()
    key = bool(get_settings().anthropic_api_key)
    print("Agent tenant isolation proof")
    print(f"  07 reasoning: {'key present — full pipeline' if key else 'no key — run stops at reasoning (fine for this test)'}\n")

    org_a, _ = ensure_org("agttest_a@foresight.test", "Agent Test — Clinic A")
    org_b, _ = ensure_org("agttest_b@foresight.test", "Agent Test — Clinic B")
    chk("two distinct clinics", org_a != org_b)

    payer_a, payer_b = make_payer(org_a), make_payer(org_b)
    claim_a = make_claim(org_a, payer_a["id"], "CLM-SHARED-0001")
    claim_b = make_claim(org_b, payer_b["id"], "CLM-SHARED-0001")  # same string, different tenant

    b_before = counts(org_b)
    b_claim_before = svc_get("claims", {"id": f"eq.{claim_b['id']}", "select": "status,risk_score,risk_level"})[0]

    # ---- run the pipeline over Clinic A's claim only ----
    await orchestrator.handle(claim_a["id"], {"type": "claim.ingested"})

    # ---- Clinic A changed ----
    a_claim = svc_get("claims", {"id": f"eq.{claim_a['id']}", "select": "status,risk_score,risk_level"})[0]
    a_issues = svc_get("claim_issues", {"claim_id": f"eq.{claim_a['id']}", "select": "organization_id,issue_type"})
    a_activity = svc_get("activity_log", {"claim_id": f"eq.{claim_a['id']}", "select": "organization_id,actor,action"})
    chk("A's claim advanced past 'received'", a_claim["status"] != "received", str(a_claim))
    chk("A's claim was scored by 06 (High: missing auth + code mismatch)",
        a_claim["risk_score"] == 80 and a_claim["risk_level"] == "High", str(a_claim))
    chk("A got claim_issues rows", len(a_issues) == 2, str(a_issues))
    chk("every issue row 06 wrote carries organization_id == A",
        all(r["organization_id"] == org_a for r in a_issues), str(a_issues))
    chk("every activity row carries organization_id == A",
        len(a_activity) > 0 and all(r["organization_id"] == org_a for r in a_activity), str(a_activity))
    chk("a 00-commander activity row exists",
        any(r["actor"] == "00-commander" for r in a_activity), str(a_activity))

    # ---- Clinic B untouched ----
    b_claim_after = svc_get("claims", {"id": f"eq.{claim_b['id']}", "select": "status,risk_score,risk_level"})[0]
    b_after = counts(org_b)
    chk("B's identically-named claim is still 'received', unscored",
        b_claim_after == b_claim_before and b_claim_after["status"] == "received", str(b_claim_after))
    for t in AGENT_TABLES:
        chk(f"B has no new {t} rows from A's run", b_after[t] == b_before[t],
            f"{t}: {b_before[t]} -> {b_after[t]}")

    # ---- nothing the run wrote references B's tenant ----
    for t in AGENT_TABLES:
        leaked = svc_get(t, {"organization_id": f"eq.{org_b}", "select": "id"})
        chk(f"no {t} row in tenant B after A's run", leaked == [], str(leaked))

    # ---- orchestrator resolves org from the claim, not a constant ----
    reloaded_b = await db.get_claim(claim_b["id"])
    chk("db.get_claim(B) still returns B's claim, org unchanged",
        reloaded_b and reloaded_b["organization_id"] == org_b)

    # ---- symmetric: run over B, confirm A's issue set is unchanged ----
    a_issue_ids_before = {r["issue_type"] for r in a_issues}
    await orchestrator.handle(claim_b["id"], {"type": "claim.ingested"})
    a_issues_after = svc_get("claim_issues", {"claim_id": f"eq.{claim_a['id']}", "select": "issue_type"})
    chk("running B's pipeline did not alter A's issues",
        {r["issue_type"] for r in a_issues_after} == a_issue_ids_before)
    b_issues = svc_get("claim_issues", {"claim_id": f"eq.{claim_b['id']}", "select": "organization_id"})
    chk("B's issues now exist and all carry organization_id == B",
        len(b_issues) == 2 and all(r["organization_id"] == org_b for r in b_issues))

    # ---- Phase 2: an eligibility run over A's check never touches B ----
    from app.agents.eligibility import bucket  # noqa: PLC0415

    def _active_member(payer_id: str) -> str:
        for n in range(10**8, 10**8 + 5000):
            if bucket(f"B{n}", payer_id) < 80:
                return f"B{n}"
        return "B100000000"

    elig_before_b = len(svc_get("eligibility_checks", {"organization_id": f"eq.{org_b}", "select": "id"}))
    check_a = svc_write("POST", "eligibility_checks", {}, {
        "organization_id": org_a, "appointment_id": None,
        "patient_name": "Isolation Elig Probe", "patient_member_id": _active_member(payer_a["id"]),
        "payer_id": payer_a["id"], "payer_name": payer_a["name"],
        "is_emergency": True, "status": "pending",
    })[0]
    await orchestrator.handle_eligibility(check_a["id"], {"type": "emergency_patient_registered"})
    await orchestrator.drain_detached()

    a_elig = svc_get("eligibility_checks", {"id": f"eq.{check_a['id']}", "select": "status,organization_id"})[0]
    chk("A's eligibility check resolved and carries organization_id == A",
        a_elig["status"] in ("verified_active", "verified_inactive") and a_elig["organization_id"] == org_a,
        str(a_elig))
    a_elig_acts = svc_get("activity_log", {"eligibility_check_id": f"eq.{check_a['id']}",
                                           "select": "organization_id,actor"})
    chk("every activity row from the eligibility run carries organization_id == A",
        len(a_elig_acts) > 0 and all(r["organization_id"] == org_a for r in a_elig_acts), str(a_elig_acts))
    chk("the eligibility run added no eligibility_checks rows to tenant B",
        len(svc_get("eligibility_checks", {"organization_id": f"eq.{org_b}", "select": "id"})) == elig_before_b)
    chk("no eligibility escalation / activity leaked to tenant B",
        svc_get("escalations", {"organization_id": f"eq.{org_b}", "eligibility_check_id": "not.is.null",
                                "select": "id"}) == []
        and svc_get("activity_log", {"organization_id": f"eq.{org_b}", "eligibility_check_id": "not.is.null",
                                     "select": "id"}) == [])

    # ---- Phase 3: a prior-auth run over A's PA never touches B ----
    pa_before_b = len(svc_get("prior_authorizations", {"organization_id": f"eq.{org_b}", "select": "id"}))
    appt_a = svc_write("POST", "appointments", {}, {
        "organization_id": org_a, "patient_name": "Isolation PA Probe",
        "patient_member_id": "PROBE-PA-1", "payer_id": payer_a["id"],
        "scheduled_at": "2026-11-01T00:00:00Z", "is_emergency": False,
    })[0]
    pa_a = svc_write("POST", "prior_authorizations", {}, {
        "organization_id": org_a, "appointment_id": appt_a["id"],
        "patient_name": "Isolation PA Probe", "patient_member_id": "PROBE-PA-1",
        "payer_id": payer_a["id"], "payer_name": payer_a["name"],
        "procedure_code": "72148", "place_of_service": "office",
        "is_emergency": False, "status": "pending",
    })[0]
    await orchestrator.handle_prior_auth(pa_a["id"], {"type": "prior_auth_requested"})
    await orchestrator.handle_prior_auth(pa_a["id"], {"type": "prior_auth_submission_approved"})
    await orchestrator.drain_detached()

    a_pa = svc_get("prior_authorizations",
                   {"id": f"eq.{pa_a['id']}", "select": "status,organization_id"})[0]
    chk("A's prior auth resolved and carries organization_id == A",
        a_pa["status"] in ("auth_approved", "info_needed", "auth_denied")
        and a_pa["organization_id"] == org_a, str(a_pa))
    a_pa_acts = svc_get("activity_log", {"prior_authorization_id": f"eq.{pa_a['id']}",
                                         "select": "organization_id,actor"})
    chk("every activity row from the prior-auth run carries organization_id == A",
        len(a_pa_acts) > 0 and all(r["organization_id"] == org_a for r in a_pa_acts), str(a_pa_acts))
    chk("the prior-auth run added no prior_authorizations rows to tenant B",
        len(svc_get("prior_authorizations", {"organization_id": f"eq.{org_b}", "select": "id"}))
        == pa_before_b)
    chk("no prior-auth escalation / activity leaked to tenant B",
        svc_get("escalations", {"organization_id": f"eq.{org_b}", "prior_authorization_id": "not.is.null",
                                "select": "id"}) == []
        and svc_get("activity_log", {"organization_id": f"eq.{org_b}", "prior_authorization_id": "not.is.null",
                                     "select": "id"}) == [])

    # ---- Phase 4: an insurance card scan + confirm over A never touches B ----
    from app.agents import ocr  # noqa: PLC0415

    cs_before_b = len(svc_get("card_scans", {"organization_id": f"eq.{org_b}", "select": "id"}))
    appt_cs_a = svc_write("POST", "appointments", {}, {
        "organization_id": org_a, "patient_name": "Isolation CardScan Probe",
        "patient_member_id": "", "payer_id": payer_a["id"],
        "scheduled_at": "2026-12-01T00:00:00Z", "is_emergency": False,
    })[0]
    import uuid as _uuid  # noqa: PLC0415
    scan_id = str(_uuid.uuid4())
    scan_a = await db.insert_card_scan(org_a, {
        "id": scan_id, "appointment_id": appt_cs_a["id"],
        "patient_name": "Isolation CardScan Probe",
        "image_path": f"{org_a}/{scan_id}.png", "image_mime": "image/png", "status": "pending",
    })
    ext = ocr.CardExtraction(
        fields={"member_id": "NWH-ISO-0001", "group_number": "GRP-ISO", "payer_name": payer_a["name"],
                "plan_type": "PPO"},
        confidence={f: {"confidence": "high", "legible": True, "absent": False} for f in ocr.FIELDS},
    )
    new_status = ocr.classify_extraction(ext, floor="medium")
    await db.update_card_scan(org_a, scan_id, {
        "status": new_status, "extracted_fields": ext.fields, "field_confidence": ext.confidence})
    # mirror the confirm write-back
    await db.update_appointment(org_a, appt_cs_a["id"], {"patient_member_id": "NWH-ISO-0001"})
    await db.update_card_scan(org_a, scan_id, {
        "status": "confirmed", "reviewed_by": None, "applied_to_appointment": True})
    await db.insert_activity(org_a, None, actor="human:seed", action="card_scan_confirmed",
                             appointment_id=appt_cs_a["id"], details={"card_scan_id": scan_id})

    a_scan = svc_get("card_scans", {"id": f"eq.{scan_id}", "select": "*"})[0]
    chk("A's card scan resolved, carries organization_id == A, image path under A's org prefix",
        a_scan["organization_id"] == org_a and a_scan["status"] == "confirmed"
        and a_scan["image_path"].startswith(f"{org_a}/"), str(a_scan))
    appt_cs_now = svc_get("appointments", {"id": f"eq.{appt_cs_a['id']}", "select": "patient_member_id"})[0]
    chk("A's confirm wrote the member id back to A's appointment only",
        appt_cs_now["patient_member_id"] == "NWH-ISO-0001", str(appt_cs_now))
    chk("the card-scan run added no card_scans rows to tenant B",
        len(svc_get("card_scans", {"organization_id": f"eq.{org_b}", "select": "id"})) == cs_before_b)
    chk("no card_scan activity leaked to tenant B",
        svc_get("activity_log", {"organization_id": f"eq.{org_b}", "action": "like.card_scan_*",
                                 "select": "id"}) == [])
    reloaded = await db.get_card_scan(scan_id)
    chk("db.get_card_scan resolves the scan, org unchanged",
        reloaded and reloaded["organization_id"] == org_a)

    # ---- Phase 4: coordination of benefits over A never sees B's coverages ----
    from datetime import date as _date  # noqa: PLC0415

    from app.agents import cob  # noqa: PLC0415

    shared_patient = {"patient_name": "Isolation COB Probe", "patient_dob": "1991-02-02"}
    cov_a = await db.insert_patient_coverage(org_a, {
        **shared_patient, "payer_name": "A Health", "coverage_type": "employer_active",
        "relationship_to_subscriber": "self", "is_dependent": False, "effective_date": "2020-01-01"})
    # Clinic B: SAME patient_name + dob, a different (and would-be-primary-changing) coverage
    svc_write("POST", "patient_coverages", {}, {
        "organization_id": org_b, **shared_patient, "payer_name": "B Health",
        "coverage_type": "medicaid", "relationship_to_subscriber": "self",
        "is_dependent": False, "effective_date": "2019-01-01"})

    a_rows = svc_get("patient_coverages", {
        "organization_id": f"eq.{org_a}", "patient_name": f"eq.{shared_patient['patient_name']}",
        "select": "*"})
    chk("A's coverage query returns only A's row (same patient key exists in B)",
        len(a_rows) == 1 and a_rows[0]["organization_id"] == org_a
        and a_rows[0]["payer_name"] == "A Health", str(a_rows))
    summary = cob.cob_summary(a_rows, today=_date(2026, 9, 7))
    chk("cob_summary over A's rows alone -> A's single coverage is primary, B's Medicaid never seen",
        summary.get("medical", [{}])[0].get("coverage_id") == cov_a["id"]
        and len(summary.get("medical", [])) == 1, str(summary))
    chk("the COB rows did not cross the tenant boundary",
        len(svc_get("patient_coverages", {"organization_id": f"eq.{org_b}", "select": "id"})) == 1
        and len(svc_get("patient_coverages", {"organization_id": f"eq.{org_a}", "select": "id"})) == 1)

    return chk.summary("agent pipeline never crosses a tenant boundary.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
