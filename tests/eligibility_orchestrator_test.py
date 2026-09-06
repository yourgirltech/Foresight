#!/usr/bin/env python3
"""Orchestrator behaviour for eligibility — the care-safety mechanics (M6).

Proves, against the real local stack:
  * an emergency check whose agent RAISES ends as a recorded check_failed row
    with NO escalation — the exception is swallowed, care is untouched;
  * the same failure on a SCHEDULED check DOES produce an escalation (E6) — the
    contrast case;
  * the emergency dispatch does not await the agent: handle_eligibility returns
    before a deliberately-slow simulate() has finished;
  * every Commander decision taken during an emergency flow has next_status None
    and route_to != 12-escalation.

No ANTHROPIC_API_KEY needed. Run (local stack up):
    python tests/eligibility_orchestrator_test.py
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, eligibility, orchestrator  # noqa: E402
from app.agents.eligibility import EligibilityResult  # noqa: E402

NOW = datetime.now(timezone.utc)
_REAL_SIMULATE = eligibility.simulate


def payer(org_id, name, *, supported=True, threshold=85):
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": False, "documentation_required": False,
        "follow_up_threshold_days": None,
        "eligibility_verification_supported": supported,
        "eligibility_active_threshold": threshold,
    })[0]


def new_appointment(org_id, payer_id):
    return svc_write("POST", "appointments", {}, {
        "organization_id": org_id, "patient_name": "Orch Probe",
        "patient_member_id": "M123456789", "payer_id": payer_id,
        "scheduled_at": (NOW).isoformat(), "is_emergency": False,
    })[0]


def new_check(org_id, *, is_emergency, payer_id, member_id="M123456789"):
    """Emergency -> a bare check (no appointment, per Q2). Scheduled -> a real
    appointment row + a linked check, as the endpoint/seed would create."""
    appt_id = None
    if not is_emergency:
        appt_id = new_appointment(org_id, payer_id)["id"]
    return db_insert_check(org_id, {
        "appointment_id": appt_id, "patient_name": "Orch Probe",
        "patient_member_id": member_id, "payer_id": payer_id,
        "payer_name": "probe", "is_emergency": is_emergency, "status": "pending",
    })


def db_insert_check(org_id, fields):
    return svc_write("POST", "eligibility_checks", {}, {**fields, "organization_id": org_id})[0]


def check_row(pk):
    return svc_get("eligibility_checks", {"id": f"eq.{pk}", "select": "status,result_payload,organization_id"})[0]


def escalations_for(pk):
    return svc_get("escalations", {"eligibility_check_id": f"eq.{pk}", "select": "reason_code,organization_id"})


async def main() -> int:
    chk = Check()
    print("Eligibility orchestrator — care-safety mechanics\n")
    org_id, _ = ensure_org("eligorch_a@foresight.test", "EligOrch — Clinic A")
    svc_write("DELETE", "eligibility_checks", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "appointments", {"organization_id": f"eq.{org_id}"}, None)

    p = payer(org_id, "EligOrch Payer")

    # ---- 1. emergency + agent raises -> recorded check_failed, NO escalation ----
    def boom(patient, payer, *, now=None):
        raise RuntimeError("simulated clearinghouse blew up")

    eligibility.simulate = boom
    try:
        c_em = new_check(org_id, is_emergency=True, payer_id=p["id"])
        d = await orchestrator.handle_eligibility(
            c_em["id"], {"type": "emergency_patient_registered"}
        )
        chk("emergency registration returns a decision (fire-and-forget)",
            d.route_to == "01-eligibility" and d.next_status is None, str(d))
        await orchestrator.drain_detached()
        row = check_row(c_em["id"])
        chk("emergency check whose agent raised ends as check_failed",
            row["status"] == "check_failed", str(row))
        chk("emergency agent failure produced NO escalation row",
            escalations_for(c_em["id"]) == [], str(escalations_for(c_em["id"])))
        acts = svc_get("activity_log", {"eligibility_check_id": f"eq.{c_em['id']}",
                                        "select": "actor,action"})
        chk("emergency agent failure was logged (01-eligibility / error)",
            any(a["actor"] == "01-eligibility" and a["action"] == "error" for a in acts), str(acts))
        chk("no Commander decision for the emergency flow carried a next_status",
            all(a["action"] != "escalated" for a in acts)
            and not any(a["action"] == "eligibility_check_failed_scheduled" for a in acts), str(acts))

        # ---- 2. same failure, SCHEDULED -> escalation DOES appear (E6) ----
        c_sc = new_check(org_id, is_emergency=False, payer_id=p["id"])
        await orchestrator.handle_eligibility(c_sc["id"], {"type": "appointment_scheduled"})
        row = check_row(c_sc["id"])
        chk("scheduled check whose agent raised ends as check_failed",
            row["status"] == "check_failed", str(row))
        esc = escalations_for(c_sc["id"])
        chk("scheduled agent failure DID produce an escalation (contrast the emergency case)",
            len(esc) == 1 and esc[0]["reason_code"] == "eligibility_check_failed_scheduled", str(esc))
        chk("that escalation is scoped to the org", esc[0]["organization_id"] == org_id)
    finally:
        eligibility.simulate = _REAL_SIMULATE

    # ---- 3. emergency dispatch does not await the agent ----
    # simulate() is sync; a slow sync body proves the spawn is not run inline.
    def slow(patient, payer_, *, now=None):
        time.sleep(1.5)
        return EligibilityResult("verified_active", {"simulated": True, "slow": True})

    eligibility.simulate = slow
    try:
        c_slow = new_check(org_id, is_emergency=True, payer_id=p["id"])
        t0 = time.monotonic()
        await orchestrator.handle_eligibility(c_slow["id"], {"type": "emergency_patient_registered"})
        elapsed = time.monotonic() - t0
        chk("handle_eligibility returned WITHOUT waiting on the slow emergency agent",
            elapsed < 1.0, f"took {elapsed:.2f}s")
        chk("the slow emergency check is still pending immediately after",
            check_row(c_slow["id"])["status"] == "pending", str(check_row(c_slow["id"])))
        await orchestrator.drain_detached()
        chk("after draining, the slow emergency check resolved",
            check_row(c_slow["id"])["status"] == "verified_active")
        chk("slow emergency check still never escalated", escalations_for(c_slow["id"]) == [])
    finally:
        eligibility.simulate = _REAL_SIMULATE

    # ---- 4. the hard invariant guard actually fires on a bad decision ----
    real_decide = orchestrator.commander.decide

    class BadDecision:
        action = "route"; reason_code = "bogus"; route_to = "01-eligibility"; next_status = "escalated"

    orchestrator.commander.decide = lambda state, trigger: BadDecision()
    try:
        c_guard = new_check(org_id, is_emergency=True, payer_id=p["id"])
        raised = False
        try:
            await orchestrator.handle_eligibility(c_guard["id"], {"type": "emergency_patient_registered"})
        except RuntimeError as exc:
            raised = "next_status" in str(exc)
        chk("orchestrator HARD-raises if a Commander decision carries next_status", raised)
    finally:
        orchestrator.commander.decide = real_decide

    return chk.summary("eligibility orchestrator honours the care-safety mechanics.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
