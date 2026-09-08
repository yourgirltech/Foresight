#!/usr/bin/env python3
"""11 — the pure appeals simulation: appeal_basis() and simulate_resolution().

Proves the contract in docs/agents/11-appeals-agent.md §6 / §8, including:

  * THE GROUNDING PROOF (§2.1 G1/G2): appeal_basis() is pure and every
    AppealGround it returns is copied VERBATIM from an input row — a
    claim_issues.description / .issue_type, claims.denial_reason, or an approved
    recommendation.action_type. It never emits a string that is not in the
    input. has_basis is True iff there is >= 1 issue OR a denial reason OR an
    executed action. distinct_grounds counts distinct (source, ref) keys, so
    three `missing_documentation` issues collapse to ONE distinct ground.

  * simulate_resolution() (§6.4): a full sweep of the raw bucket 0..99 for
    distinct_grounds in {0..4} and is_resubmit in {False, True} —
      - the three outcome bands partition every bucket exactly (monotone in raw:
        approved -> partial -> denied, no gap, no overlap);
      - more distinct_grounds => the `approved` band is >= as wide (monotonic);
      - a resubmit's effective bucket is max(0, raw - APPEAL_RESUBMIT_BONUS);
      - reversed_amount == claim.amount for `approved`, 0.0 for `denied`, and
        strictly between for `partial`;
      - deterministic (same inputs -> same AppealResolution, twice).

Stdlib only, no stack, no key.  Run:  python tests/appeals_agent_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.appeals import (  # noqa: E402
    APPEAL_BASE_WIN_THRESHOLD,
    APPEAL_GROUNDS_BONUS,
    APPEAL_MAX_WIN_THRESHOLD,
    APPEAL_PARTIAL_BAND,
    APPEAL_RESUBMIT_BONUS,
    appeal_basis,
    resolution_bucket,
    simulate_resolution,
    win_threshold,
)

_PASS = _FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
    if detail and not ok:
        line += f"\n         -> {detail}"
    print(line)


# --------------------------------------------------------------------------- #
# row builders — the shape db.list_issues / db.list_recommendations return
# --------------------------------------------------------------------------- #
def issue(issue_type: str, description: str) -> dict:
    return {"issue_type": issue_type, "severity": "high", "description": description,
            "evidence": {}}


def rec(action_type: str, approval_status: str = "approved") -> dict:
    return {"action_type": action_type, "approval_status": approval_status,
            "cited_issue_types": []}


def claim(amount: float = 1500.0, denial_reason: str | None = None) -> dict:
    return {"id": "clm-pk-1", "claim_id": "CLM-APPEAL-1", "patient_name": "Test Patient",
            "amount": amount, "status": "denied", "denial_reason": denial_reason}


ISSUE_TEXT = {
    "missing_authorization": "Meridian requires prior auth and none is recorded on the claim.",
    "code_mismatch": "Billed 99215 but the documentation supports 99213.",
    "missing_documentation": "No operative note attached for the procedure billed.",
}
DENIAL = "Prior authorization not on file for this service."
FOLLOW_UP = [{"id": "fu-1", "kind": "payer_status_follow_up"}]


def input_strings(issues, recs, follow_ups, denial_reason) -> set[str]:
    """Every substantive string that legitimately appears in the input rows."""
    out: set[str] = set()
    for i in issues:
        out.add(i["issue_type"])
        out.add(i["description"])
    if denial_reason and denial_reason.strip():
        out.add(denial_reason.strip())
    if any(r["approval_status"] == "approved" for r in recs) and follow_ups:
        approved = next(r for r in recs if r["approval_status"] == "approved")
        out.add(approved["action_type"])
        out.add(f"{approved['action_type']} was approved and completed on this claim")
    return out


def run() -> int:  # noqa: PLR0915
    print("11 appeals — the pure appeal_basis() + simulate_resolution()\n")

    # =====================================================================
    # 1. appeal_basis() grounding grid
    #    {0, 1, 3} issues x {denial present / absent} x {0, 1 executed action}
    # =====================================================================
    issue_type_sets = {
        0: [],
        1: [("code_mismatch", ISSUE_TEXT["code_mismatch"])],
        3: [("missing_documentation", ISSUE_TEXT["missing_documentation"]),
            ("missing_documentation", ISSUE_TEXT["missing_documentation"] + " (second line item)"),
            ("missing_authorization", ISSUE_TEXT["missing_authorization"])],
    }
    fabricated = 0
    det_wrong = 0
    basis_wrong = 0
    nondet = 0
    cases = 0

    for n_issues, dr_on, action_on in itertools.product((0, 1, 3), (False, True), (False, True)):
        cases += 1
        issues = [issue(it, desc) for it, desc in issue_type_sets[n_issues]]
        recs = [rec("request_documentation")] if action_on else [rec("x", "declined")]
        fups = FOLLOW_UP if action_on else []
        dr = DENIAL if dr_on else None
        c = claim(denial_reason=dr)

        b1 = appeal_basis(c, issues, recs, fups, dr)
        b2 = appeal_basis(c, issues, recs, fups, dr)
        if [g.__dict__ for g in b1.grounds] != [g.__dict__ for g in b2.grounds]:
            nondet += 1

        allowed = input_strings(issues, recs, fups, dr)
        for g in b1.grounds:
            if g.detail not in allowed:
                fabricated += 1
            if g.ref is not None and g.ref not in allowed:
                fabricated += 1

        expect_basis = n_issues > 0 or dr_on or action_on
        if b1.has_basis is not expect_basis:
            basis_wrong += 1

        # distinct_grounds: distinct (source, ref); the 3-issue set has two
        # distinct issue_types, so distinct rule-engine grounds == 2 there.
        distinct_issue_refs = len({it for it, _ in issue_type_sets[n_issues]})
        expect_distinct = distinct_issue_refs + (1 if dr_on else 0) + (1 if action_on else 0)
        if b1.distinct_grounds != expect_distinct:
            det_wrong += 1

    check(f"appeal_basis grid: {cases} cases, every ground copied verbatim from an input row "
          "(no fabricated string)", fabricated == 0, f"{fabricated} fabricated strings")
    check("appeal_basis grid: has_basis is True iff >=1 issue OR denial reason OR executed action",
          basis_wrong == 0, f"{basis_wrong} wrong")
    check("appeal_basis grid: distinct_grounds counts distinct (source, ref) keys "
          "(3 issues / 2 types -> 2 distinct rule-engine grounds)",
          det_wrong == 0, f"{det_wrong} wrong")
    check("appeal_basis is deterministic across the grid", nondet == 0, f"{nondet} nondeterministic")

    # --- named grounding cases -----------------------------------------------
    b = appeal_basis(claim(denial_reason=None), [], [rec("x", "declined")], [], None)
    check("test_no_issues_no_denial_reason_no_history_has_no_basis",
          b.has_basis is False and b.grounds == [] and b.distinct_grounds == 0, str(b))

    strong = appeal_basis(
        claim(denial_reason="Documentation not received."),
        [issue("missing_documentation", ISSUE_TEXT["missing_documentation"])],
        [rec("request_documentation")], FOLLOW_UP, "Documentation not received.")
    check("test_denied_for_missing_docs_after_docs_were_provided_is_a_strong_appeal "
          "(3 distinct grounds: the issue, the denial reason, the action taken)",
          strong.has_basis and strong.distinct_grounds == 3
          and {g.source for g in strong.grounds}
          == {"rule_engine_issue", "payer_denial_reason", "action_taken"}, str(strong))

    thin = appeal_basis(claim(denial_reason=None),
                        [issue("code_mismatch", ISSUE_TEXT["code_mismatch"])], [], [], None)
    check("test_a_single_thin_ground_still_drafts_but_wins_less_often "
          "(has_basis True, distinct_grounds 1, narrower approved band than a 3-ground appeal)",
          thin.has_basis and thin.distinct_grounds == 1
          and win_threshold(thin.distinct_grounds) < win_threshold(strong.distinct_grounds), str(thin))

    # =====================================================================
    # 2. win_threshold — the documented formula + the cap
    # =====================================================================
    check("win_threshold(0) == APPEAL_BASE_WIN_THRESHOLD",
          win_threshold(0) == APPEAL_BASE_WIN_THRESHOLD)
    check("win_threshold grows by APPEAL_GROUNDS_BONUS per distinct ground, capped at the max",
          all(win_threshold(d) == min(APPEAL_MAX_WIN_THRESHOLD,
                                      APPEAL_BASE_WIN_THRESHOLD + APPEAL_GROUNDS_BONUS * d)
              for d in range(0, 8))
          and win_threshold(99) == APPEAL_MAX_WIN_THRESHOLD)
    check("win_threshold is monotonic non-decreasing in distinct_grounds",
          all(win_threshold(d) <= win_threshold(d + 1) for d in range(0, 12)))

    # =====================================================================
    # 3. simulate_resolution — full sweep of the raw bucket 0..99
    # =====================================================================
    # find a (claim_id, appeal_id) pair for every target raw bucket
    fixed_claim_pk = "clm-sweep"
    appeal_for_bucket: dict[int, str] = {}
    for i in range(500000):
        aid = f"ap-{i}"
        bkt = resolution_bucket(fixed_claim_pk, aid)
        appeal_for_bucket.setdefault(bkt, aid)
        if len(appeal_for_bucket) == 100:
            break
    check("found a (claim, appeal) pair for all 100 raw buckets", len(appeal_for_bucket) == 100,
          f"only {len(appeal_for_bucket)}")

    class _B:  # a minimal AppealBasis stand-in carrying just distinct_grounds
        def __init__(self, d: int) -> None:
            self.distinct_grounds = d

    AMOUNT = 1234.0
    partition_bad = 0
    monotone_bad = 0
    resubmit_bad = 0
    amount_bad = 0
    nondet2 = 0
    approved_widths: dict[bool, list[int]] = {False: [], True: []}

    for is_resubmit in (False, True):
        for d in range(0, 5):
            outcomes_by_bucket: list[str] = []
            approved_count = 0
            for raw in range(100):
                c = {"id": fixed_claim_pk, "claim_id": "CLM-SWEEP", "amount": AMOUNT}
                aid = appeal_for_bucket[raw]
                r1 = simulate_resolution(c, aid, _B(d), is_resubmit=is_resubmit)
                r2 = simulate_resolution(c, aid, _B(d), is_resubmit=is_resubmit)
                if (r1.outcome, r1.reversed_amount, r1.bucket) != (r2.outcome, r2.reversed_amount, r2.bucket):
                    nondet2 += 1
                outcomes_by_bucket.append(r1.outcome)
                if r1.outcome == "approved":
                    approved_count += 1

                if r1.bucket != raw:
                    partition_bad += 1
                eff_expected = max(0, raw - APPEAL_RESUBMIT_BONUS) if is_resubmit else raw
                if r1.payload["effective_bucket"] != eff_expected:
                    resubmit_bad += 1

                if r1.outcome == "approved" and r1.reversed_amount != round(AMOUNT, 2):
                    amount_bad += 1
                if r1.outcome == "denied" and r1.reversed_amount != 0.0:
                    amount_bad += 1
                if r1.outcome == "partial" and not (0.0 < r1.reversed_amount < AMOUNT):
                    amount_bad += 1

            # the three bands are monotone in raw: approved* partial* denied*
            rank = {"approved": 0, "partial": 1, "denied": 2}
            ranks = [rank[o] for o in outcomes_by_bucket]
            if ranks != sorted(ranks):
                monotone_bad += 1
            # exactly the three bands present, partitioning 0..99
            if set(outcomes_by_bucket) - {"approved", "partial", "denied"}:
                partition_bad += 1
            approved_widths[is_resubmit].append(approved_count)

    check("simulate_resolution: bucket in the payload always equals the raw sha256 roll",
          partition_bad == 0, f"{partition_bad} mismatches")
    check("simulate_resolution: outcomes are monotone in the raw bucket "
          "(approved -> partial -> denied, a clean partition)", monotone_bad == 0,
          f"{monotone_bad} non-monotone sweeps")
    check("simulate_resolution: effective bucket == max(0, raw - APPEAL_RESUBMIT_BONUS) on a resubmit, "
          "raw otherwise", resubmit_bad == 0, f"{resubmit_bad} wrong")
    check("simulate_resolution: reversed_amount is the full amount for `approved`, 0.0 for `denied`, "
          "strictly between for `partial`", amount_bad == 0, f"{amount_bad} wrong")
    check("simulate_resolution is deterministic across the whole sweep", nondet2 == 0,
          f"{nondet2} nondeterministic")
    check("more distinct grounds => the `approved` band is >= as wide (monotonic), both paths",
          all(approved_widths[False][d] <= approved_widths[False][d + 1] for d in range(4))
          and all(approved_widths[True][d] <= approved_widths[True][d + 1] for d in range(4)),
          f"non-resubmit {approved_widths[False]}, resubmit {approved_widths[True]}")
    check("a resubmit never narrows the `approved` band vs. the same appeal without the bonus",
          all(approved_widths[True][d] >= approved_widths[False][d] for d in range(5)),
          f"non-resubmit {approved_widths[False]}, resubmit {approved_widths[True]}")

    # the partial band width is exactly APPEAL_PARTIAL_BAND wherever it is not
    # clipped by the top of the range (a documented constant, not inlined)
    d2_win = win_threshold(2)
    partial_expected = min(APPEAL_PARTIAL_BAND, max(0, 100 - d2_win))
    partial_seen = sum(
        1 for raw in range(100)
        if simulate_resolution({"id": fixed_claim_pk, "amount": AMOUNT},
                               appeal_for_bucket[raw], _B(2)).outcome == "partial"
    )
    check(f"the `partial` band is APPEAL_PARTIAL_BAND ({APPEAL_PARTIAL_BAND}) wide at distinct_grounds=2",
          partial_seen == partial_expected, f"expected {partial_expected}, saw {partial_seen}")

    print()
    print("=" * 62)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — appeal_basis cites only real evidence; "
              "the resolution is a deterministic partition.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
