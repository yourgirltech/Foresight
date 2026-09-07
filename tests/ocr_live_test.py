#!/usr/bin/env python3
"""03 — opt-in live vision integration test.

Calls Claude vision (real ANTHROPIC_API_KEY) against synthetic, clearly-fake
card images and asserts the CONTRACT, not exact wording:

  * the response has all four fields in both `fields` and `confidence`;
  * a field the fixture deliberately makes illegible comes back null +
    legible:false — NEVER a fabricated value (the whole point of 03);
  * a field not printed on the card comes back null (absent);
  * the pure gate classifies a clean card `extracted` and a degraded one
    `needs_review`.

Skipped unless BOTH:  --live  passed  AND  ANTHROPIC_API_KEY set.
Needs Pillow (tests/_cards.py). No Supabase stack required.

Run:  python tests/ocr_live_test.py --live
"""
from __future__ import annotations

import asyncio
import os
import pathlib
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
        print("03 live OCR test — SKIPPED (pass --live to run against the real vision API)")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # fall back to backend/.env
        env = pathlib.Path(__file__).resolve().parents[1] / "backend" / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "ANTHROPIC_API_KEY":
                    os.environ["ANTHROPIC_API_KEY"] = value.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("03 live OCR test — SKIPPED (--live given but no ANTHROPIC_API_KEY)")
        return 0

    from app.agents.ocr import FIELDS, classify_extraction, extract  # noqa: E402, PLC0415
    from app.config import get_settings  # noqa: E402, PLC0415

    from _cards import synthetic_card  # noqa: E402, PLC0415

    floor = get_settings().ocr_confidence_floor
    print(f"03 live OCR — model {get_settings().ocr_model}, confidence floor {floor}\n")

    # ---- clean card: everything extracted, member_id matches exactly ----------
    png, spec = synthetic_card("clean")
    ext = await extract(png, "image/png")
    check("clean: all four fields present in fields + confidence",
          all(f in ext.fields and f in ext.confidence for f in FIELDS), str(ext))
    got_member = (ext.fields.get("member_id") or "").replace(" ", "")
    check("clean: member_id read exactly off the card",
          got_member == spec["member_id"].replace(" ", ""),
          f"{got_member!r} != {spec['member_id']!r}")
    check("clean: payer_name mentions Northwind",
          "northwind" in (ext.fields.get("payer_name") or "").lower(),
          str(ext.fields.get("payer_name")))
    check("clean: gate says 'extracted'", classify_extraction(ext, floor=floor) == "extracted",
          classify_extraction(ext, floor=floor))

    # ---- blurred member id: null, NOT a guess --------------------------------
    png, spec = synthetic_card("blurry-member-id")
    ext = await extract(png, "image/png")
    mid = ext.fields.get("member_id")
    check("blurry: member_id is null / not-legible — NOT a fabricated value",
          mid is None or ext.confidence.get("member_id", {}).get("legible") is False,
          f"member_id={mid!r} conf={ext.confidence.get('member_id')}")
    check("blurry: the illegible member_id was NOT guessed as the real value",
          (mid or "").replace(" ", "") != spec["member_id"].replace(" ", ""), f"{mid!r}")
    check("blurry: gate says 'needs_review'",
          classify_extraction(ext, floor=floor) == "needs_review",
          classify_extraction(ext, floor=floor))

    # ---- no plan type printed ----------------------------------------------
    png, spec = synthetic_card("no-plan-type")
    ext = await extract(png, "image/png")
    check("no-plan-type: plan_type is null (not on the card)",
          ext.fields.get("plan_type") in (None, ""), str(ext.fields.get("plan_type")))
    check("no-plan-type: the other three fields still came back non-null",
          all(ext.fields.get(f) for f in ("member_id", "group_number", "payer_name")),
          str(ext.fields))

    # ---- wrong card (back): nothing visible, nothing invented --------------
    png, spec = synthetic_card("wrong-card-back")
    ext = await extract(png, "image/png")
    check("card-back: no coverage field was invented (all null / not-legible)",
          all(ext.fields.get(f) in (None, "")
              or ext.confidence.get(f, {}).get("legible") is False for f in FIELDS),
          str(ext.fields))

    print()
    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} LIVE CHECKS PASSED — 03 reads the card and never guesses.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
