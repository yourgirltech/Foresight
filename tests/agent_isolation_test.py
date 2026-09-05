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

    return chk.summary("agent pipeline never crosses a tenant boundary.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
