#!/usr/bin/env python3
"""Orchestrator behaviour for prior auth — the care-safety mechanics + the gate.

Proves, against the real local stack:
  * an emergency determination whose agent RAISES ends as a recorded row with NO
    escalation and NO await_human — the exception is swallowed, care is
    untouched;
  * a SCHEDULED denied / info_needed response DOES produce an escalation (A10) —
    the contrast case;
  * the emergency dispatch does not await the agent: handle_prior_auth returns
    before a deliberately-slow determine() has finished;
  * THE HUMAN-APPROVAL GATE: a scheduled PA that reaches required_draft never
    runs 02.submit until the prior_auth_submission_approved trigger arrives —
    re-firing prior_auth_determined just re-parks at await_human;
  * the orchestrator HARD-raises if a Commander decision (a) carries a
    next_status, (b) routes an emergency to 12-escalation, or (c) parks an
    emergency at await_human.

No ANTHROPIC_API_KEY needed. Run (local stack up):
    python tests/prior_auth_orchestrator_test.py
"""
from __future__ import annotations

import asyncio
import time

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, orchestrator, prior_auth  # noqa: E402
from app.agents.prior_auth import Determination, PayerResponse  # noqa: E402

_REAL_DETERMINE = prior_auth.determine
_REAL_RESPONSE = prior_auth.simulate_response


def payer(org_id, name, *, supported=True, required_default=True, threshold=80):
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": False, "documentation_required": False,
        "prior_auth_supported": supported, "prior_auth_required_default": required_default,
        "prior_auth_approval_threshold": threshold,
    })[0]


def new_appt(org_id, payer_id, *, emergency=False):
    return svc_write("POST", "appointments", {}, {
        "organization_id": org_id, "patient_name": "PA Probe",
        "patient_member_id": "M123456789", "payer_id": payer_id,
        "scheduled_at": None if emergency else "2026-10-01T00:00:00Z",
        "is_emergency": emergency,
    })[0]


def new_pa(org_id, *, emergency, payer_id, procedure="72148", place="office", appt=True):
    appt_id = None
    if appt and not emergency:
        appt_id = new_appt(org_id, payer_id)["id"]
    return svc_write("POST", "prior_authorizations", {}, {
        "organization_id": org_id, "appointment_id": appt_id,
        "patient_name": "PA Probe", "patient_member_id": "M123456789",
        "payer_id": payer_id, "payer_name": "probe",
        "procedure_code": procedure,
        "place_of_service": "emergency" if emergency else place,
        "is_emergency": emergency, "status": "pending",
    })[0]


def pa_row(pk):
    return svc_get("prior_authorizations",
                   {"id": f"eq.{pk}", "select": "status,response_payload,organization_id"})[0]


def escalations_for(pk):
    return svc_get("escalations",
                   {"prior_authorization_id": f"eq.{pk}", "select": "reason_code,organization_id"})


def activity_for(pk):
    return svc_get("activity_log",
                   {"prior_authorization_id": f"eq.{pk}", "select": "actor,action"})


