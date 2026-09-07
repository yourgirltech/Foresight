#!/usr/bin/env python3
"""05 — opt-in live phrasing test.

Calls phrase() (real ANTHROPIC_API_KEY) on a FIXED, pre-computed priced estimate
and asserts the wording contract (agent doc §6.4 / test plan §9):

  * the returned paragraph is non-empty;
  * it contains the EXACT computed total string and NO other dollar figure —
    the model never introduced or recomputed a number;
  * it mentions neither "insurance" nor "copay"/"coverage"/"deductible"
    (the patient is self-pay);
  * it says this is an estimate, not a bill.

Skipped unless --live is passed AND ANTHROPIC_API_KEY is set. No stack needed.

Run:  python tests/cost_estimate_live_test.py --live
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


async def main() -> int:
    if "--live" not in sys.argv:
        print("05 live phrasing test — SKIPPED (pass --live to call the real model)")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        env = pathlib.Path(__file__).resolve().parents[1] / "backend" / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "ANTHROPIC_API_KEY":
                    os.environ["ANTHROPIC_API_KEY"] = v.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("05 live phrasing test — SKIPPED (--live given but no ANTHROPIC_API_KEY)")
        return 0

    from app.agents.cost_estimate import EstimateLine, PricedEstimate, phrase  # noqa: E402, PLC0415
    from app.config import get_settings  # noqa: E402, PLC0415

    print(f"05 live phrasing — model {get_settings().cost_estimate_model}\n")

    priced = PricedEstimate(
        lines=[
            EstimateLine("99214", "Office visit, established patient (moderate)", 210.00),
            EstimateLine("71046", "Chest X-ray, 2 views", 135.00),
            EstimateLine("36415", "Routine venipuncture", 15.25),
        ],
        subtotal=360.25,
        unpriced_codes=[],
    )
    summary, model = await phrase(priced, patient_name="Riley Sample", clinic_name="Cedar Street Clinic")
    print(f"  model: {model}\n  --- summary ---\n{summary}\n  ---------------\n")

    check("summary is non-empty", len(summary.strip()) > 40, summary)
    check("summary contains the exact computed total ($360.25)", "360.25" in summary, summary)

    dollars = set(re.findall(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)", summary))
    normalised = {d.replace(",", "").rstrip("0").rstrip(".") for d in dollars}
    check("summary contains NO dollar figure other than the total",
          normalised <= {"360.25", "360"}, f"found {dollars}")

    low = summary.lower()
    check("summary does not mention insurance / coverage / copay / deductible",
          not any(w in low for w in ("insurance", "coverage", "copay", "co-pay", "deductible")),
          summary)
    check("summary says this is an estimate, not a bill",
          "estimate" in low and "bill" in low, summary)

    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} LIVE CHECKS PASSED — the model phrases the number, never invents one.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
