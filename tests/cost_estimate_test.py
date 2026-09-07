#!/usr/bin/env python3
"""05 — the pure cost-estimate contract: price(), gate_reason(), plain_template(),
and the NSA disclaimer constant.

Proves docs/agents/05-cost-estimate-agent.md §6, §7, §9:

  * price() arithmetic over {known / unknown / duplicate / blank / empty} codes —
    subtotal is exactly Σ base_price of the priced lines, unpriced_codes is
    exactly the unknown set, duplicates are each priced, rounding is 2dp,
    deterministic;
  * THE NSA GATE — the full (self_pay x has_active_coverage) grid: allowed ONLY
    for an affirmed self-pay patient with no active coverage;
  * plain_template() carries the exact computed number;
  * NSA_GFE_DISCLAIMER is non-empty, names the $400 dispute threshold, the word
    "dispute", and the 120-day window; its version string is exported.

Stdlib only. Run:  python tests/cost_estimate_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.cost_estimate import (  # noqa: E402
    GFE_DISPUTE_THRESHOLD_USD,
    GFE_DISPUTE_WINDOW_DAYS,
    NSA_GFE_DISCLAIMER,
    NSA_GFE_DISCLAIMER_VERSION,
    gate_reason,
    plain_template,
    price,
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


PRICE_ROWS = [
    {"procedure_code": "99213", "description": "Office visit, established patient", "base_price": 145.00, "active": True},
    {"procedure_code": "72148", "description": "MRI lumbar spine without contrast", "base_price": 1200.50, "active": True},
    {"procedure_code": "36415", "description": "Routine venipuncture", "base_price": 15.25, "active": True},
    {"procedure_code": "80053", "description": "Comprehensive metabolic panel", "base_price": 49.99, "active": True},
    {"procedure_code": "OLD01", "description": "Retired code", "base_price": 500.00, "active": False},
]


def test_price() -> None:
    print("  -- price() arithmetic --")

    e = price(["99213"], PRICE_ROWS)
    check("single known code -> subtotal == its base_price",
          e.subtotal == 145.00 and len(e.lines) == 1 and e.unpriced_codes == [], str(e))

    e = price(["99213", "72148", "36415"], PRICE_ROWS)
    check("three known codes -> subtotal is the exact sum (2dp)",
          e.subtotal == round(145.00 + 1200.50 + 15.25, 2) == 1360.75, str(e.subtotal))

    e = price(["99213", "ZZZ99", "99999"], PRICE_ROWS)
    check("unknown codes are excluded from the subtotal and listed",
          e.subtotal == 145.00 and e.unpriced_codes == ["ZZZ99", "99999"], str(e))

    e = price(["99213", "99213", "36415"], PRICE_ROWS)
    check("a duplicate code is priced each time it appears",
          len(e.lines) == 3 and e.subtotal == round(145.00 + 145.00 + 15.25, 2), str(e))

    e = price(["", "  ", "99213"], PRICE_ROWS)
    check("blank / whitespace codes are ignored (not unpriced, not priced)",
          e.subtotal == 145.00 and e.unpriced_codes == [] and len(e.lines) == 1, str(e))

    e = price([], PRICE_ROWS)
    check("empty code list -> subtotal 0.00, no lines", e.subtotal == 0 and e.lines == [], str(e))

    e = price(["OLD01"], PRICE_ROWS)
    check("an inactive price row does NOT price the code (it is unpriced)",
          e.subtotal == 0 and e.unpriced_codes == ["OLD01"], str(e))

    e = price(["ZZZ99", "ZZZ99"], PRICE_ROWS)
    check("a repeated unknown code is listed once", e.unpriced_codes == ["ZZZ99"], str(e))

    a = price(["99213", "72148"], PRICE_ROWS)
    b = price(["99213", "72148"], PRICE_ROWS)
    check("price() is deterministic",
          (a.subtotal, [line.as_dict() for line in a.lines])
          == (b.subtotal, [line.as_dict() for line in b.lines]))

    # a rounding-sensitive combination
    e = price(["36415", "80053"], PRICE_ROWS)
    check("subtotal rounds to 2dp (15.25 + 49.99 = 65.24)", e.subtotal == 65.24, str(e.subtotal))


def test_gate() -> None:
    print("  -- the NSA gate: (self_pay x has_active_coverage), exhaustive --")
    covered = [{"payer_name": "Northwind Health"}]
    grid = {
        (False, False): "refused",   # no affirmation
        (False, True): "refused",
        (True, True): "refused",     # patient has coverage
        (True, False): "allowed",    # the only allowed cell
    }
    for (self_pay, has_cov), expected in grid.items():
        reason = gate_reason(self_pay, covered if has_cov else [])
        got = "allowed" if reason is None else "refused"
        check(f"self_pay={self_pay}, active_coverage={has_cov} -> {expected}",
              got == expected, f"got {got!r}: {reason!r}")

    check("refusal for no affirmation mentions confirming self-pay",
          "self-pay" in (gate_reason(False, []) or "").lower())
    r = gate_reason(True, covered) or ""
    check("refusal for a covered patient names the payer and 'No Surprises Act'",
          "Northwind Health" in r and "No Surprises Act" in r, r)

    # determinism + a few coverage shapes
    for cov_list in ([], [{"payer_name": "X"}], [{"payer_name": ""}, {"payer_name": "Y"}]):
        check(f"gate_reason deterministic for {cov_list}",
              gate_reason(True, cov_list) == gate_reason(True, cov_list))
    # an active-coverage list with only blank payer names still refuses, with a fallback label
    r = gate_reason(True, [{"payer_name": ""}]) or ""
    check("a coverage row with no payer name still refuses (fallback label)",
          r != "" and "active plan" in r, r)


def test_plain_template() -> None:
    print("  -- plain_template() --")
    e = price(["99213", "72148"], PRICE_ROWS)
    t = plain_template(e, patient_name="Jordan Blake")
    check("plain template contains the exact computed subtotal string",
          "$1,345.50" in t, t)
    check("plain template says it is an estimate, not a bill", "not a bill" in t.lower(), t)
    check("plain template contains no other dollar figure",
          t.count("$") == 1, t)
    single = plain_template(price(["99213"], PRICE_ROWS))
    check("plain template works with no patient name", "$145.00" in single, single)


def test_disclaimer() -> None:
    print("  -- NSA_GFE_DISCLAIMER (the versioned compliance constant) --")
    d = NSA_GFE_DISCLAIMER
    check("disclaimer is non-empty and substantial", len(d.strip()) > 400, str(len(d)))
    check("disclaimer names the $400 dispute threshold",
          "$400" in d and str(GFE_DISPUTE_THRESHOLD_USD) in d, "")
    check("disclaimer uses the word 'dispute'", "dispute" in d.lower())
    check(f"disclaimer names the {GFE_DISPUTE_WINDOW_DAYS}-day dispute window",
          f"{GFE_DISPUTE_WINDOW_DAYS} days" in d)
    check("disclaimer says it is not a contract", "not a contract" in d.lower())
    check("disclaimer tells the patient to keep a copy", "keep a copy" in d.lower())
    check("disclaimer points to cms.gov/nosurprises", "cms.gov/nosurprises" in d.lower())
    check("version string is set and looks like a date-stamped id",
          NSA_GFE_DISCLAIMER_VERSION.startswith("nsa-gfe-") and len(NSA_GFE_DISCLAIMER_VERSION) >= 12,
          NSA_GFE_DISCLAIMER_VERSION)
    # the code comment / doc flags legal review — assert the marker is present in the source
    src = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app" / "agents" / "cost_estimate.py"
    text = src.read_text(encoding="utf-8")
    check("cost_estimate.py flags the disclaimer for legal sign-off before production",
          "LEGAL SIGN-OFF BEFORE PRODUCTION" in text.upper())


def run() -> int:
    print("05 cost estimate — the pure pricing + gate + disclaimer contract\n")
    test_price()
    test_gate()
    test_plain_template()
    test_disclaimer()
    print()
    print("=" * 62)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — the number is deterministic, the gate is "
              "self-pay-only, the disclaimer is a verbatim versioned constant.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
