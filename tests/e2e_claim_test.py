#!/usr/bin/env python3
"""End-to-end: one claim, seeded, driven through the Commander, approved.

Confirms the right agent executes and the right activity_log entries appear, all
scoped to the claim's organization_id.

Three claims, one per outcome:
  * AUTH     — missing authorization -> submit_authorization_request -> approve
               -> 09-followup executes -> status 'actioned', a follow_ups row.
  * CODING   — code mismatch -> resubmit_corrected_coding -> approve
               -> R10 -> 12 logs it -> status 'manual_action_required',
               NO follow_ups row, an escalations row (approved_manual_action).
  * DECLINE  — missing documentation -> request_documentation -> decline
               -> status 'declined', no execution.

07-reasoning-agent calls the Claude API. Without ANTHROPIC_API_KEY the run stops
at the reasoning step (claims escalate) and the approval assertions are SKIPPED —
the banner says so. The deterministic assertions (06 + Commander routing +
tenant scoping) always run.

Run (local stack up):  python tests/e2e_claim_test.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, orchestrator  # noqa: E402
from app.config import get_settings  # noqa: E402

NOW = datetime.now(timezone.utc)
KEY = bool(get_settings().anthropic_api_key)


def payer(org_id, name, auth_req, doc_req, thr):
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": auth_req, "documentation_required": doc_req,
        "follow_up_threshold_days": thr,
    })[0]


def claim(org_id, payer_id, claim_id, **ev):
    row = {
        "organization_id": org_id, "payer_id": payer_id, "claim_id": claim_id,
        "patient_name": "E2E Patient", "patient_member_id": "E2E-1", "amount": 500.00,
        "authorization_present": True, "documentation_present": True, "coding_matches": True,
        "created_at": NOW.isoformat(),
    }
    row.update(ev)
    return svc_write("POST", "claims", {}, row)[0]


async def approve_or_decline(org_id, claim_pk, uid, *, approve):
    rec = await db.latest_recommendation(org_id, claim_pk)
    assert rec and rec["approval_status"] == "pending", f"no pending recommendation: {rec}"
    await db.set_recommendation_decision(
        org_id, rec["id"], approval_status="approved" if approve else "declined", decided_by=uid
    )
    return await orchestrator.handle(
        claim_pk,
        {"type": "human.approved" if approve else "human.declined",
         "payload": {"user_id": uid, "recommendation_id": rec["id"]}},
    )


def claim_row(pk):
    return svc_get("claims", {"id": f"eq.{pk}", "select": "status,risk_level,risk_score,organization_id"})[0]


def activity(pk):
    return svc_get("activity_log", {"claim_id": f"eq.{pk}", "select": "actor,action,organization_id",
                                    "order": "created_at"})


async def main() -> int:
    chk = Check()
    print("End-to-end claim pipeline")
    print(f"  07 reasoning: {'ANTHROPIC_API_KEY present — full path' if KEY else 'NO KEY — approval-path assertions SKIPPED'}\n")

    org_id, uid = ensure_org("e2e_admin@foresight.test", "E2E — Northgate Clinic")
    other_org, _ = ensure_org("e2e_other@foresight.test", "E2E — Other Clinic")

    p_full = payer(org_id, "E2E Meridian", True, True, 21)     # requires auth + docs
    p_none = payer(org_id, "E2E Summit", False, False, None)   # requires neither

    c_auth = claim(org_id, p_full["id"], "CLM-E2E-AUTH", authorization_present=False)
    c_code = claim(org_id, p_none["id"], "CLM-E2E-CODING", coding_matches=False)
    c_decl = claim(org_id, p_full["id"], "CLM-E2E-DECLINE", documentation_present=False)

    # ---------- ingest all three ----------
    d_auth = await orchestrator.handle(c_auth["id"], {"type": "claim.ingested"})
    await orchestrator.handle(c_code["id"], {"type": "claim.ingested"})
    await orchestrator.handle(c_decl["id"], {"type": "claim.ingested"})

    # ---------- deterministic assertions (always) ----------
    r_auth = claim_row(c_auth["id"])
    chk("AUTH claim scored by 06: missing auth -> 50 / Medium",
        r_auth["risk_score"] == 50 and r_auth["risk_level"] == "Medium", str(r_auth))
    issues_auth = svc_get("claim_issues", {"claim_id": f"eq.{c_auth['id']}", "select": "issue_type,organization_id"})
    chk("AUTH claim has one missing_authorization issue, scoped to the org",
        [i["issue_type"] for i in issues_auth] == ["missing_authorization"]
        and all(i["organization_id"] == org_id for i in issues_auth), str(issues_auth))

    r_code = claim_row(c_code["id"])
    chk("CODING claim scored by 06: code mismatch -> 30 / Low",
        r_code["risk_score"] == 30 and r_code["risk_level"] == "Low", str(r_code))

    commander_rows = [a for a in activity(c_auth["id"]) if a["actor"] == "00-commander"]
    chk("Commander logged its decisions to activity_log (org-scoped)",
        len(commander_rows) >= 2 and all(a["organization_id"] == org_id for a in commander_rows),
        str(commander_rows))
    chk("first Commander decision on ingest was needs_analysis -> 06",
        commander_rows[0]["action"] == "needs_analysis", str(commander_rows[:1]))

    if not KEY:
        chk("without a key, AUTH claim escalated at the reasoning step",
            claim_row(c_auth["id"])["status"] == "escalated"
            and d_auth.reason_code in ("agent_error",), str(d_auth))
        print("\n  (approval-path assertions skipped — set ANTHROPIC_API_KEY and re-run)")
        return chk.summary("deterministic pipeline holds; approval path needs a key.")

    # ---------- full path: reasoning + recommendation ----------
    chk("AUTH claim reached awaiting_approval", claim_row(c_auth["id"])["status"] == "awaiting_approval")
    chk("AUTH claim has a reasoning_summary (07)",
        bool(svc_get("claims", {"id": f"eq.{c_auth['id']}", "select": "reasoning_summary"})[0]["reasoning_summary"]))
    rec_auth = await db.latest_recommendation(org_id, c_auth["id"])
    chk("08 recommended submit_authorization_request", rec_auth["action_type"] == "submit_authorization_request", str(rec_auth))

    # ---------- approve AUTH -> 09 executes ----------
    d = await approve_or_decline(org_id, c_auth["id"], uid, approve=True)
    chk("approve AUTH -> Commander routes approved_followup -> 09-followup",
        d.reason_code == "approved_followup" and d.route_to == "09-followup", str(d))
    chk("AUTH claim status is now 'actioned'", claim_row(c_auth["id"])["status"] == "actioned")
    fu = svc_get("follow_ups", {"claim_id": f"eq.{c_auth['id']}", "select": "kind,originating_agent,organization_id,sent_at"})
    chk("09 wrote a follow_ups record (kind=follow_up, agent 09-followup, org-scoped, sent)",
        len(fu) == 1 and fu[0]["kind"] == "follow_up" and fu[0]["originating_agent"] == "09-followup"
        and fu[0]["organization_id"] == org_id and fu[0]["sent_at"], str(fu))
    acts = [a["action"] for a in activity(c_auth["id"])]
    chk("activity_log shows: needs_analysis, needs_reasoning, needs_recommendation, "
        "awaiting_human_approval, human.approved, approved_followup, executed, execution_complete",
        all(x in acts for x in ("needs_analysis", "needs_reasoning", "needs_recommendation",
                                "awaiting_human_approval", "human.approved", "approved_followup",
                                "executed", "execution_complete")), str(acts))
    chk("every activity row for the AUTH claim is scoped to the org",
        all(a["organization_id"] == org_id for a in activity(c_auth["id"])))
    chk("no follow_ups / escalations leaked to the other clinic",
        svc_get("follow_ups", {"organization_id": f"eq.{other_org}", "select": "id"}) == []
        and svc_get("escalations", {"organization_id": f"eq.{other_org}", "select": "id"}) == [])

    # ---------- approve CODING -> manual_action_required ----------
    rec_code = await db.latest_recommendation(org_id, c_code["id"])
    chk("08 recommended resubmit_corrected_coding for CODING",
        rec_code["action_type"] == "resubmit_corrected_coding", str(rec_code))
    d = await approve_or_decline(org_id, c_code["id"], uid, approve=True)
    chk("approve CODING -> Commander routes approved_manual_action -> 12",
        d.reason_code == "approved_manual_action" and d.route_to == "12-escalation", str(d))
    chk("CODING claim status is now 'manual_action_required'",
        claim_row(c_code["id"])["status"] == "manual_action_required")
    chk("CODING claim has NO follow_ups row (no agent executed it)",
        svc_get("follow_ups", {"claim_id": f"eq.{c_code['id']}", "select": "id"}) == [])
    esc = svc_get("escalations", {"claim_id": f"eq.{c_code['id']}", "select": "reason_code,originating_agent,organization_id"})
    chk("12 wrote an escalation (approved_manual_action, from 00-commander, org-scoped)",
        len(esc) == 1 and esc[0]["reason_code"] == "approved_manual_action"
        and esc[0]["organization_id"] == org_id, str(esc))

    # ---------- decline DECLINE ----------
    d = await approve_or_decline(org_id, c_decl["id"], uid, approve=False)
    chk("decline -> Commander declined_by_human, no route",
        d.reason_code == "declined_by_human" and d.route_to is None, str(d))
    chk("DECLINE claim status is now 'declined'", claim_row(c_decl["id"])["status"] == "declined")
    chk("DECLINE claim has no follow_ups and no escalations",
        svc_get("follow_ups", {"claim_id": f"eq.{c_decl['id']}", "select": "id"}) == []
        and svc_get("escalations", {"claim_id": f"eq.{c_decl['id']}", "select": "id"}) == [])

    return chk.summary("full claim pipeline holds, end to end, tenant-scoped.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
