"""Appeals endpoints (Phase 5 / 11-appeals-agent).

Reads go through the caller's RLS scope; the single mutating path (approve /
decline / start / resubmit) verifies the target is visible, records the human
decision under the caller's own scope, then hands off to
`orchestrator.handle_appeal`.

THE HUMAN-APPROVAL GATE (docs/agents/11-appeals-agent.md §2.2): the drafted
appeal is never sent automatically. `approve-submission` records the reviewer on
the appeal row (status stays `drafted`, exactly like Phase 3's
`approve-submission`), then emits `appeal_submission_approved` — the Commander's
AP6, guarded on that trigger + `status == 'drafted'`, is the only path to
`11.submit`.

11 is a Commander agent; `commander.py` gains a fourth disjoint family (§14) but
R1-R20 / E1-E7 / A1-A11 are untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..agents import db, orchestrator
from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["appeals"])

_ACTIVE_APPEAL_STATUSES = {"pending", "drafted", "submitting", "submitted"}


def _decision(d) -> dict:
    return {"action": d.action, "reason_code": d.reason_code, "route_to": d.route_to}


async def _visible_claim(access_token: str, claim_pk: str) -> dict:
    rows = await rest_get(
        access_token, "/claims",
        {"id": f"eq.{claim_pk}", "select": "id,claim_id,status,denial_reason"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    return rows[0]


async def _visible_appeal(access_token: str, appeal_id: str) -> dict:
    rows = await rest_get(access_token, "/appeals", {"id": f"eq.{appeal_id}", "select": "*"})
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="appeal not found")
    return rows[0]


@router.get("/api/claims/{claim_id}/appeal")
async def get_claim_appeal(
    claim_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    await _visible_claim(ctx.access_token, claim_id)
    chain = await rest_get(
        ctx.access_token, "/appeals",
        {"claim_id": f"eq.{claim_id}", "select": "*", "order": "created_at"},
    )
    activity = await rest_get(
        ctx.access_token, "/activity_log",
        {"claim_id": f"eq.{claim_id}", "select": "actor,action,details,created_at",
         "order": "created_at"},
    )
    appeal_activity = [
        a for a in activity
        if (a["actor"] or "").startswith("11-appeals")
        or (a["action"] or "").startswith("appeal_")
        or (a.get("details") or {}).get("appeal_id")
    ]
    return {
        "claim_id": claim_id,
        "appeal": chain[-1] if chain else None,
        "chain": chain,
        "activity_log": appeal_activity,
    }


@router.post("/api/claims/{claim_id}/appeal", status_code=status.HTTP_201_CREATED)
async def start_appeal(
    claim_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """Manually start an appeal on a `denied` claim (for claims denied before
    this feature, or where auto-draft is off). Emits `claim_denied`; the
    orchestrator creates the `pending` row and routes to 11 (AP2)."""
    org_id = ctx.organization_id
    claim = await _visible_claim(ctx.access_token, claim_id)
    if claim["status"] != "denied":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"claim is {claim['status']}, not denied — nothing to appeal",
        )
    existing = await db.latest_appeal(org_id, claim_id)
    if existing and existing["status"] in _ACTIVE_APPEAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"an appeal is already {existing['status']} for this claim",
        )
    decision = await orchestrator.handle_appeal(
        claim_id, {"type": "claim_denied", "payload": {"user_id": ctx.user_id}}
    )
    fresh = await db.latest_appeal(org_id, claim_id)
    return {"appeal": fresh, "decision": _decision(decision)}


@router.post("/api/appeals/{appeal_id}/approve-submission",
             status_code=status.HTTP_201_CREATED)
async def approve_submission(
    appeal_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """A human approves sending the drafted letter. Records the reviewer on the
    appeal (status stays `drafted`), then emits `appeal_submission_approved`."""
    org_id = ctx.organization_id
    appeal = await _visible_appeal(ctx.access_token, appeal_id)
    if appeal["status"] != "drafted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"appeal is {appeal['status']}, not drafted",
        )
    await db.update_appeal(org_id, appeal_id, {
        "reviewed_by": ctx.user_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.insert_activity(
        org_id, appeal["claim_id"], actor=f"human:{ctx.user_id}",
        action="appeal_submission_approved", details={"appeal_id": appeal_id},
    )
    decision = await orchestrator.handle_appeal(
        appeal["claim_id"],
        {"type": "appeal_submission_approved", "payload": {"user_id": ctx.user_id, "appeal_id": appeal_id}},
    )
    return {"decision": _decision(decision)}


@router.post("/api/appeals/{appeal_id}/decline-submission",
             status_code=status.HTTP_201_CREATED)
async def decline_submission(
    appeal_id: str, ctx: AuthContext = Depends(require_organization)
) -> dict:
    org_id = ctx.organization_id
    appeal = await _visible_appeal(ctx.access_token, appeal_id)
    if appeal["status"] != "drafted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"appeal is {appeal['status']}, not drafted",
        )
    await db.update_appeal(org_id, appeal_id, {
        "status": "submission_declined",
        "reviewed_by": ctx.user_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.insert_activity(
        org_id, appeal["claim_id"], actor=f"human:{ctx.user_id}",
        action="appeal_submission_declined", details={"appeal_id": appeal_id},
    )
    decision = await orchestrator.handle_appeal(
        appeal["claim_id"],
        {"type": "appeal_submission_declined", "payload": {"user_id": ctx.user_id, "appeal_id": appeal_id}},
    )
    return {"decision": _decision(decision)}


class Resubmit(BaseModel):
    added_context: str | None = None
    documentation_affirmed: bool = False


@router.post("/api/appeals/{appeal_id}/resubmit", status_code=status.HTTP_201_CREATED)
async def resubmit(
    appeal_id: str, body: Resubmit, ctx: AuthContext = Depends(require_organization)
) -> dict:
    """A second-level appeal. Appends a new `pending` `appeals` row with
    `previous_appeal_id`, then emits `appeal_resubmitted` (skips the AP1
    re-entrancy guard)."""
    org_id = ctx.organization_id
    prior = await _visible_appeal(ctx.access_token, appeal_id)
    if prior["status"] in _ACTIVE_APPEAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"appeal is still {prior['status']} — resubmit only after a resolution",
        )
    new_row = await db.insert_appeal(org_id, {
        "claim_id": prior["claim_id"],
        "previous_appeal_id": appeal_id,
        "denial_reason": prior.get("denial_reason"),
        "status": "pending",
        "submission_payload": (
            {"added_context": body.added_context.strip(), "documentation_affirmed": True}
            if body.added_context and body.added_context.strip() and body.documentation_affirmed
            else {}
        ),
    })
    decision = await orchestrator.handle_appeal(
        prior["claim_id"],
        {"type": "appeal_resubmitted",
         "payload": {"user_id": ctx.user_id, "appeal_id": new_row["id"], "previous_appeal_id": appeal_id}},
    )
    fresh = await db.get_appeal(new_row["id"])
    return {"appeal": fresh, "decision": _decision(decision)}
