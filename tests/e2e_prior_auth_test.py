#!/usr/bin/env python3
"""End-to-end: prior authorization, driven through the Commander.

Same pattern as e2e_eligibility_test.py — real orgs via signup/bootstrap, the
real orchestrator, every check printed. No ANTHROPIC_API_KEY needed.

Scenarios (docs/agents/02-prior-auth-agent.md §12):
  1  scheduled, always-auth procedure          -> A3 -> required_draft, A5 await_human, appt untouched
  2  scenario 1 + approve submission            -> A7 -> 02.submit -> approved -> auth_approved + auth number
  3  scheduled, tuned to a DENIED response      -> A10 -> auth_denied + one escalation, org-scoped
  4  scheduled, tuned to INFO_NEEDED + resubmit -> chain reads info_needed -> auth_approved
  5  scheduled, routine procedure               -> A6 -> not_required, NO escalation, NO draft
  6  EMERGENCY, always-auth procedure           -> A1 detached -> emergency_exempt, NO draft,
                                                   NO await_human, NO escalation, next_status None throughout
  7  scheduled, blank procedure code            -> A6 -> insufficient_info; resubmit with a code -> required_draft
  8  second org                                  -> nothing leaked across the tenant boundary
  9  collected                                   -> every emergency decision: next_status None,
                                                   action != await_human, route_to != 12-escalation

Run (local stack up):  python tests/e2e_prior_auth_test.py
"""
from __future__ import annotations

import asyncio

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, orchestrator  # noqa: E402
from app.agents.prior_auth import INFO_NEEDED_BAND, response_bucket  # noqa: E402


def payer(org_id, name, *, supported=True, required_default=True, threshold=80):
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": False, "documentation_required": False,
        "prior_auth_supported": supported, "prior_auth_required_default": required_default,
        "prior_auth_approval_threshold": threshold,
    })[0]


def member_for_band(payer_id, procedure, threshold, band) -> str:
    for n in range(400000):
        mid = f"E{n:07d}"
        b = response_bucket(mid, payer_id, procedure)
        if band == "approved" and b < threshold - 4:
            return mid
        if band == "info_needed" and threshold <= b < threshold + INFO_NEEDED_BAND:
            return mid
        if band == "denied" and b >= threshold + INFO_NEEDED_BAND + 3:
            return mid
    raise RuntimeError(f"no member id found for band {band}")


async def make_pa(org_id, payer_row, name, member_id, procedure, *,
                  emergency=False, place="office", appt=True):
    appt_id = None
    if appt and not emergency:
        a = await db.insert_appointment(org_id, {
            "patient_name": name, "patient_member_id": member_id,
            "payer_id": payer_row["id"] if payer_row else None,
            "scheduled_at": "2026-10-15T00:00:00Z", "is_emergency": False,
        })
        appt_id = a["id"]
    pa = await db.insert_prior_authorization(org_id, {
        "appointment_id": appt_id, "patient_name": name, "patient_member_id": member_id,
        "payer_id": payer_row["id"] if payer_row else None,
        "payer_name": payer_row["name"] if payer_row else None,
        "procedure_code": procedure, "procedure_description": "",
        "place_of_service": "emergency" if emergency else place,
        "is_emergency": emergency, "status": "pending",
    })
    trig = "prior_auth_emergency" if emergency else "prior_auth_requested"
    d = await orchestrator.handle_prior_auth(pa["id"], {"type": trig})
    await orchestrator.drain_detached()
    return pa, appt_id, d


async def approve(pa_id):
    d = await orchestrator.handle_prior_auth(pa_id, {"type": "prior_auth_submission_approved"})
    await orchestrator.drain_detached()
    return d


async def resubmit(org_id, prior_id, *, procedure=None):
    prior = svc_get("prior_authorizations", {"id": f"eq.{prior_id}", "select": "*"})[0]
    new = await db.insert_prior_authorization(org_id, {
        "appointment_id": prior.get("appointment_id"), "previous_auth_id": prior_id,
        "patient_name": prior["patient_name"], "patient_member_id": prior.get("patient_member_id", ""),
        "payer_id": prior.get("payer_id"), "payer_name": prior.get("payer_name"),
        "procedure_code": procedure or prior.get("procedure_code", ""),
        "procedure_description": "", "place_of_service": prior.get("place_of_service", "office"),
        "is_emergency": prior.get("is_emergency", False), "status": "pending",
    })
    d = await orchestrator.handle_prior_auth(new["id"], {"type": "prior_auth_requested"})
    await orchestrator.drain_detached()
    return new, d


