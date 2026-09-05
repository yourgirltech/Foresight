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

from datetime import datetime, timezone

from ..config import get_settings
from . import commander, db, escalation, executors, reasoning, recommendation, rules
from .commander import CommanderDecision

MAX_INVOCATIONS = 12


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
