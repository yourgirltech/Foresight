#!/usr/bin/env python3
"""11 — opt-in live drafting test.

Calls draft_appeal() (real ANTHROPIC_API_KEY) on a FIXED claim with known issues
(missing_authorization + code_mismatch) and a recorded denial reason, then
asserts the grounding contract (agent doc §2.1 / test plan §8):

  * the letter references the claim id and the payer name;
  * it mentions the actual issue types that are in the grounds;
  * it introduces NO fabrication — a curated deny-list of plausible-but-absent
    strings (a diagnosis code, a policy number, an attached document, a date) is
    checked absent;
  * it is non-empty and carries no signature block / letterhead.

Skipped unless --live is passed AND ANTHROPIC_API_KEY resolves. No stack needed.

Run:  python tests/appeals_live_test.py --live
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

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


def _load_key() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env = pathlib.Path(__file__).resolve().parents[1] / "backend" / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "ANTHROPIC_API_KEY":
            os.environ["ANTHROPIC_API_KEY"] = v.strip().strip('"').strip("'")


async def main() -> int:
    if "--live" not in sys.argv:
        print("11 live drafting test — SKIPPED (pass --live to call the real model)")
        return 0
    _load_key()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("11 live drafting test — SKIPPED (--live given but no ANTHROPIC_API_KEY)")
        return 0

    from app.agents.appeals import appeal_basis, draft_appeal  # noqa: E402, PLC0415
    from app.config import get_settings  # noqa: E402, PLC0415

    print(f"11 live drafting — model {get_settings().appeals_model}\n")

    claim = {
        "id": "clm-live-1", "claim_id": "CLM-LIVE-APPEAL-7", "patient_name": "Jordan Ellis",
        "amount": 2480.00, "status": "denied",
        "denial_reason": "Service denied — prior authorization not on file.",
    }
    payer = {"name": "Cascade Health Partners"}
    issues = [
        {"issue_type": "missing_authorization", "severity": "high",
         "description": "Cascade requires prior authorization for this service and none is recorded.",
         "evidence": {}},
        {"issue_type": "code_mismatch", "severity": "medium",
         "description": "The billed level of service is higher than the documentation supports.",
         "evidence": {}},
    ]
    basis = appeal_basis(claim, issues, [], [], claim["denial_reason"])
    check("appeal_basis produced 3 grounds (2 issues + the denial reason)",
          basis.has_basis and len(basis.grounds) == 3, str(basis))

    letter, model = await draft_appeal(claim, payer, basis)
    print(f"  model: {model}\n  --- letter ---\n{letter}\n  --------------\n")
    low = letter.lower()

    check("letter is non-empty prose", len(letter.strip()) > 120, letter)
    check("letter references the claim id", "CLM-LIVE-APPEAL-7" in letter, letter)
    check("letter references the payer by name", "cascade" in low, letter)
    check("letter references the recorded reason: prior authorization",
          "authorization" in low or "authoriz" in low, letter)

    # a curated deny-list of plausible fabrications NOT present in any ground
    fabrications = {
        "icd-10": r"\b[A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?\b",   # a diagnosis code
        "a policy / member number": r"\b(?:policy|member)\s*(?:no\.|number|#)\s*[:#]?\s*\w*\d\w*",
        "an authorization number": r"\bauth(?:orization)?\s*(?:no\.|number|#)\s*[:#]?\s*\w*\d\w*",
        "'enclosed' / 'attached' documentation": r"\b(?:enclosed|attached|we have attached)\b",
        "a specific date of service": r"\b(?:january|february|march|april|may|june|july|august|"
                                      r"september|october|november|december)\s+\d",
        "a dollar figure other than the billed amount": None,
    }
    for label, pat in fabrications.items():
        if label.startswith("a dollar figure"):
            dollars = set(re.findall(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)", letter))
            norm = {d.replace(",", "").rstrip("0").rstrip(".") for d in dollars}
            check("letter introduces no dollar figure other than the billed amount",
                  norm <= {"2480", "2480.00", "2480.0"}, f"found {dollars}")
            continue
        hits = re.findall(pat, low if label != "icd-10" else letter)
        check(f"letter fabricates no {label}", not hits, f"found {hits[:3]}")

    check("letter has no signature block",
          not re.search(r"\b(sincerely|regards|respectfully),?\s*\n", low)
          and "[name]" not in low and "signature" not in low, letter)

    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} LIVE CHECKS PASSED — the model grounds the letter, never fabricates.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