async def main() -> int:
    chk = Check()
    print("Prior-auth orchestrator — care-safety mechanics + the human-approval gate\n")
    org_id, _ = ensure_org("paorch_a@foresight.test", "PAOrch — Clinic A")
    p = payer(org_id, "PAOrch Payer")

    # ---- 1. emergency + determine raises -> recorded, NO escalation, NO await ----
    def boom(encounter, payer_, *, now=None):
        raise RuntimeError("simulated payer rules engine blew up")

    prior_auth.determine = boom
    try:
        pa_em = new_pa(org_id, emergency=True, payer_id=p["id"], place="emergency")
        d = await orchestrator.handle_prior_auth(pa_em["id"], {"type": "prior_auth_emergency"})
        chk("emergency registration returns a decision (fire-and-forget)",
            d.route_to == "02-prior-auth" and d.next_status is None, str(d))
        await orchestrator.drain_detached()
        row = pa_row(pa_em["id"])
        chk("emergency PA whose agent raised ends recorded (insufficient_info)",
            row["status"] == "insufficient_info", str(row))
        chk("emergency agent failure produced NO escalation row",
            escalations_for(pa_em["id"]) == [], str(escalations_for(pa_em["id"])))
        acts = activity_for(pa_em["id"])
        chk("emergency agent failure was logged (02-prior-auth / error)",
            any(a["actor"] == "02-prior-auth" and a["action"] == "error" for a in acts), str(acts))
        chk("no Commander decision in the emergency flow was an escalation or await_human",
            not any(a["action"] in ("prior_auth_needs_human", "prior_auth_unrecognized_state",
                                    "prior_auth_awaiting_submission_approval") for a in acts),
            str(acts))
    finally:
        prior_auth.determine = _REAL_DETERMINE

    # ---- 2. scheduled denied response -> escalation DOES appear (A10) ----
    def always_denied(encounter, payer_, patient, *, is_resubmit=False, now=None):
        return PayerResponse("denied", {"simulated": True, "response_status": "denied",
                                        "bucket": 99, "threshold": 80}, None)

    prior_auth.simulate_response = always_denied
    try:
        pa_sc = new_pa(org_id, emergency=False, payer_id=p["id"], procedure="72148")
        await orchestrator.handle_prior_auth(pa_sc["id"], {"type": "prior_auth_requested"})
        chk("scheduled PA for an always-auth procedure reached required_draft",
            pa_row(pa_sc["id"])["status"] == "required_draft", str(pa_row(pa_sc["id"])))
        # a human approves -> submit -> denied -> escalation
        await orchestrator.handle_prior_auth(pa_sc["id"], {"type": "prior_auth_submission_approved"})
        row = pa_row(pa_sc["id"])
        chk("after approval + denied response, the PA row is auth_denied",
            row["status"] == "auth_denied", str(row))
        esc = escalations_for(pa_sc["id"])
        chk("scheduled denied DID produce an escalation (contrast the emergency case)",
            len(esc) == 1 and esc[0]["reason_code"] == "prior_auth_needs_human", str(esc))
        chk("that escalation is scoped to the org", esc[0]["organization_id"] == org_id)
    finally:
        prior_auth.simulate_response = _REAL_RESPONSE

    # ---- 3. THE HUMAN-APPROVAL GATE ----
    pa_gate = new_pa(org_id, emergency=False, payer_id=p["id"], procedure="72148")
    await orchestrator.handle_prior_auth(pa_gate["id"], {"type": "prior_auth_requested"})
    chk("gate: PA parked at required_draft after determination",
        pa_row(pa_gate["id"])["status"] == "required_draft")
    # re-fire the determination trigger — must NOT submit, just re-park
    await orchestrator.handle_prior_auth(pa_gate["id"], {"type": "prior_auth_determined"})
    row = pa_row(pa_gate["id"])
    chk("gate: re-firing prior_auth_determined does NOT run 02.submit "
        "(still required_draft, no response_payload)",
        row["status"] == "required_draft" and not row["response_payload"], str(row))
    acts = activity_for(pa_gate["id"])
    chk("gate: no 02-prior-auth:submit actor appears until approval",
        not any(a["actor"] == "02-prior-auth:submit" for a in acts), str(acts))
    # now the real approval trigger
    await orchestrator.handle_prior_auth(pa_gate["id"], {"type": "prior_auth_submission_approved"})
    row = pa_row(pa_gate["id"])
    chk("gate: only prior_auth_submission_approved gets past the gate to a payer response",
        row["status"] in ("auth_approved", "info_needed", "auth_denied")
        and bool(row["response_payload"]), str(row))

    # ---- 4. emergency dispatch does not await the agent ----
    def slow(encounter, payer_, *, now=None):
        time.sleep(1.5)
        return Determination("emergency_exempt", False, {"simulated": True, "slow": True})

    prior_auth.determine = slow
    try:
        pa_slow = new_pa(org_id, emergency=True, payer_id=p["id"], place="emergency")
        t0 = time.monotonic()
        await orchestrator.handle_prior_auth(pa_slow["id"], {"type": "prior_auth_emergency"})
        elapsed = time.monotonic() - t0
        chk("handle_prior_auth returned WITHOUT waiting on the slow emergency agent",
            elapsed < 1.0, f"took {elapsed:.2f}s")
        chk("the slow emergency PA is still pending immediately after",
            pa_row(pa_slow["id"])["status"] == "pending", str(pa_row(pa_slow["id"])))
        await orchestrator.drain_detached()
        chk("after draining, the slow emergency PA resolved to emergency_exempt",
            pa_row(pa_slow["id"])["status"] == "emergency_exempt")
        chk("slow emergency PA still never escalated", escalations_for(pa_slow["id"]) == [])
    finally:
        prior_auth.determine = _REAL_DETERMINE

    # ---- 5. the hard invariant guards actually fire ----
    real_decide = orchestrator.commander.decide

    def bad(action, route_to, next_status):
        class _D:
            pass
        d = _D()
        d.action, d.reason_code, d.route_to, d.next_status = action, "bogus", route_to, next_status
        return d

    for label, decision, needle in (
        ("carries a next_status", bad("route", "02-prior-auth", "auth_approved"), "next_status"),
        ("routes an emergency to 12-escalation", bad("route", "12-escalation", None), "12-escalation"),
        ("parks an emergency at await_human", bad("await_human", None, None), "await_human"),
    ):
        orchestrator.commander.decide = lambda s, t, _d=decision: _d
        try:
            pa_guard = new_pa(org_id, emergency=True, payer_id=p["id"], place="emergency")
            raised = False
            try:
                await orchestrator.handle_prior_auth(pa_guard["id"], {"type": "prior_auth_emergency"})
            except RuntimeError as exc:
                raised = needle in str(exc)
            chk(f"orchestrator HARD-raises if a decision {label}", raised)
        finally:
            orchestrator.commander.decide = real_decide

    return chk.summary("prior-auth orchestrator honours the care-safety mechanics + the gate.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