def prow(pk):
    return svc_get("prior_authorizations",
                   {"id": f"eq.{pk}", "select": "*"})[0]


def esc_for(pk):
    return svc_get("escalations",
                   {"prior_authorization_id": f"eq.{pk}", "select": "reason_code,organization_id"})


def acts_for(pk):
    return svc_get("activity_log",
                   {"prior_authorization_id": f"eq.{pk}",
                    "select": "actor,action,details,organization_id", "order": "created_at"})


async def main() -> int:
    chk = Check()
    print("End-to-end — prior authorization\n")

    org_id, _ = ensure_org("e2e_pa@foresight.test", "E2E PA — Northgate")
    other_org, _ = ensure_org("e2e_pa_other@foresight.test", "E2E PA — Other")

    p = payer(org_id, "E2E PA Payer", supported=True, required_default=True, threshold=75)
    emergency_decisions = []

    # ---- 1. scheduled, always-auth procedure -> required_draft -> await_human ----
    m_ok = member_for_band(p["id"], "72148", 75, "approved")
    pa1, appt1, d1 = await make_pa(org_id, p, "Sched AlwaysAuth", m_ok, "72148")
    r1 = prow(pa1["id"])
    chk("1 scheduled: Commander routed A3 (determine), next_status None",
        d1.reason_code == "prior_auth_determine" and d1.next_status is None, str(d1))
    chk("1 scheduled: determination = required_draft", r1["status"] == "required_draft", str(r1))
    chk("1 scheduled: a request packet was drafted", bool(r1["request_payload"]), str(r1["request_payload"]))
    a1 = [a["action"] for a in acts_for(pa1["id"])]
    chk("1 scheduled: Commander parked at await_human (prior_auth_awaiting_submission_approval)",
        "prior_auth_awaiting_submission_approval" in a1, str(a1))
    appt1_now = svc_get("appointments", {"id": f"eq.{appt1}", "select": "*"})[0]
    chk("1 scheduled: the appointment row is unchanged / has no 'blocked' state",
        appt1_now["is_emergency"] is False and "status" not in appt1_now, str(appt1_now))

    # ---- 2. approve submission -> submit -> approved -----------------------------
    d2 = await approve(pa1["id"])
    r2 = prow(pa1["id"])
    chk("2 approve: A7 routed to 02-prior-auth (submit), next_status None",
        d2.reason_code == "prior_auth_submit" and d2.next_status is None, str(d2))
    chk("2 approve: PA resolved auth_approved with an authorization_number",
        r2["status"] == "auth_approved" and r2["authorization_number"], str(r2))
    a2 = [a["actor"] for a in acts_for(pa1["id"])]
    chk("2 approve: the submit actor (02-prior-auth:submit) appears in the audit trail",
        "02-prior-auth:submit" in a2, str(a2))
    chk("2 approve: every activity row is org-scoped",
        all(a["organization_id"] == org_id for a in acts_for(pa1["id"])))

    # ---- 3. scheduled, tuned to DENIED -> escalation ---------------------------
    m_denied = member_for_band(p["id"], "29881", 75, "denied")
    pa3, _, _ = await make_pa(org_id, p, "Sched Denied", m_denied, "29881")
    await approve(pa3["id"])
    r3 = prow(pa3["id"])
    chk("3 denied: PA resolved auth_denied", r3["status"] == "auth_denied", str(r3))
    e3 = esc_for(pa3["id"])
    chk("3 denied: ONE escalation, prior_auth_needs_human, org-scoped",
        len(e3) == 1 and e3[0]["reason_code"] == "prior_auth_needs_human"
        and e3[0]["organization_id"] == org_id, str(e3))

    # ---- 4. scheduled, tuned to INFO_NEEDED -> resubmit -> approved -----------
    m_info = member_for_band(p["id"], "62323", 75, "info_needed")
    pa4, _, _ = await make_pa(org_id, p, "Sched InfoNeeded", m_info, "62323")
    await approve(pa4["id"])
    r4 = prow(pa4["id"])
    chk("4 info_needed: first attempt resolved info_needed", r4["status"] == "info_needed", str(r4))
    pa4b, _ = await resubmit(org_id, pa4["id"])
    await approve(pa4b["id"])
    r4b = prow(pa4b["id"])
    chk("4 resubmit: the chained row resolved auth_approved (the resubmit approval bonus)",
        r4b["status"] == "auth_approved", str(r4b))
    chk("4 resubmit: previous_auth_id links the chain",
        r4b["previous_auth_id"] == pa4["id"], str(r4b["previous_auth_id"]))

    # ---- 5. scheduled, routine procedure -> not_required ---------------------
    pa5, _, _ = await make_pa(org_id, p, "Sched Routine", "E9999999", "99213")
    r5 = prow(pa5["id"])
    chk("5 routine: determination = not_required", r5["status"] == "not_required", str(r5))
    chk("5 routine: NO draft, NO escalation",
        not r5["request_payload"] and esc_for(pa5["id"]) == [], str(r5))

    # ---- 6. EMERGENCY, always-auth procedure -> emergency_exempt -------------
    pa6, appt6, d6 = await make_pa(org_id, p, "ER AlwaysAuth", "E1234567", "70553",
                                   emergency=True, place="emergency", appt=False)
    emergency_decisions.append(d6)
    r6 = prow(pa6["id"])
    chk("6 emergency: Commander routed A1 (emergency determine), route_to 02, next_status None",
        d6.reason_code == "prior_auth_emergency_determine"
        and d6.route_to == "02-prior-auth" and d6.next_status is None, str(d6))
    chk("6 emergency: determination = emergency_exempt", r6["status"] == "emergency_exempt", str(r6))
    chk("6 emergency: NO request packet drafted", not r6["request_payload"], str(r6["request_payload"]))
    chk("6 emergency: NO escalation row", esc_for(pa6["id"]) == [], str(esc_for(pa6["id"])))
    a6 = [a["action"] for a in acts_for(pa6["id"])]
    chk("6 emergency: never parked at await_human, never escalated",
        "prior_auth_awaiting_submission_approval" not in a6
        and "prior_auth_needs_human" not in a6, str(a6))
    for a in acts_for(pa6["id"]):
        if a["actor"] == "00-commander":
            chk(f"6 emergency: Commander activity '{a['action']}' recorded is_emergency=true",
                a["details"].get("is_emergency") is True, str(a["details"]))

    # ---- 7. scheduled, blank procedure -> insufficient_info -> resubmit w/ code ----
    pa7, _, _ = await make_pa(org_id, p, "Sched NoCode", "E4242424", "")
    r7 = prow(pa7["id"])
    chk("7 no code: determination = insufficient_info", r7["status"] == "insufficient_info", str(r7))
    chk("7 no code: recheck_recommended flagged",
        r7["determination_payload"].get("recheck_recommended") is True, str(r7["determination_payload"]))
    pa7b, _ = await resubmit(org_id, pa7["id"], procedure="72148")
    r7b = prow(pa7b["id"])
    chk("7 resubmit with a code: determination = required_draft", r7b["status"] == "required_draft", str(r7b))

    # ---- 8. tenant isolation -------------------------------------------------
    chk("8 isolation: org B has no prior_authorizations after all of org A's runs",
        svc_get("prior_authorizations", {"organization_id": f"eq.{other_org}", "select": "id"}) == [])
    chk("8 isolation: org B has no PA escalations / activity",
        svc_get("escalations", {"organization_id": f"eq.{other_org}",
                                "prior_authorization_id": "not.is.null", "select": "id"}) == []
        and svc_get("activity_log", {"organization_id": f"eq.{other_org}",
                                     "prior_authorization_id": "not.is.null", "select": "id"}) == [])
    p_o = payer(other_org, "Other PA Payer", threshold=75)
    pa_o, _, _ = await make_pa(other_org, p_o, "Other Patient",
                               member_for_band(p_o["id"], "72148", 75, "approved"), "72148")
    chk("8 isolation: a run in org B writes only org B rows; org A's PA rows are all org A's",
        prow(pa_o["id"])["organization_id"] == other_org
        and all(r["organization_id"] == org_id
                for r in svc_get("prior_authorizations",
                                 {"organization_id": f"eq.{org_id}", "select": "organization_id"})))

    # ---- 9. the emergency invariant, collected -----------------------------
    chk("9 invariant: every emergency Commander decision had next_status None",
        all(d.next_status is None for d in emergency_decisions),
        str([(d.reason_code, d.next_status) for d in emergency_decisions]))
    chk("9 invariant: no emergency Commander decision was await_human or routed to 12-escalation",
        all(d.action != "await_human" and d.route_to != "12-escalation" for d in emergency_decisions),
        str([(d.reason_code, d.action, d.route_to) for d in emergency_decisions]))

    return chk.summary("prior authorization holds end to end; emergency care is never gated.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
