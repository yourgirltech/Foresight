#!/usr/bin/env python3
"""03 — the pure confidence gate: classify_extraction().

Proves docs/agents/03-ocr-agent.md §6.4 / §O5 exhaustively. The gate is the
whole safety story for 03: it decides whether an extraction is safe for the UI
to pre-fill (`extracted`) or whether a human must fill a blank (`needs_review`).
A wrong `extracted` is the dangerous outcome, so the test sweeps the full grid.

No stack, no API key, no network. Run:  python tests/ocr_gate_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.ocr import (  # noqa: E402
    FIELDS,
    FLOOR_RANK,
    CardExtraction,
    classify_extraction,
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


def make(**overrides) -> CardExtraction:
    """A clean, all-high extraction; override one field's value/meta via
    e.g. make(member_id=(None, {'legible': False, 'confidence': 'low'}))."""
    fields, confidence = {}, {}
    for f in FIELDS:
        if f in overrides:
            value, meta = overrides[f]
        else:
            value, meta = f"{f}-VALUE", {}
        fields[f] = value
        confidence[f] = {
            "confidence": meta.get("confidence", "high"),
            "legible": meta.get("legible", value is not None),
            "absent": meta.get("absent", False),
        }
    return CardExtraction(fields=fields, confidence=confidence)


CONF = ("high", "medium", "low")
FLOORS = ("high", "medium", "low")


def run() -> int:
    print("03 OCR — the pure confidence gate classify_extraction()\n")

    # --- 1. the clean card ------------------------------------------------------
    clean = make()
    check("test_clean_card_is_extracted", classify_extraction(clean) == "extracted",
          classify_extraction(clean))
    check("deterministic (same input -> same status, twice)",
          classify_extraction(clean) == classify_extraction(clean))

    # --- 2. FULL GRID: one field degraded at a time --------------------------
    # for each field x value{present,None} x legible{T,F} x confidence x floor
    grid_violations = []
    grid_n = 0
    for field, present, legible, conf, floor in itertools.product(
        FIELDS, (True, False), (True, False), CONF, FLOORS
    ):
        grid_n += 1
        value = f"{field}-VALUE" if present else None
        ext = make(**{field: (value, {"legible": legible, "confidence": conf})})
        got = classify_extraction(ext, floor=floor)

        # determinism inside the grid
        if got != classify_extraction(ext, floor=floor):
            grid_violations.append((field, present, legible, conf, floor, "non-deterministic"))
            continue

        below_floor = FLOOR_RANK[conf] < FLOOR_RANK[floor]
        must_review = (not present) or (not legible) or below_floor
        # plan_type + None + not flagged absent still must review (carve-out needs absent:true)
        expected = "needs_review" if must_review else "extracted"
        if got != expected:
            grid_violations.append((field, present, legible, conf, floor, f"{got}!={expected}"))

    check(f"FULL GRID ({grid_n} combos): any field None/illegible/below-floor -> needs_review; "
          f"all-good -> extracted; deterministic throughout",
          not grid_violations, f"{len(grid_violations)} violations, e.g. {grid_violations[:4]}")

    # --- 3. the plan_type-absent carve-out ---------------------------------
    absent_ok = make(plan_type=(None, {"legible": False, "confidence": "low", "absent": True}))
    check("plan_type absent + flagged absent:true -> not a blocker (extracted)",
          classify_extraction(absent_ok) == "extracted", classify_extraction(absent_ok))
    absent_unflagged = make(plan_type=(None, {"legible": False, "confidence": "low", "absent": False}))
    check("plan_type absent + NOT flagged -> needs_review",
          classify_extraction(absent_unflagged) == "needs_review",
          classify_extraction(absent_unflagged))
    # the carve-out is plan_type ONLY — the same shape on member_id still reviews
    member_absent = make(member_id=(None, {"legible": False, "confidence": "low", "absent": True}))
    check("member_id None even with absent:true -> needs_review (carve-out is plan_type only)",
          classify_extraction(member_absent) == "needs_review",
          classify_extraction(member_absent))

    # --- 4. named realistic scenarios -------------------------------------
    glare = make(member_id=(None, {"legible": False, "confidence": "low"}))
    check("test_glare_on_member_id_forces_review",
          classify_extraction(glare) == "needs_review", classify_extraction(glare))

    ambiguous_digit = make(member_id=("A1234I567", {"legible": True, "confidence": "medium"}))
    check("test_medium_confidence_ambiguous_digit_at_high_floor_reviews",
          classify_extraction(ambiguous_digit, floor="high") == "needs_review",
          classify_extraction(ambiguous_digit, floor="high"))
    check("...but the same medium-confidence field passes at the medium floor",
          classify_extraction(ambiguous_digit, floor="medium") == "extracted",
          classify_extraction(ambiguous_digit, floor="medium"))

    empty_string = make(group_number=("   ", {"legible": True, "confidence": "high"}))
    check("a whitespace-only value counts as missing -> needs_review",
          classify_extraction(empty_string) == "needs_review",
          classify_extraction(empty_string))

    bad_floor = make()
    check("an unknown floor string falls back to 'medium', still deterministic",
          classify_extraction(bad_floor, floor="bananas") == "extracted")

    # --- 5. multiple degraded fields -------------------------------------
    two_bad = make(
        member_id=(None, {"legible": False}),
        payer_name=("x", {"legible": True, "confidence": "low"}),
    )
    check("two degraded fields -> needs_review", classify_extraction(two_bad) == "needs_review")

    print()
    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — the confidence gate matches §6.4.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
