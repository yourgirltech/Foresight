"""The orchestrator — the thin driver around the Commander.

The Commander decides; the orchestrator does everything else: load the state,
log the decision, write the next status, run the routed agent, and re-invoke the
Commander with the natural follow-on trigger. That loop walks a fresh claim from
`received` to `awaiting_approval` in one pass, then stops for a human.

Tenancy: `organization_id` is resolved ONCE, from the triggering claim row, and
threaded into every db call (docs/architecture.md §3, 00-commander.md §8).

Loop-safety: a hard cap of MAX_INVOCATIONS Commander calls per originating
trigger. The rule table should make it unreachable; if it is ever hit, the
orchestrator forces an escalation rather than looping (00-commander.md §7.1).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..config import get_settings
from . import (
    appeals,
    commander,
    db,
    eligibility,
    escalation,
    executors,
    prior_auth,
    reasoning,
    recommendation,
    rules,
)
from .commander import CommanderDecision

MAX_INVOCATIONS = 12

# Detached tasks (the emergency eligibility fire-and-forget path). Held in a set
# so they are not garbage-collected mid-flight; discarded on completion.
_detached_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _detached_tasks.add(task)
    task.add_done_callback(_detached_tasks.discard)
    return task


async def drain_detached() -> None:
    """Wait for every outstanding detached eligibility task to finish. For the
    seed script and tests — production callers never wait on these (that is the
    whole point of the fire-and-forget path)."""
    while _detached_tasks:
        await asyncio.gather(*list(_detached_tasks), return_exceptions=True)


async def _load_state(claim: dict) -> dict:
    org_id = claim["organization_id"]
    claim_pk = claim["id"]
    payer = await db.get_payer(org_id, claim["payer_id"])
    issues = await db.list_issues(org_id, claim_pk)
    rec = await db.latest_recommendation(org_id, claim_pk)
    return {"claim": claim, "payer": payer or {}, "issues": issues, "recommendation": rec}


async def _run_agent(
    target: str,
    org_id: str,
    claim: dict,
    state: dict,
    decision: CommanderDecision,
    status_before: str,
) -> dict | None:
    """Run one specialist agent. Returns the follow-on trigger, or None if the
    run should stop here (await_human / manual handoff / terminal escalation)."""
    claim_pk = claim["id"]

    if target == "06-analyzer":
        result = rules.analyze(claim, state["payer"])
        await db.replace_issues(
            org_id,
            claim_pk,
            [
                {
                    "issue_type": i.issue_type,
                    "severity": i.severity,
                    "description": i.description,
                    "evidence": i.evidence,
                }
                for i in result.issues
            ],
        )
        await db.update_claim(
            org_id, claim_pk,
            {"risk_score": result.risk_score, "risk_level": result.risk_level},
        )
        await db.insert_activity(
            org_id, claim_pk, actor="06-analyzer", action="analyzed",
            details={
                "issue_count": len(result.issues),
                "issue_types": [i.issue_type for i in result.issues],
                "risk_score": result.risk_score,
                "risk_level": result.risk_level,
            },
        )
        return {"type": "analysis.completed"}

    if target == "07-reasoning":
        result = await reasoning.explain(claim, state["payer"], state["issues"])
        await db.update_claim(
            org_id, claim_pk,
            {
                "reasoning_summary": result.summary,
                "reasoning_detail": result.detail,
                "reasoning_generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await db.insert_activity(
            org_id, claim_pk, actor="07-reasoning", action="explained",
            details={"model": get_settings().reasoning_model, "issues_explained": len(result.detail)},
        )
        return {"type": "reasoning.completed"}

    if target == "08-recommendation":
        rec = recommendation.recommend(
            [rules.Issue(i["issue_type"], i["severity"], i["description"], i.get("evidence", {}))
             for i in state["issues"]]
        )
        if rec is None:  # no issues — should not happen after R17, but be safe
            return {"type": "analysis.completed"}
        row = await db.insert_recommendation(
            org_id, claim_pk,
            {
                "action_type": rec.action_type,
                "confidence": rec.confidence,
                "low_confidence": rec.low_confidence,
                "rationale": rec.rationale,
                "cited_issue_types": rec.cited_issue_types,
            },
        )
        await db.insert_activity(
            org_id, claim_pk, actor="08-recommendation", action="recommended",
            details={
                "recommendation_id": row["id"],
                "action_type": rec.action_type,
                "confidence": rec.confidence,
                "low_confidence": rec.low_confidence,
            },
        )
        return {"type": "recommendation.completed"}

    if target in ("09-followup", "10-reminder"):
        rec = state["recommendation"]
        try:
            if target == "09-followup":
                await executors.run_followup(org_id, claim, rec)
            else:
                await executors.run_reminder(org_id, claim, rec)
        except executors.ExecutionFailed as exc:
            return {"type": "execution.failed", "payload": {"agent": target, "error": str(exc), "transient": exc.transient}}
        return {"type": "execution.completed"}

    if target == "12-escalation":
        await escalation.escalate(
            org_id, claim_pk,
            reason_code=decision.reason_code,
            context={
                "trigger_reason": decision.reason_code,
                "claim_status_before": status_before,
                "issue_types": [i["issue_type"] for i in state["issues"]],
                "recommendation": state["recommendation"],
            },
        )
        return None  # a human owns it now

    raise RuntimeError(f"orchestrator has no runner for agent {target!r}")


async def handle(claim_pk: str, trigger: dict, *, _depth: int = 0) -> CommanderDecision:
    """Process one (claim, trigger). Re-invokes itself to walk the pipeline."""
    claim = await db.get_claim(claim_pk)
    if claim is None:
        raise ValueError(f"claim {claim_pk!r} not found")
    org_id = claim["organization_id"]
    status_before = claim["status"]

    # record the human decision itself (the endpoint already wrote the DB change)
    if trigger.get("type") in ("human.approved", "human.declined"):
        payload = trigger.get("payload") or {}
        await db.insert_activity(
            org_id, claim_pk,
            actor=f"human:{payload.get('user_id', 'unknown')}",
            action=trigger["type"],
            details={"recommendation_id": payload.get("recommendation_id")},
        )

    state = await _load_state(claim)
    decision = commander.decide(state, trigger)

    await db.insert_activity(
        org_id, claim_pk, actor="00-commander", action=decision.reason_code,
        details={
            "trigger": trigger.get("type"),
            "action": decision.action,
            "route_to": decision.route_to,
            "next_status": decision.next_status,
        },
    )

    if decision.next_status:
        await db.update_claim(org_id, claim_pk, {"status": decision.next_status})

    if decision.action != "route":
        return decision

    if _depth + 1 >= MAX_INVOCATIONS:
        await escalation.escalate(
            org_id, claim_pk,
            reason_code="loop_cap_exceeded",
            context={"trigger": trigger, "depth": _depth},
        )
        await db.update_claim(org_id, claim_pk, {"status": "escalated"})
        return CommanderDecision("route", "loop_cap_exceeded", "12-escalation", "escalated")

    # reload the claim so the routed agent sees the status the Commander just wrote
    claim = await db.get_claim(claim_pk)
    state = await _load_state(claim)

    # The pipeline keeps walking below (follow-on triggers, or agent.error), but
    # this call returns THIS decision — the one made for the trigger it was
    # handed. Callers pair it with the claim's final status, which they re-read.
    try:
        follow_on = await _run_agent(decision.route_to, org_id, claim, state, decision, status_before)
    except Exception as exc:  # noqa: BLE001 — any agent failure becomes agent.error
        await db.insert_activity(
            org_id, claim_pk, actor=decision.route_to or "?", action="error",
            details={"error": str(exc), "error_type": type(exc).__name__},
        )
        await handle(
            claim_pk,
            {"type": "agent.error", "payload": {"agent": decision.route_to, "error": str(exc)}},
            _depth=_depth + 1,
        )
        return decision

    if follow_on is not None:
        await handle(claim_pk, follow_on, _depth=_depth + 1)
    return decision


# =========================================================================== #
# Phase 2 — eligibility verification (01-eligibility-agent)
#
# THE CARE-SAFETY INVARIANT (docs/agents/00-commander.md §12.3):
#   * every eligibility Commander decision has next_status is None — enforced by
#     a hard raise below, not just a comment;
#   * an emergency check runs DETACHED — nothing on a care path awaits it, its
#     exceptions are swallowed to a recorded check_failed, it chains no
#     care-related follow-on.
# =========================================================================== #
async def _run_eligibility_agent(org_id: str, check_id: str, state: dict) -> dict:
    """Run the pure simulation, write the resolved status onto the (pending)
    check row, log it. Returns the follow-on trigger. Any exception in the pure
    core is caught and recorded as check_failed (never re-raised) — a scheduled
    caller then reaches E6, a detached emergency caller reaches E4."""
    chk = state["eligibility_check"]
    payer = state.get("payer") or {}
    patient = {"name": chk.get("patient_name"), "member_id": chk.get("patient_member_id", "")}

    try:
        result = eligibility.simulate(patient, payer or None)
        status, payload = result.status, result.result_payload
    except Exception as exc:  # noqa: BLE001 — a bug in the pure core must not crash the run
        status = "check_failed"
        payload = {
            "simulated": True,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "recheck_recommended": True,
        }
        await db.insert_activity(
            org_id, None, actor="01-eligibility", action="error",
            eligibility_check_id=check_id, appointment_id=chk.get("appointment_id"),
            details={"error": str(exc), "error_type": type(exc).__name__},
        )

    await db.update_eligibility_check(
        org_id, check_id,
        {
            "status": status,
            "result_payload": payload,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await db.insert_activity(
        org_id, None, actor="01-eligibility", action="verified",
        eligibility_check_id=check_id, appointment_id=chk.get("appointment_id"),
        details={"status": status, "member_bucket": payload.get("member_bucket")},
    )
    return {
        "type": "eligibility_check_failed" if status == "check_failed"
        else "eligibility_check_completed"
    }


async def _run_eligibility_detached(org_id: str, check_id: str, state: dict, *, _depth: int) -> None:
    """The emergency fire-and-forget body. Runs 01, then re-enters
    handle_eligibility with the follow-on (which lands on E4 -> no_action).
    EVERYTHING is swallowed — this coroutine is not awaited by anything on a
    care path, so an exception here has nowhere to propagate and must not become
    an unhandled task error."""
    try:
        follow_on = await _run_eligibility_agent(org_id, check_id, state)
        await handle_eligibility(check_id, follow_on, _depth=_depth)
    except Exception as exc:  # noqa: BLE001
        try:
            await db.insert_activity(
                org_id, None, actor="01-eligibility", action="error",
                eligibility_check_id=check_id,
                details={"error": str(exc), "error_type": type(exc).__name__, "detached": True},
            )
        except Exception:  # noqa: BLE001 — last-ditch; never raise out of a detached task
            pass


def _resolve_emergency(check: dict, appt: dict | None, trigger: dict) -> bool:
    """Mirror of commander._is_emergency_context, resolved from the loaded rows.
    Ambiguity -> True (the non-blocking path)."""
    if (trigger or {}).get("type") == "emergency_patient_registered":
        return True
    if check.get("is_emergency") is True:
        return True
    if isinstance(appt, dict) and appt.get("is_emergency") is True:
        return True
    if appt is None:  # a check with no appointment is treated as the safe path
        return True
    return False


async def handle_eligibility(check_id: str, trigger: dict, *, _depth: int = 0) -> CommanderDecision:
    """Process one (eligibility_check, trigger). The eligibility analogue of
    handle(). organization_id is resolved ONCE, from the check row."""
    check = await db.get_eligibility_check(check_id)
    if check is None:
        raise ValueError(f"eligibility_check {check_id!r} not found")
    org_id = check["organization_id"]

    appt = None
    if check.get("appointment_id"):
        appt = await db.get_appointment(org_id, check["appointment_id"])
    payer = {}
    if check.get("payer_id"):
        payer = await db.get_payer(org_id, check["payer_id"]) or {}

    emergency = _resolve_emergency(check, appt, trigger)
    state = {
        "appointment": appt,
        "eligibility_check": check,
        "payer": payer,
        "context": {"is_emergency": emergency},
    }
    decision = commander.decide(state, trigger)

    await db.insert_activity(
        org_id, None, actor="00-commander", action=decision.reason_code,
        appointment_id=check.get("appointment_id"), eligibility_check_id=check_id,
        details={
            "trigger": trigger.get("type"),
            "action": decision.action,
            "route_to": decision.route_to,
            "is_emergency": emergency,
        },
    )

    # --- the care-safety invariant: HARD enforced, not just documented --------
    if decision.next_status is not None:
        raise RuntimeError(
            "eligibility Commander decision carried a next_status "
            f"({decision.next_status!r} for {decision.reason_code}) — an eligibility "
            "trigger must never transition a care object (00-commander.md §12.3)"
        )
    if emergency and decision.route_to == "12-escalation":
        raise RuntimeError(
            f"eligibility Commander routed an emergency check to 12-escalation "
            f"({decision.reason_code}) — forbidden by 00-commander.md §12.3"
        )

    if decision.action != "route":
        return decision

    if _depth + 1 >= MAX_INVOCATIONS:
        await escalation.escalate(
            org_id, None, reason_code="loop_cap_exceeded",
            context={"trigger": trigger, "depth": _depth},
            appointment_id=check.get("appointment_id"), eligibility_check_id=check_id,
        )
        return CommanderDecision("route", "loop_cap_exceeded", "12-escalation", None)

    if decision.route_to == "01-eligibility":
        if emergency:
            # FIRE-AND-FORGET. Not awaited. Nothing care-related waits on 01.
            _spawn(_run_eligibility_detached(org_id, check_id, state, _depth=_depth + 1))
            return decision
        follow_on = await _run_eligibility_agent(org_id, check_id, state)
        await handle_eligibility(check_id, follow_on, _depth=_depth + 1)
        return decision

    if decision.route_to == "12-escalation":  # E6 — scheduled check_failed only
        await escalation.escalate(
            org_id, None, reason_code=decision.reason_code,
            context={
                "trigger_reason": decision.reason_code,
                "eligibility_status": check.get("status"),
                "payer_name": check.get("payer_name"),
                "is_emergency": emergency,
            },
            appointment_id=check.get("appointment_id"), eligibility_check_id=check_id,
        )
        return decision

    raise RuntimeError(f"handle_eligibility has no dispatch for route_to={decision.route_to!r}")


# =========================================================================== #
# Phase 3 — prior authorization (02-prior-auth-agent)
#
# THE CARE-SAFETY INVARIANT (docs/agents/00-commander.md §13.3):
#   * every prior-auth Commander decision has next_status is None — enforced by a
#     hard raise below, not just a comment;
#   * NO emergency-context decision routes to 12-escalation, to await_human, or
#     to a submission;
#   * an emergency determination runs DETACHED — nothing on a care path awaits
#     it, its exceptions are swallowed to a recorded status, it chains no
#     care-related follow-on.
#
# Prior auth MAY gate an *elective* service (that is correct). Foresight never
# *sets* a gating state — it only records status. The elective-gating nuance
# never touches the emergency path (P5: an emergency determination can only be
# emergency_exempt / insufficient_info — never required_draft).
# =========================================================================== #
_PA_RESPONSE_TO_STATUS = {
    "approved": "auth_approved",
    "info_needed": "info_needed",
    "denied": "auth_denied",
}


def _encounter_of(pa: dict) -> dict:
    return {
        "procedure_code": pa.get("procedure_code", ""),
        "procedure_description": pa.get("procedure_description", ""),
        "place_of_service": pa.get("place_of_service", "office"),
        "is_emergency": pa.get("is_emergency", False),
    }


def _patient_of(pa: dict) -> dict:
    return {"name": pa.get("patient_name"), "member_id": pa.get("patient_member_id", "")}


def _resolve_prior_auth_emergency(pa: dict, appt: dict | None, trigger: dict) -> bool:
    """Mirror of commander._is_prior_auth_emergency_context, resolved from the
    loaded rows. Ambiguity -> True (the non-blocking, no-auth path)."""
    if (trigger or {}).get("type") == "prior_auth_emergency":
        return True
    if pa.get("is_emergency") is True:
        return True
    if pa.get("place_of_service") == "emergency":
        return True
    if isinstance(appt, dict) and appt.get("is_emergency") is True:
        return True
    if appt is None:
        return True
    return False


async def _run_prior_auth_determine(org_id: str, pa_id: str, state: dict) -> dict:
    """Run the pure determination (+ draft the packet when auth is required),
    write it onto the PA row, log it. Returns the follow-on trigger. Any
    exception in the pure core is caught and recorded as insufficient_info
    (never re-raised) — a scheduled caller then reaches A6, a detached emergency
    caller reaches A4."""
    pa = state["prior_authorization"]
    payer = state.get("payer") or {}
    encounter = _encounter_of(pa)
    patient = _patient_of(pa)

    try:
        det = prior_auth.determine(encounter, payer or None)
        status, det_payload = det.status, det.determination_payload
        request_payload = (
            prior_auth.draft_request(encounter, payer or None, patient)
            if status == "required_draft" else None
        )
    except Exception as exc:  # noqa: BLE001 — a bug in the pure core must not crash the run
        status = "insufficient_info"
        det_payload = {
            "simulated": True,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "recheck_recommended": True,
        }
        request_payload = None
        await db.insert_activity(
            org_id, None, actor="02-prior-auth", action="error",
            prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"),
            details={"error": str(exc), "error_type": type(exc).__name__},
        )

    now = datetime.now(timezone.utc).isoformat()
    fields: dict = {
        "status": status,
        "determination_payload": det_payload,
        "determined_at": now,
    }
    if request_payload is not None:
        fields["request_payload"] = request_payload
    if status in ("not_required", "emergency_exempt"):
        fields["resolved_at"] = now
    await db.update_prior_authorization(org_id, pa_id, fields)
    await db.insert_activity(
        org_id, None, actor="02-prior-auth", action="determined",
        prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"),
        details={"status": status, "matched_rule": det_payload.get("matched_rule")},
    )
    return {"type": "prior_auth_determined"}


async def _run_prior_auth_submit(org_id: str, pa_id: str, state: dict) -> dict:
    """Reachable ONLY via A7 (post human-approval). Submit the drafted request
    (simulated send), then compute the deterministic payer response and write the
    resolved status. Returns the follow-on trigger."""
    pa = state["prior_authorization"]
    payer = state.get("payer") or {}
    encounter = _encounter_of(pa)
    patient = _patient_of(pa)

    await db.update_prior_authorization(org_id, pa_id, {"status": "submitting"})
    await db.insert_activity(
        org_id, None, actor="02-prior-auth:submit", action="submitting",
        prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"),
        details={"channel": (pa.get("request_payload") or {}).get("channel", "electronic")},
    )

    submitted_at = datetime.now(timezone.utc).isoformat()
    await db.update_prior_authorization(
        org_id, pa_id, {"status": "submitted", "submitted_at": submitted_at}
    )
    await db.insert_activity(
        org_id, None, actor="02-prior-auth:submit", action="submitted",
        prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"),
        details={"simulated_send": True},
    )

    resp = prior_auth.simulate_response(
        encounter, payer, patient, is_resubmit=bool(pa.get("previous_auth_id"))
    )
    resolved_status = _PA_RESPONSE_TO_STATUS[resp.response_status]
    resolved_fields: dict = {
        "status": resolved_status,
        "response_payload": resp.response_payload,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    if resp.authorization_number:
        resolved_fields["authorization_number"] = resp.authorization_number
    await db.update_prior_authorization(org_id, pa_id, resolved_fields)
    await db.insert_activity(
        org_id, None, actor="02-prior-auth:submit", action="response",
        prior_authorization_id=pa_id, appointment_id=pa.get("appointment_id"),
        details={"response_status": resp.response_status,
                 "bucket": resp.response_payload.get("bucket")},
    )
    return {"type": "prior_auth_response_received"}


async def _run_prior_auth_detached(org_id: str, pa_id: str, state: dict, *, _depth: int) -> None:
    """The emergency fire-and-forget body. Runs 02.determine, then re-enters
    handle_prior_auth with the follow-on (which lands on A4 -> no_action).
    EVERYTHING is swallowed — this coroutine is not awaited by anything on a care
    path."""
    try:
        follow_on = await _run_prior_auth_determine(org_id, pa_id, state)
        await handle_prior_auth(pa_id, follow_on, _depth=_depth)
    except Exception as exc:  # noqa: BLE001
        try:
            await db.insert_activity(
                org_id, None, actor="02-prior-auth", action="error",
                prior_authorization_id=pa_id,
                details={"error": str(exc), "error_type": type(exc).__name__, "detached": True},
            )
        except Exception:  # noqa: BLE001 — last-ditch; never raise out of a detached task
            pass


async def handle_prior_auth(pa_id: str, trigger: dict, *, _depth: int = 0) -> CommanderDecision:
    """Process one (prior_authorization, trigger). The prior-auth analogue of
    handle(). organization_id is resolved ONCE, from the PA row."""
    pa = await db.get_prior_authorization(pa_id)
    if pa is None:
        raise ValueError(f"prior_authorization {pa_id!r} not found")
    org_id = pa["organization_id"]

    appt = None
    if pa.get("appointment_id"):
        appt = await db.get_appointment(org_id, pa["appointment_id"])
    payer = {}
    if pa.get("payer_id"):
        payer = await db.get_payer(org_id, pa["payer_id"]) or {}

    emergency = _resolve_prior_auth_emergency(pa, appt, trigger)
    state = {
        "appointment": appt,
        "prior_authorization": pa,
        "payer": payer,
        "context": {"is_emergency": emergency},
    }
    decision = commander.decide(state, trigger)

    await db.insert_activity(
        org_id, None, actor="00-commander", action=decision.reason_code,
        appointment_id=pa.get("appointment_id"), prior_authorization_id=pa_id,
        details={
            "trigger": trigger.get("type"),
            "action": decision.action,
            "route_to": decision.route_to,
            "is_emergency": emergency,
        },
    )

    # --- the care-safety invariant: HARD enforced, not just documented --------
    if decision.next_status is not None:
        raise RuntimeError(
            "prior-auth Commander decision carried a next_status "
            f"({decision.next_status!r} for {decision.reason_code}) — a prior-auth "
            "trigger must never write a status (00-commander.md §13.3)"
        )
    if emergency and decision.route_to == "12-escalation":
        raise RuntimeError(
            f"prior-auth Commander routed an emergency to 12-escalation "
            f"({decision.reason_code}) — forbidden by 00-commander.md §13.3"
        )
    if emergency and decision.action == "await_human":
        raise RuntimeError(
            f"prior-auth Commander parked an emergency at await_human "
            f"({decision.reason_code}) — forbidden by 00-commander.md §13.3"
        )

    if decision.action != "route":
        return decision

    if _depth + 1 >= MAX_INVOCATIONS:
        await escalation.escalate(
            org_id, None, reason_code="loop_cap_exceeded",
            context={"trigger": trigger, "depth": _depth},
            appointment_id=pa.get("appointment_id"), prior_authorization_id=pa_id,
        )
        return CommanderDecision("route", "loop_cap_exceeded", "12-escalation", None)

    if decision.route_to == "02-prior-auth":
        if decision.reason_code == "prior_auth_emergency_determine":
            # FIRE-AND-FORGET. Not awaited. Nothing care-related waits on 02.
            _spawn(_run_prior_auth_detached(org_id, pa_id, state, _depth=_depth + 1))
            return decision
        if decision.reason_code == "prior_auth_submit":  # A7 — post-approval only
            follow_on = await _run_prior_auth_submit(org_id, pa_id, state)
            await handle_prior_auth(pa_id, follow_on, _depth=_depth + 1)
            return decision
        # A3 — scheduled determination, awaited ahead of the visit
        follow_on = await _run_prior_auth_determine(org_id, pa_id, state)
        await handle_prior_auth(pa_id, follow_on, _depth=_depth + 1)
        return decision

    if decision.route_to == "12-escalation":  # A10 / A11 (non-emergency) only
        await escalation.escalate(
            org_id, None, reason_code=decision.reason_code,
            context={
                "trigger_reason": decision.reason_code,
                "prior_auth_status": pa.get("status"),
                "response_status": (pa.get("response_payload") or {}).get("response_status"),
                "payer_name": pa.get("payer_name"),
                "procedure_code": pa.get("procedure_code"),
                "is_emergency": emergency,
            },
            appointment_id=pa.get("appointment_id"), prior_authorization_id=pa_id,
        )
        return decision

    raise RuntimeError(f"handle_prior_auth has no dispatch for route_to={decision.route_to!r}")


# =========================================================================== #
# Phase 5 — appeals (11-appeals-agent)
#
# 11 is a Commander agent (a fourth disjoint family, AP1-AP12). There is no
# care-safety concern here (billing, not care), so an appeal decision MAY carry
# a next_status. The TWO STRUCTURAL INVARIANTS (00-commander.md §14.3) are
# hard-raised below, not just documented:
#
#   * the ONLY route to 11.submit is AP6 (reason_code 'appeal_submit'), and it
#     may only run when a human is recorded on the appeal (`reviewed_by`);
#   * the ONLY non-None next_status is the exact pair
#     ('appeal_won_claim_reversed', 'paid') — a won appeal reversing the claim.
# =========================================================================== #
_APPEAL_RESOLUTION_TO_STATUS = {
    "approved": "appeal_approved",
    "partial": "appeal_partial",
    "denied": "appeal_denied",
}


async def _load_appeal_state(claim: dict) -> dict:
    org_id = claim["organization_id"]
    claim_pk = claim["id"]
    payer = await db.get_payer(org_id, claim["payer_id"]) if claim.get("payer_id") else {}
    issues = await db.list_issues(org_id, claim_pk)
    recs = await db.list_recommendations(org_id, claim_pk)
    fups = await db.list_follow_ups(org_id, claim_pk)
    ap = await db.latest_appeal(org_id, claim_pk)
    return {
        "claim": claim,
        "payer": payer or {},
        "issues": issues,
        "recommendations": recs,
        "follow_ups": fups,
        "appeal": ap,
    }


async def _run_appeal_draft(org_id: str, claim: dict, state: dict) -> dict:
    """AP2 body. Ensure a pending appeal row exists, run the PURE appeal_basis(),
    then — only if there are grounds — call the drafting model. Returns the
    follow-on trigger (`appeal_drafted` or `appeal_error`)."""
    claim_pk = claim["id"]
    ap = state.get("appeal")
    if ap is None or ap.get("status") != "pending":
        ap = await db.insert_appeal(org_id, {
            "claim_id": claim_pk, "status": "pending",
            "denial_reason": claim.get("denial_reason"),
        })
    appeal_id = ap["id"]

    basis = appeals.appeal_basis(
        claim, state["issues"], state["recommendations"], state["follow_ups"],
        claim.get("denial_reason"),
    )

    if not basis.has_basis:
        await db.update_appeal(org_id, appeal_id, {
            "status": "insufficient_basis",
            "grounds": [g.as_dict() for g in basis.grounds],
            "has_basis": False,
            "denial_reason": claim.get("denial_reason"),
        })
        await db.insert_activity(
            org_id, claim_pk, actor="11-appeals", action="insufficient_basis",
            details={"appeal_id": appeal_id, "grounds": len(basis.grounds)},
        )
        return {"type": "appeal_drafted"}

    try:
        letter, model = await appeals.draft_appeal(claim, state["payer"], basis)
    except appeals.AppealsUnavailable as exc:
        await db.update_appeal(org_id, appeal_id, {
            "status": "error", "letter_text": "",
            "grounds": [g.as_dict() for g in basis.grounds],
            "has_basis": True, "denial_reason": claim.get("denial_reason"),
        })
        await db.insert_activity(
            org_id, claim_pk, actor="11-appeals", action="error",
            details={"appeal_id": appeal_id, "error": str(exc), "error_type": type(exc).__name__},
        )
        return {"type": "appeal_error"}

    await db.update_appeal(org_id, appeal_id, {
        "status": "drafted",
        "letter_text": letter,
        "model": model,
        "grounds": [g.as_dict() for g in basis.grounds],
        "has_basis": True,
        "denial_reason": claim.get("denial_reason"),
    })
    await db.insert_activity(
        org_id, claim_pk, actor="11-appeals", action="drafted",
        details={"appeal_id": appeal_id, "model": model,
                 "grounds": [g.as_dict() for g in basis.grounds]},
    )
    return {"type": "appeal_drafted"}


async def _run_appeal_submit(org_id: str, claim: dict, state: dict) -> dict:
    """AP6 body — post-approval only. Simulated send + the DETERMINISTIC
    resolution. Returns the `appeal_resolution_received` follow-on."""
    claim_pk = claim["id"]
    ap = state["appeal"]
    appeal_id = ap["id"]
    is_resubmit = bool(ap.get("previous_appeal_id"))

    basis = appeals.appeal_basis(
        claim, state["issues"], state["recommendations"], state["follow_ups"],
        ap.get("denial_reason") or claim.get("denial_reason"),
    )

    now = datetime.now(timezone.utc).isoformat()
    submission = {
        "simulated": True,
        "channel": "simulated",
        "claim_id": claim.get("claim_id"),
        "payer_name": (state["payer"] or {}).get("name"),
        "grounds_cited": basis.distinct_grounds,
        "is_resubmit": is_resubmit,
        "submitted_at": now,
    }
    await db.update_appeal(org_id, appeal_id, {"status": "submitting", "submission_payload": submission})

    resolution = appeals.simulate_resolution(claim, appeal_id, basis, is_resubmit=is_resubmit)
    status = _APPEAL_RESOLUTION_TO_STATUS[resolution.outcome]
    await db.update_appeal(org_id, appeal_id, {
        "status": status,
        "resolution_payload": {**resolution.payload, "responded_at": now},
        "resolved_at": now,
    })
    await db.insert_activity(
        org_id, claim_pk, actor="11-appeals:submit", action="resolution",
        details={"appeal_id": appeal_id, "outcome": resolution.outcome,
                 "bucket": resolution.bucket, "reversed_amount": resolution.reversed_amount,
                 "is_resubmit": is_resubmit},
    )
    return {"type": "appeal_resolution_received"}


async def handle_appeal(claim_id: str, trigger: dict, *, _depth: int = 0) -> CommanderDecision:
    """Process one (denied claim, appeal trigger). The appeals analogue of
    handle(). organization_id is resolved ONCE, from the claim row."""
    claim = await db.get_claim(claim_id)
    if claim is None:
        raise ValueError(f"claim {claim_id!r} not found")
    org_id = claim["organization_id"]

    state = await _load_appeal_state(claim)
    decision = commander.decide(state, trigger)
    ap = state.get("appeal")
    appeal_id = ap.get("id") if isinstance(ap, dict) else None

    await db.insert_activity(
        org_id, claim_id, actor="00-commander", action=decision.reason_code,
        details={
            "trigger": trigger.get("type"),
            "action": decision.action,
            "route_to": decision.route_to,
            "next_status": decision.next_status,
            "appeal_id": appeal_id,
        },
    )

    # --- the two structural invariants: HARD enforced, not just documented -----
    if decision.next_status is not None and not (
        decision.reason_code == "appeal_won_claim_reversed" and decision.next_status == "paid"
    ):
        raise RuntimeError(
            "appeals Commander decision carried a forbidden next_status "
            f"({decision.next_status!r} for {decision.reason_code}) — the only "
            "appeal transition is denied->paid on AP9 (00-commander.md §14.3)"
        )
    if (
        decision.action == "route"
        and decision.route_to == "11-appeals"
        and decision.reason_code == "appeal_submit"
        and not (isinstance(ap, dict) and ap.get("reviewed_by"))
    ):
        raise RuntimeError(
            "appeals Commander routed to 11.submit without a recorded human "
            f"approver on the appeal ({decision.reason_code}) — forbidden by "
            "00-commander.md §14.3"
        )

    if decision.next_status is not None:
        await db.update_claim(org_id, claim_id, {"status": decision.next_status})

    if decision.action != "route":
        return decision

    if _depth + 1 >= MAX_INVOCATIONS:
        await escalation.escalate(
            org_id, claim_id, reason_code="loop_cap_exceeded",
            context={"trigger": trigger, "depth": _depth, "appeal_id": appeal_id},
        )
        return CommanderDecision("route", "loop_cap_exceeded", "12-escalation", None)

    if decision.route_to == "11-appeals":
        if decision.reason_code == "appeal_submit":  # AP6 — post-approval only
            follow_on = await _run_appeal_submit(org_id, claim, state)
        else:  # AP2 — draft / re-draft
            follow_on = await _run_appeal_draft(org_id, claim, state)
        await handle_appeal(claim_id, follow_on, _depth=_depth + 1)
        return decision

    if decision.route_to == "12-escalation":  # AP3 / AP5 / AP7 / AP10 / AP11 / AP12
        await escalation.escalate(
            org_id, claim_id, reason_code=decision.reason_code,
            context={
                "trigger_reason": decision.reason_code,
                "appeal_id": appeal_id,
                "appeal_status": ap.get("status") if isinstance(ap, dict) else None,
                "appeal_resolution": (ap.get("resolution_payload") or {}).get("outcome")
                if isinstance(ap, dict) else None,
                "claim_status": claim.get("status"),
                "payer_id": claim.get("payer_id"),
            },
        )
        return decision

    raise RuntimeError(f"handle_appeal has no dispatch for route_to={decision.route_to!r}")
