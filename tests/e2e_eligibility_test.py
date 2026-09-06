#!/usr/bin/env python3
"""End-to-end: appointments + eligibility, driven through the Commander.

Same pattern as e2e_claim_test.py — real orgs via signup/bootstrap, the real
orchestrator, every check printed (not summarised). No ANTHROPIC_API_KEY needed.

Scenarios (docs/agents/01-eligibility-agent.md §12):
  1  scheduled, supported payer, active member  -> E3 -> verified_active, appt untouched
  2  scheduled, off-network payer               -> E6 -> check_failed + escalation
  3  emergency, no member id                     -> E1 fire-and-forget -> insufficient_info,
                                                    NO escalation, next_status None throughout
  4  scenario 3 + member id added (re-check)     -> new row -> verified_active; history chains
  5  emergency, off-network payer                -> E4 -> check_failed, NO escalation (contrast 2)
  6  second org                                   -> nothing leaked across the tenant boundary
  7  collected                                    -> every emergency decision had next_status None
                                                    and route_to != 12-escalation

Run (local stack up):  python tests/e2e_eligibility_test.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, orchestrator  # noqa: E402
from app.agents.eligibility import bucket  # noqa: E402

NOW = datetime.now(timezone.utc)


def payer(org_id, name, *, supported, threshold):
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": False, "documentation_required": False,
        "follow_up_threshold_days": None,
        "eligibility_verification_supported": supported,
        "eligibility_active_threshold": threshold,
    })[0]


def member_bucketing_below(payer_id: str, ceiling: int) -> str:
    for n in range(10**8, 10**8 + 5000):
        mid = f"B{n}"
        if bucket(mid, payer_id) < ceiling:
            return mid
    raise RuntimeError("no member id found")


async def sched_appointment(org_id, payer_row, name, member_id):
    appt = await db.insert_appointment(org_id, {
        "patient_name": name, "patient_member_id": member_id, "payer_id": payer_row["id"],
        "scheduled_at": (NOW + timedelta(days=7)).isoformat(), "is_emergency": False,
    })
    check = await db.insert_eligibility_check(org_id, {
        "appointment_id": appt["id"], "patient_name": name, "patient_member_id": member_id,
        "payer_id": payer_row["id"], "payer_name": payer_row["name"],
        "is_emergency": False, "status": "pending",
    })
    d = await orchestrator.handle_eligibility(check["id"], {"type": "appointment_scheduled"})
    return appt, check, d


async def emergency_registration(org_id, name, member_id, payer_row):
    check = await db.insert_eligibility_check(org_id, {
        "appointment_id": None, "patient_name": name, "patient_member_id": member_id,
        "payer_id": payer_row["id"] if payer_row else None,
        "payer_name": payer_row["name"] if payer_row else None,
        "is_emergency": True, "status": "pending",
    })
    d = await orchestrator.handle_eligibility(check["id"], {"type": "emergency_patient_registered"})
    await orchestrator.drain_detached()
    return check, d


async def recheck(org_id, prior_id, *, member_id, emergency):
    prior = svc_get("eligibility_checks", {"id": f"eq.{prior_id}", "select": "*"})[0]
    new = await db.insert_eligibility_check(org_id, {
        "appointment_id": prior.get("appointment_id"), "previous_check_id": prior_id,
        "patient_name": prior["patient_name"], "patient_member_id": member_id,
        "payer_id": prior.get("payer_id"), "payer_name": prior.get("payer_name"),
        "is_emergency": prior.get("is_emergency", False), "status": "pending",
    })
    trig = "emergency_patient_registered" if emergency else "appointment_scheduled"
    d = await orchestrator.handle_eligibility(new["id"], {"type": trig})
    await orchestrator.drain_detached()
    return new, d


def crow(pk):
    return svc_get("eligibility_checks", {"id": f"eq.{pk}", "select": "status,is_emergency,organization_id,result_payload"})[0]


def esc_for(pk):
    return svc_get("escalations", {"eligibility_check_id": f"eq.{pk}", "select": "reason_code,organization_id"})


def acts_for_check(pk):
    return svc_get("activity_log", {"eligibility_check_id": f"eq.{pk}",
                                    "select": "actor,action,details,organization_id", "order": "created_at"})


async def main() -> int:
    chk = Check()
    print("End-to-end — appointments + eligibility verification\n")

    org_id, _ = ensure_org("e2e_elig@foresight.test", "E2E Elig — Northgate")
    other_org, _ = ensure_org("e2e_elig_other@foresight.test", "E2E Elig — Other")

    p_ok = payer(org_id, "E2E Meridian", supported=True, threshold=95)
    p_off = payer(org_id, "E2E OffNetwork", supported=False, threshold=0)

    emergency_decisions = []

    # ---- 1. scheduled, active ------------------------------------------------
    active_member = member_bucketing_below(p_ok["id"], 95)
    appt1, c1, d1 = await sched_appointment(org_id, p_ok, "Scheduled Active", active_member)
    r1 = crow(c1["id"])
    chk("1 scheduled: Commander routed E3 (ahead of time), next_status None",
        d1.reason_code == "eligibility_scheduled_ahead_of_time" and d1.next_status is None, str(d1))
    chk("1 scheduled: check resolved verified_active", r1["status"] == "verified_active", str(r1))
    appt1_now = svc_get("appointments", {"id": f"eq.{appt1['id']}", "select": "*"})[0]
    chk("1 scheduled: the appointment row is unchanged / has no 'blocked' state",
        appt1_now["is_emergency"] is False and "status" not in appt1_now, str(appt1_now))
    a1 = [a["action"] for a in acts_for_check(c1["id"])]
    chk("1 scheduled: activity chain has the Commander decision + 01 verifying",
        "eligibility_scheduled_ahead_of_time" in a1 and "verified" in a1, str(a1))
    chk("1 scheduled: every activity row is org-scoped",
        all(a["organization_id"] == org_id for a in acts_for_check(c1["id"])))

    # ---- 2. scheduled, off-network -> E6 escalation ------------------------------
    appt2, c2, _ = await sched_appointment(org_id, p_off, "Scheduled OffNet", "B100000001")
    r2 = crow(c2["id"])
    chk("2 scheduled off-network: check resolved check_failed", r2["status"] == "check_failed", str(r2))
    e2 = esc_for(c2["id"])
    chk("2 scheduled off-network: ONE escalation, eligibility_check_failed_scheduled, org-scoped",
        len(e2) == 1 and e2[0]["reason_code"] == "eligibility_check_failed_scheduled"
        and e2[0]["organization_id"] == org_id, str(e2))

    # ---- 3. emergency, unidentified -> E1 fire-and-forget -> insufficient_info ----
    c3, d3 = await emergency_registration(org_id, "Unidentified Trauma", "", p_ok)
    emergency_decisions.append(d3)
    r3 = crow(c3["id"])
    chk("3 emergency: Commander routed E1 (fire-and-forget) -> 01, next_status None",
        d3.reason_code == "eligibility_emergency_fire_and_forget"
        and d3.route_to == "01-eligibility" and d3.next_status is None, str(d3))
    chk("3 emergency: check resolved insufficient_info", r3["status"] == "insufficient_info", str(r3))
    chk("3 emergency: result flags recheck_recommended",
        r3["result_payload"].get("recheck_recommended") is True, str(r3["result_payload"]))
    chk("3 emergency: NO escalation row (insufficient_info is not an error here)",
        esc_for(c3["id"]) == [], str(esc_for(c3["id"])))
    chk("3 emergency: NO appointment row was created for the registration",
        svc_get("appointments", {"organization_id": f"eq.{org_id}",
                                 "patient_name": "eq.Unidentified Trauma", "select": "id"}) == [])
    a3 = acts_for_check(c3["id"])
    for a in a3:
        if a["actor"] == "00-commander":
            chk(f"3 emergency: Commander activity '{a['action']}' recorded is_emergency=true",
                a["details"].get("is_emergency") is True, str(a["details"]))

    # ---- 4. re-check once a member id is on file -> verified_active ------------
    good_member = member_bucketing_below(p_ok["id"], 95)
    c4, d4 = await recheck(org_id, c3["id"], member_id=good_member, emergency=True)
    emergency_decisions.append(d4)
    r4 = crow(c4["id"])
    chk("4 re-check: new check row resolved verified_active", r4["status"] == "verified_active", str(r4))
    chk("4 re-check: still fire-and-forget, next_status None, not escalated",
        d4.next_status is None and d4.route_to != "12-escalation" and esc_for(c4["id"]) == [], str(d4))
    chain = svc_get("eligibility_checks",
                    {"or": f"(id.eq.{c3['id']},previous_check_id.eq.{c3['id']})",
                     "select": "status", "order": "created_at"})
    chk("4 re-check: history chains insufficient_info -> verified_active",
        [c["status"] for c in chain] == ["insufficient_info", "verified_active"], str(chain))

    # ---- 5. emergency + off-network payer -> check_failed, NO escalation --------
    c5, d5 = await emergency_registration(org_id, "ER OffNet Patient", "B100000002", p_off)
    emergency_decisions.append(d5)
    r5 = crow(c5["id"])
    chk("5 emergency off-network: check resolved check_failed", r5["status"] == "check_failed", str(r5))
    chk("5 emergency off-network: NO escalation (contrast scenario 2 — emergency check_failed != error)",
        esc_for(c5["id"]) == [], str(esc_for(c5["id"])))

    # ---- 6. tenant isolation --------------------------------------------------
    # org B was wiped by ensure_org and has had nothing run against it. After all
    # of org A's scenarios above, org B must still be completely empty.
    for tbl in ("eligibility_checks", "appointments"):
        chk(f"6 isolation: org B has no {tbl} rows after all of org A's runs",
            svc_get(tbl, {"organization_id": f"eq.{other_org}", "select": "id"}) == [])
    chk("6 isolation: org B has no eligibility escalations / activity",
        svc_get("escalations", {"organization_id": f"eq.{other_org}",
                                "eligibility_check_id": "not.is.null", "select": "id"}) == []
        and svc_get("activity_log", {"organization_id": f"eq.{other_org}",
                                     "eligibility_check_id": "not.is.null", "select": "id"}) == [])
    p_o = payer(other_org, "Other Meridian", supported=True, threshold=95)
    _, c_o, _ = await sched_appointment(other_org, p_o, "Other Patient",
                                        member_bucketing_below(p_o["id"], 95))
    chk("6 isolation: a run in org B writes only org B rows, and org A is unchanged",
        crow(c_o["id"])["organization_id"] == other_org
        and svc_get("eligibility_checks", {"organization_id": f"eq.{other_org}",
                                           "patient_name": "eq.Unidentified Trauma", "select": "id"}) == []
        and all(r["organization_id"] == org_id
                for r in svc_get("eligibility_checks", {"organization_id": f"eq.{org_id}",
                                                        "select": "organization_id"})))

    # ---- 7. the emergency invariant, collected --------------------------------
    chk("7 invariant: every emergency Commander decision had next_status None",
        all(d.next_status is None for d in emergency_decisions),
        str([(d.reason_code, d.next_status) for d in emergency_decisions]))
    chk("7 invariant: no emergency Commander decision routed to 12-escalation",
        all(d.route_to != "12-escalation" for d in emergency_decisions),
        str([(d.reason_code, d.route_to) for d in emergency_decisions]))

    return chk.summary("appointments + eligibility hold end to end; emergency care is never gated.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
