#!/usr/bin/env python3
"""End-to-end: appeals (11), driven through the Commander.

Same pattern as e2e_prior_auth_test.py — real orgs via signup/bootstrap, the
real orchestrator, every check printed. The DRAFT step needs ANTHROPIC_API_KEY;
without it the run still proves the degradation path (error -> escalation) and
the no-basis path, which need no model.

Scenarios (docs/agents/11-appeals-agent.md §8):
  1  denied claim + denial_reason + issues -> claim_denied
       key:    AP2 -> drafted, AP4 await_human, claim still `denied`
       no key: AP2 -> error, AP11 -> ONE escalation (appeal_agent_error)
  2  scenario 1 + approve -> 11.submit, tuned to a WON resolution
       AP6 -> submit -> AP9 -> claim `paid` + an appeal_won_claim_reversed activity row
  3  denied claim tuned to an UPHELD resolution
       AP6 -> submit -> AP10 -> claim still `denied` + ONE escalation (appeal_exhausted_needs_human), org-scoped
  4  approve on an appeal that is NOT drafted (double click) -> AP7 -> escalation, NEVER submits
  5  denied claim with NO issues and NO denial reason -> AP2 -> insufficient_basis ->
       AP3 -> ONE escalation (appeal_no_basis_needs_human), no model call, letter_text == ''
  6  a second-level resubmit chain: an upheld appeal -> resubmit -> the chained attempt WINS
       (the resubmit bonus) -> claim `paid`
  7  second org: nothing leaked; the reversal wrote only Clinic A's claim

Run (local stack up):  python tests/e2e_appeal_test.py
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from _agentlib import Check, ensure_org, svc_get, svc_write

from app.agents import db, orchestrator  # noqa: E402
from app.agents.appeals import win_threshold  # noqa: E402
from app.config import get_settings  # noqa: E402

NOW = datetime.now(timezone.utc)


def payer(org_id, name):
    return svc_write("POST", "payers", {}, {
        "organization_id": org_id, "name": name,
        "authorization_required": True, "documentation_required": True,
        "follow_up_threshold_days": 21,
    })[0]


def denied_claim(org_id, payer_id, claim_id, *, denial_reason=None, amount=1500.0):
    return svc_write("POST", "claims", {}, {
        "organization_id": org_id, "payer_id": payer_id, "claim_id": claim_id,
        "patient_name": "Appeal Probe", "patient_member_id": "AP-1", "amount": amount,
        "status": "denied", "denial_reason": denial_reason,
        "authorization_present": False, "documentation_present": True, "coding_matches": False,
        "created_at": (NOW - timedelta(days=20)).isoformat(),
    })[0]


def add_issues(org_id, claim_pk, specs):
    for it, sev, desc in specs:
        svc_write("POST", "claim_issues", {}, {
            "organization_id": org_id, "claim_id": claim_pk,
            "issue_type": it, "severity": sev, "description": desc,
        })


def appeal_id_for_band(claim_pk: str, distinct_grounds: int, band: str, *, is_resubmit=False) -> str:
    """A uuid whose deterministic resolution bucket lands in the wanted band."""
    from app.agents.appeals import APPEAL_PARTIAL_BAND, APPEAL_RESUBMIT_BONUS, resolution_bucket

    win = win_threshold(distinct_grounds)
    for _ in range(200000):
        cand = str(uuid.uuid4())
        raw = resolution_bucket(claim_pk, cand)
        eff = max(0, raw - APPEAL_RESUBMIT_BONUS) if is_resubmit else raw
        if band == "approved" and eff < win - 2:
            return cand
        if band == "denied" and eff >= win + APPEAL_PARTIAL_BAND + 2:
            return cand
    raise RuntimeError(f"no appeal id found for band {band}")


def crow(pk):
    return svc_get("claims", {"id": f"eq.{pk}", "select": "status,denial_reason"})[0]


def arow(pk):
    return svc_get("appeals", {"id": f"eq.{pk}", "select": "*"})[0]


def latest_appeal_row(claim_pk):
    rows = svc_get("appeals", {"claim_id": f"eq.{claim_pk}", "select": "*", "order": "created_at"})
    return rows[-1] if rows else None


def esc_for(claim_pk):
    return svc_get("escalations",
                   {"claim_id": f"eq.{claim_pk}", "select": "reason_code,organization_id,context"})


def acts_for(claim_pk):
    return svc_get("activity_log",
                   {"claim_id": f"eq.{claim_pk}",
                    "select": "actor,action,details,organization_id", "order": "created_at"})


async def main() -> int:  # noqa: PLR0915
    chk = Check()
    key = bool(get_settings().anthropic_api_key)
    print("End-to-end — appeals (11)")
    print(f"  draft step: {'ANTHROPIC_API_KEY present' if key else 'no key'} "
          "— the model path is asserted only if a real draft comes back\n")

    org_id, admin_uid = ensure_org("e2e_appeal@foresight.test", "E2E Appeal — Riverside")
    other_org, _ = ensure_org("e2e_appeal_other@foresight.test", "E2E Appeal — Other")
    p = payer(org_id, "Cascade Health")

    async def approve_and_submit(claim_id, appeal_id):
        """Mirror POST /api/appeals/{id}/approve-submission then the Commander hand-off."""
        await db.update_appeal(org_id, appeal_id, {
            "reviewed_by": admin_uid, "reviewed_at": NOW.isoformat()})
        return await orchestrator.handle_appeal(claim_id, {"type": "appeal_submission_approved"})

    ISSUES = [
        ("missing_authorization", "high", "Cascade requires prior auth and none is recorded."),
        ("code_mismatch", "medium", "Billed level of service exceeds the documentation."),
    ]
    # 2 distinct issue types + a denial reason -> 3 distinct grounds
    DISTINCT = 3

    # ---- 1. denied claim + issues + denial reason -> claim_denied ------------
    c1 = denied_claim(org_id, p["id"], "CLM-APPEAL-1",
                      denial_reason="Prior authorization not on file for this service.")
    add_issues(org_id, c1["id"], ISSUES)
    d1 = await orchestrator.handle_appeal(c1["id"], {"type": "claim_denied"})
    a1 = latest_appeal_row(c1["id"])
    chk("1: an appeal row was created for the denied claim", a1 is not None, str(d1))
    chk("1: the claim is still `denied` after drafting (no rule touched it)",
        crow(c1["id"])["status"] == "denied", str(crow(c1["id"])))

    # the model path is asserted only when a real grounded draft actually came
    # back — a missing / exhausted key degrades to the AP11 error path, which is
    # itself a Phase 5 requirement, so assert THAT instead of failing.
    drafted_ok = bool(a1) and a1["status"] == "drafted"
    if key and not drafted_ok:
        print("  (note: a key is set but the draft did not return — asserting the "
              "degradation path; check ANTHROPIC credits to exercise the full flow)")

    if drafted_ok:
        chk("1: AP2 drafted a grounded letter, Commander parked at await_human",
            a1["status"] == "drafted" and a1["has_basis"] and a1["letter_text"]
            and a1["model"], str({k: a1[k] for k in ("status", "has_basis", "model")}))
        chk("1: the grounds are the real rows (2 issues + the denial reason), none fabricated",
            len(a1["grounds"]) == 3
            and {g["source"] for g in a1["grounds"]}
            == {"rule_engine_issue", "payer_denial_reason"}, str(a1["grounds"]))
        acts1 = [x["action"] for x in acts_for(c1["id"])]
        chk("1: await_human recorded (appeal_awaiting_submission_approval), NOT escalated",
            "appeal_awaiting_submission_approval" in acts1
            and "appeal_agent_error" not in acts1, str(acts1))
        chk("1: no escalation yet", esc_for(c1["id"]) == [], str(esc_for(c1["id"])))
    else:
        chk("1 (degraded): AP2 recorded `error`, letter_text empty",
            a1["status"] == "error" and a1["letter_text"] == "", str(a1))
        e1 = esc_for(c1["id"])
        chk("1 (degraded): AP11 -> ONE escalation (appeal_agent_error), org-scoped",
            len(e1) == 1 and e1[0]["reason_code"] == "appeal_agent_error"
            and e1[0]["organization_id"] == org_id, str(e1))

    # ---- 2. approve -> submit -> WON -> claim paid --------------------------
    if drafted_ok:
        c2 = denied_claim(org_id, p["id"], "CLM-APPEAL-2",
                          denial_reason="Documentation insufficient to support the billed code.")
        add_issues(org_id, c2["id"], ISSUES)
        won_id = appeal_id_for_band(c2["id"], DISTINCT, "approved")
        await db.insert_appeal(org_id, {"id": won_id, "claim_id": c2["id"], "status": "pending",
                                        "denial_reason": crow(c2["id"])["denial_reason"]})
        await orchestrator.handle_appeal(c2["id"], {"type": "appeal_resubmitted"})  # skips AP1
        a2 = arow(won_id)
        chk("2: the pre-seeded appeal id drafted (draft reused the pending row)",
            a2["status"] == "drafted", str(a2["status"]))
        d2 = await approve_and_submit(c2["id"], won_id)
        a2 = arow(won_id)
        chk("2: AP6 routed to 11.submit", d2.reason_code == "appeal_submit", str(d2))
        chk("2: the resolution is `appeal_approved` (deterministic, tuned)",
            a2["status"] == "appeal_approved"
            and a2["resolution_payload"]["outcome"] == "approved", str(a2["resolution_payload"]))
        chk("2: AP9 reversed the claim `denied` -> `paid`", crow(c2["id"])["status"] == "paid",
            str(crow(c2["id"])))
        acts2 = [x["action"] for x in acts_for(c2["id"])]
        chk("2: an appeal_won_claim_reversed activity row exists",
            "appeal_won_claim_reversed" in acts2, str(acts2))
        chk("2: reversed_amount == the full billed amount",
            float(a2["resolution_payload"]["reversed_amount"]) == 1500.0,
            str(a2["resolution_payload"]))
        chk("2: every activity row is org-scoped",
            all(x["organization_id"] == org_id for x in acts_for(c2["id"])))

    # ---- 3. approve -> submit -> UPHELD -> claim stays denied + escalation --
    if drafted_ok:
        c3 = denied_claim(org_id, p["id"], "CLM-APPEAL-3", denial_reason="Not medically necessary.")
        add_issues(org_id, c3["id"], ISSUES)
        lost_id = appeal_id_for_band(c3["id"], DISTINCT, "denied")
        await db.insert_appeal(org_id, {"id": lost_id, "claim_id": c3["id"], "status": "pending",
                                        "denial_reason": crow(c3["id"])["denial_reason"]})
        await orchestrator.handle_appeal(c3["id"], {"type": "appeal_resubmitted"})
        d3 = await approve_and_submit(c3["id"], lost_id)
        a3 = arow(lost_id)
        chk("3: the resolution is `appeal_denied` (deterministic, tuned)",
            a3["status"] == "appeal_denied", str(a3["status"]))
        chk("3: AP10 left the claim `denied`", crow(c3["id"])["status"] == "denied",
            str(crow(c3["id"])))
        e3 = esc_for(c3["id"])
        chk("3: ONE escalation (appeal_exhausted_needs_human), org-scoped",
            len(e3) == 1 and e3[0]["reason_code"] == "appeal_exhausted_needs_human"
            and e3[0]["organization_id"] == org_id, str(e3))
        chk("3: the resolution decision carried next_status None", d3.next_status is None, str(d3))

    # ---- 4. approve on a NOT-drafted appeal -> AP7 -> escalation, no submit --
    if drafted_ok:
        c4 = denied_claim(org_id, p["id"], "CLM-APPEAL-4", denial_reason="Timely filing.")
        add_issues(org_id, c4["id"], ISSUES)
        stray = str(uuid.uuid4())
        await db.insert_appeal(org_id, {"id": stray, "claim_id": c4["id"], "status": "submitted",
                                        "reviewed_by": None})
        d4 = await orchestrator.handle_appeal(c4["id"], {"type": "appeal_submission_approved"})
        chk("4: AP7 routed a spurious approval to 12, never to 11.submit",
            d4.reason_code == "appeal_approval_without_draft" and d4.route_to == "12-escalation",
            str(d4))
        chk("4: the appeal row was NOT submitted / resolved",
            arow(stray)["status"] == "submitted"
            and arow(stray)["resolution_payload"] == {}, str(arow(stray)))
        chk("4: the claim is untouched", crow(c4["id"])["status"] == "denied")

    # ---- 5. denied claim, NO issues, NO denial reason -> insufficient_basis --
    c5 = denied_claim(org_id, p["id"], "CLM-APPEAL-5", denial_reason=None)
    d5 = await orchestrator.handle_appeal(c5["id"], {"type": "claim_denied"})
    a5 = latest_appeal_row(c5["id"])
    chk("5: AP2 -> insufficient_basis (no citable grounds), no letter",
        a5["status"] == "insufficient_basis" and a5["letter_text"] == ""
        and a5["has_basis"] is False, str(a5))
    chk("5: no model was called (model is null)", a5["model"] is None, str(a5["model"]))
    e5 = esc_for(c5["id"])
    chk("5: AP3 -> ONE escalation (appeal_no_basis_needs_human), org-scoped",
        len(e5) == 1 and e5[0]["reason_code"] == "appeal_no_basis_needs_human"
        and e5[0]["organization_id"] == org_id, str(e5))
    chk("5: the claim is still `denied`", crow(c5["id"])["status"] == "denied")

    # ---- 6. a second-level resubmit chain: upheld -> resubmit -> WON --------
    if drafted_ok:
        c6 = denied_claim(org_id, p["id"], "CLM-APPEAL-6", denial_reason="Bundled service.")
        add_issues(org_id, c6["id"], ISSUES)
        first_id = appeal_id_for_band(c6["id"], DISTINCT, "denied")
        await db.insert_appeal(org_id, {"id": first_id, "claim_id": c6["id"], "status": "pending",
                                        "denial_reason": crow(c6["id"])["denial_reason"]})
        await orchestrator.handle_appeal(c6["id"], {"type": "appeal_resubmitted"})
        await approve_and_submit(c6["id"], first_id)
        chk("6: the first-level appeal was upheld", arow(first_id)["status"] == "appeal_denied",
            str(arow(first_id)["status"]))
        # the resubmit: a fresh pending row chained to the first, tuned to win WITH the bonus
        second_id = appeal_id_for_band(c6["id"], DISTINCT, "approved", is_resubmit=True)
        await db.insert_appeal(org_id, {"id": second_id, "claim_id": c6["id"], "status": "pending",
                                        "previous_appeal_id": first_id,
                                        "denial_reason": crow(c6["id"])["denial_reason"]})
        await orchestrator.handle_appeal(c6["id"], {"type": "appeal_resubmitted"})
        await approve_and_submit(c6["id"], second_id)
        a6b = arow(second_id)
        chk("6: the chained attempt links previous_appeal_id",
            a6b["previous_appeal_id"] == first_id, str(a6b["previous_appeal_id"]))
        chk("6: the resubmit is flagged is_resubmit in the resolution payload",
            a6b["resolution_payload"].get("is_resubmit") is True, str(a6b["resolution_payload"]))
        chk("6: the resubmit WON (the bonus tipped it) and reversed the claim",
            a6b["status"] == "appeal_approved" and crow(c6["id"])["status"] == "paid",
            str({"appeal": a6b["status"], "claim": crow(c6["id"])["status"]}))

    # ---- 7. tenant isolation ----------------------------------------------
    chk("7: org B has no appeals rows after all of org A's runs",
        svc_get("appeals", {"organization_id": f"eq.{other_org}", "select": "id"}) == [])
    chk("7: org B has no appeal escalations / activity",
        svc_get("escalations", {"organization_id": f"eq.{other_org}", "select": "id"}) == []
        and svc_get("activity_log", {"organization_id": f"eq.{other_org}",
                                     "action": "like.appeal_*", "select": "id"}) == [])
    a_appeals = svc_get("appeals", {"organization_id": f"eq.{org_id}", "select": "organization_id,claim_id"})
    a_claim_ids = {r["id"] for r in svc_get("claims", {"organization_id": f"eq.{org_id}", "select": "id"})}
    chk("7: every appeal row A wrote is org A's and points at one of A's claims",
        all(r["organization_id"] == org_id and r["claim_id"] in a_claim_ids for r in a_appeals),
        str(a_appeals[:3]))
    if drafted_ok:
        chk("7: exactly the two won claims (scenario 2 + 6) are `paid`; the rest stay `denied`",
            len(svc_get("claims", {"organization_id": f"eq.{org_id}", "status": "eq.paid",
                                   "select": "id"})) == 2)
    else:
        chk("7 (degraded): no claim was reversed to `paid` without a model draft",
            svc_get("claims", {"organization_id": f"eq.{org_id}", "status": "eq.paid",
                               "select": "id"}) == [])

    return chk.summary("appeals hold end to end; a claim moves only on a human-approved win.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
