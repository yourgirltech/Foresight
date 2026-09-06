"""Dashboard aggregates.

Real counts only — every number here is derived from the caller's own
RLS-scoped rows (claims, eligibility_checks). Things Foresight has not built yet
(patients seen today, reminders sent, refill requests, time saved) are NOT
invented here; the frontend renders those as clearly-labelled placeholders.
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends

from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard")
async def dashboard(ctx: AuthContext = Depends(require_organization)) -> dict:
    claims = await rest_get(
        ctx.access_token,
        "/claims",
        {"select": "status,risk_level,documentation_present,authorization_present,amount"},
    )
    checks = await rest_get(
        ctx.access_token,
        "/eligibility_checks",
        {"select": "status,is_emergency"},
    )

    by_risk = Counter(c.get("risk_level") for c in claims if c.get("risk_level"))
    by_status = Counter(c.get("status") for c in claims)
    total_claims = len(claims)
    low = by_risk.get("Low", 0)
    medium = by_risk.get("Medium", 0)
    high = by_risk.get("High", 0)

    # "at risk" = a human already has to look at it, or the engine rated it High
    at_risk = by_status.get("escalated", 0) + high
    # "may be denied" without action = High risk still awaiting a human decision
    awaiting = by_status.get("awaiting_approval", 0)
    _open = ("awaiting_approval", "analyzed", "reasoned", "escalated")
    missing_docs = sum(
        1 for c in claims if not c.get("documentation_present") and c.get("status") in _open
    )
    missing_auth = sum(
        1 for c in claims if not c.get("authorization_present") and c.get("status") in _open
    )

    elig_status = Counter(c.get("status") for c in checks)
    needs_followup = elig_status.get("pending", 0) + elig_status.get("insufficient_info", 0)

    scored = low + medium + high
    clean_pct = round(100 * low / scored) if scored else 0

    return {
        "claims": {
            "total": total_claims,
            "submitted": total_claims,
            "risk": {"low": low, "medium": medium, "high": high, "scored": scored},
            "escalated": by_status.get("escalated", 0),
            "awaiting_approval": awaiting,
            "at_risk": at_risk,
            "missing_documentation": missing_docs,
            "missing_authorization": missing_auth,
            "clean_pct": clean_pct,
        },
        "eligibility": {
            "total": len(checks),
            "needs_followup": needs_followup,
            "verified_active": elig_status.get("verified_active", 0),
            "verified_inactive": elig_status.get("verified_inactive", 0),
            "check_failed": elig_status.get("check_failed", 0),
            "emergency": sum(1 for c in checks if c.get("is_emergency")),
        },
    }
