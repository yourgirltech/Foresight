#!/usr/bin/env python3
"""06 — analyzer-agent unit tests: the deterministic risk formula.

Contract under test (docs/architecture.md § Risk scoring):
    weight   low=10  medium=30  high=50
    risk_score = min(100, sum of weights)
    risk_level = High >=70 / Medium 40-69 / Low <40
    severity   missing_authorization=high  missing_documentation=high
               code_mismatch=medium        overdue_follow_up=low

Stdlib only. Run:  python tests/rules_test.py
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.rules import analyze, risk_level_for  # noqa: E402

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)
_PASS = _FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    mark = "PASS" if ok else "FAIL"
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    line = f"  [{mark}] {name}"
    if detail and not ok:
        line += f"\n         -> {detail}"
    print(line)


def claim(**over) -> dict:
    base = {
        "claim_id": "CLM-T",
        "patient_name": "Test Patient",
        "amount": 100,
        "authorization_present": True,
        "documentation_present": True,
        "coding_matches": True,
        "last_followup_at": None,
        "created_at": NOW.isoformat(),
    }
    base.update(over)
    return base


def payer(**over) -> dict:
    base = {
        "name": "Test Payer",
        "authorization_required": False,
        "documentation_required": False,
        "follow_up_threshold_days": None,
    }
    base.update(over)
    return base


def run() -> int:
    print("06 analyzer — risk formula\n")

    r = analyze(claim(), payer(), now=NOW)
    check("clean claim -> 0 issues, score 0, Low",
          r.issues == [] and r.risk_score == 0 and r.risk_level == "Low", str(r))

    # --- single issues ---
    r = analyze(claim(authorization_present=False), payer(authorization_required=True), now=NOW)
    check("missing_authorization -> high(50), Medium",
          [i.issue_type for i in r.issues] == ["missing_authorization"]
          and r.issues[0].severity == "high" and r.risk_score == 50 and r.risk_level == "Medium", str(r))

    r = analyze(claim(documentation_present=False), payer(documentation_required=True), now=NOW)
    check("missing_documentation -> high(50), Medium",
          [i.issue_type for i in r.issues] == ["missing_documentation"]
          and r.issues[0].severity == "high" and r.risk_score == 50, str(r))

    r = analyze(claim(coding_matches=False), payer(), now=NOW)
    check("code_mismatch -> medium(30), Low",
          [i.issue_type for i in r.issues] == ["code_mismatch"]
          and r.issues[0].severity == "medium" and r.risk_score == 30 and r.risk_level == "Low", str(r))

    old = (NOW - timedelta(days=40)).isoformat()
    r = analyze(claim(created_at=old), payer(follow_up_threshold_days=14), now=NOW)
    check("overdue_follow_up (anchored on created_at) -> low(10), Low",
          [i.issue_type for i in r.issues] == ["overdue_follow_up"]
          and r.issues[0].severity == "low" and r.risk_score == 10, str(r))

    # --- combinations / thresholds ---
    r = analyze(claim(coding_matches=False, created_at=old), payer(follow_up_threshold_days=14), now=NOW)
    check("code_mismatch + overdue -> 40, Medium (boundary)",
          r.risk_score == 40 and r.risk_level == "Medium", str(r))

    r = analyze(claim(authorization_present=False, coding_matches=False),
                payer(authorization_required=True), now=NOW)
    check("missing_auth + code_mismatch -> 80, High",
          r.risk_score == 80 and r.risk_level == "High", str(r))

    r = analyze(claim(authorization_present=False, documentation_present=False),
                payer(authorization_required=True, documentation_required=True), now=NOW)
    check("missing_auth + missing_doc -> 100 (capped), High",
          r.risk_score == 100 and r.risk_level == "High", str(r))

    r = analyze(claim(authorization_present=False, documentation_present=False,
                      coding_matches=False, created_at=old),
                payer(authorization_required=True, documentation_required=True,
                      follow_up_threshold_days=14), now=NOW)
    check("all four issues -> 100 (capped), High, 4 issues",
          r.risk_score == 100 and r.risk_level == "High" and len(r.issues) == 4, str(r))

    check("risk_level_for boundaries: 39->Low, 40->Medium, 69->Medium, 70->High",
          risk_level_for(39) == "Low" and risk_level_for(40) == "Medium"
          and risk_level_for(69) == "Medium" and risk_level_for(70) == "High")

    # --- payer config gates the check ---
    r = analyze(claim(authorization_present=False), payer(authorization_required=False), now=NOW)
    check("payer does NOT require auth -> no missing_authorization issue",
          r.issues == [], str(r))

    r = analyze(claim(documentation_present=False), payer(documentation_required=False), now=NOW)
    check("payer does NOT require docs -> no missing_documentation issue", r.issues == [], str(r))

    r = analyze(claim(created_at=old), payer(follow_up_threshold_days=None), now=NOW)
    check("no follow_up_threshold -> no overdue_follow_up issue", r.issues == [], str(r))

    recent = (NOW - timedelta(days=5)).isoformat()
    r = analyze(claim(created_at=old, last_followup_at=recent), payer(follow_up_threshold_days=14), now=NOW)
    check("recent last_followup_at within threshold -> no overdue issue", r.issues == [], str(r))

    print()
    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — 06 risk formula holds.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
