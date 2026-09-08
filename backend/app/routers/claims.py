"""Claims & billing endpoints.

Reads go straight through the caller's RLS scope (their own JWT forwarded to
PostgREST). The approve / decline / reanalyze writes verify the claim is visible
to the caller first (RLS), record the human decision under the caller's own
scope, then hand off to the agent orchestrator.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ..agents import orchestrator
from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get, rest_patch

router = APIRouter(tags=["claims"])


async def _visible_claim(ctx: AuthContext, claim_pk: str) -> dict:
    rows = await rest_get(
        ctx.access_token,
        "/claims",
        {"id": f"eq.{claim_pk}", "select": "id,claim_id,status,organization_id"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    return rows[0]


@router.get("/api/claims")
async def list_claims(ctx: AuthContext = Depends(require_organization)) -> dict:
    rows = await rest_get(
        ctx.access_token,
        "/claims",
        {
            "select": "id,claim_id,patient_name,amount,status,risk_score,risk_level,created_at,payers(name)",
            "order": "created_at.desc",
        },
    )
    claims = [
        {**r, "payer_name": (r.pop("payers", None) or {}).get("name")}
        for r in rows
    ]
    return {"organization_id": ctx.organization_id, "claims": claims}


@router.get("/api/claims/{claim_pk}")
async def get_claim(claim_pk: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    claims = await rest_get(
        ctx.access_token,
        "/claims",
        {
            "id": f"eq.{claim_pk}",
            "select": (
                "id,claim_id,patient_name,patient_member_id,amount,status,risk_score,risk_level,"
                "authorization_present,documentation_present,coding_matches,last_followup_at,"
                "denial_reason,reasoning_summary,reasoning_detail,reasoning_generated_at,created_at,"
                "payers(id,name,authorization_required,documentation_required,follow_up_threshold_days)"
            ),
        },
    )
    if not claims:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    claim = claims[0]
    payer = claim.pop("payers", None)

    issues, recs, activity, escalations, follow_ups = (
        await rest_get(ctx.access_token, "/claim_issues",
                       {"claim_id": f"eq.{claim_pk}", "select": "issue_type,severity,description,evidence,created_at",
                        "order": "created_at"}),
        await rest_get(ctx.access_token, "/recommendations",
                       {"claim_id": f"eq.{claim_pk}",
                        "select": "id,action_type,confidence,low_confidence,rationale,cited_issue_types,approval_status,decided_at,decided_by,created_at",
                        "order": "created_at.desc"}),
        await rest_get(ctx.access_token, "/activity_log",
                       {"claim_id": f"eq.{claim_pk}", "select": "actor,action,details,created_at", "order": "created_at"}),
        await rest_get(ctx.access_token, "/escalations",
                       {"claim_id": f"eq.{claim_pk}", "select": "reason_code,originating_agent,context,created_at",
                        "order": "created_at"}),
        await rest_get(ctx.access_token, "/follow_ups",
                       {"claim_id": f"eq.{claim_pk}",
                        "select": "kind,note,due_at,originating_agent,simulated_send,sent_at,created_at",
                        "order": "created_at"}),
    )

    # Phase 5: the appeal chain for a denied claim (newest last), if any.
    appeals_rows = await rest_get(
        ctx.access_token, "/appeals",
        {"claim_id": f"eq.{claim_pk}", "select": "*", "order": "created_at"},
    )

    return {
        "claim": claim,
        "payer": payer,
        "issues": issues,
        "recommendations": recs,
        "recommendation": recs[0] if recs else None,
        "activity_log": activity,
        "escalations": escalations,
        "follow_ups": follow_ups,
        "appeals": appeals_rows,
        "appeal": appeals_rows[-1] if appeals_rows else None,
    }


async def _decide(ctx: AuthContext, claim_pk: str, approve: bool) -> dict:
    claim = await _visible_claim(ctx, claim_pk)

    pending = await rest_get(
        ctx.access_token,
        "/recommendations",
        {
            "claim_id": f"eq.{claim_pk}",
            "approval_status": "eq.pending",
            "select": "id,action_type,low_confidence,created_at",
            "order": "created_at.desc",
        },
    )
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no pending recommendation to act on for this claim",
        )
    rec = pending[0]

    now = datetime.now(timezone.utc).isoformat()
    updated = await rest_patch(
        ctx.access_token,
        "/recommendations",
        {"id": f"eq.{rec['id']}", "approval_status": "eq.pending"},
        {
            "approval_status": "approved" if approve else "declined",
            "decided_at": now,
            "decided_by": ctx.user_id,
        },
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="recommendation already decided")

    trigger = {
        "type": "human.approved" if approve else "human.declined",
        "payload": {"user_id": ctx.user_id, "recommendation_id": rec["id"]},
    }
    decision = await orchestrator.handle(claim_pk, trigger)

    fresh = await _visible_claim(ctx, claim_pk)
    return {
        "claim_id": fresh["claim_id"],
        "status": fresh["status"],
        "decision": {
            "action": decision.action,
            "reason_code": decision.reason_code,
            "route_to": decision.route_to,
        },
    }


@router.post("/api/claims/{claim_pk}/approve")
async def approve_claim(claim_pk: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    return await _decide(ctx, claim_pk, approve=True)


@router.post("/api/claims/{claim_pk}/decline")
async def decline_claim(claim_pk: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    return await _decide(ctx, claim_pk, approve=False)


@router.post("/api/claims/{claim_pk}/reanalyze")
async def reanalyze_claim(claim_pk: str, ctx: AuthContext = Depends(require_organization)) -> dict:
    await _visible_claim(ctx, claim_pk)
    decision = await orchestrator.handle(
        claim_pk, {"type": "claim.reanalyze", "payload": {"user_id": ctx.user_id}}
    )
    fresh = await _visible_claim(ctx, claim_pk)
    return {
        "claim_id": fresh["claim_id"],
        "status": fresh["status"],
        "decision": {
            "action": decision.action,
            "reason_code": decision.reason_code,
            "route_to": decision.route_to,
        },
    }
