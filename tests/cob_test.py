#!/usr/bin/env python3
"""04 — the pure coordination-of-benefits rule engine: determine_cob().

Proves docs/agents/04-cob-agent.md §6 to the same standard as the eligibility /
prior-auth fuzz:

  * every worked case in §6.3, by name;
  * THE EXHAUSTIVE FUZZ over pairs of coverages — for a few thousand cases:
      - deterministic (two calls identical);
      - EXACTLY ONE primary; the orders are a clean permutation
        {primary, secondary} — no gap, no duplicate;
      - the cited rule is R0..R7 and its stated criterion actually holds in the
        chosen direction (an R3 primary really does have the earlier birthday, …);
      - order-independence: swapping the two input coverages yields the same
        placements;
  * a 3-coverage slice (own + spouse + Medicaid);
  * manual_order_override pins honoured and cited as R0.

Stdlib only. Run:  python tests/cob_test.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.cob import (  # noqa: E402
    ACTIVE_EMPLOYMENT,
    INACTIVE_EMPLOYMENT,
    MEDICARE_PRIMARY_OVER,
    RULE_CRITERION,
    determine_cob,
)

_PASS = _FAIL = 0
TODAY = date(2026, 9, 7)


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


_ID = itertools.count(1)


def cov(**kw) -> dict:
    d = {
        "id": kw.pop("id", f"cov-{next(_ID)}"),
        "payer_name": kw.pop("payer_name", "Payer"),
        "member_id": kw.pop("member_id", "M1"),
        "plan_kind": "medical",
        "coverage_type": "employer_active",
        "relationship_to_subscriber": "self",
        "is_dependent": False,
        "subscriber_dob": None,
        "effective_date": "2020-01-01",
        "termination_date": None,
        "manual_order_override": None,
    }
    d.update(kw)
    return d


def placements_by_id(coverages):
    res = determine_cob(coverages, today=TODAY, patient_dob=None)
    return res, {p.coverage_id: p for p in res}


# --------------------------------------------------------------------------- #
# per-rule criterion verifier — "the cited rule actually holds this direction"
# --------------------------------------------------------------------------- #
def _is_own(c):
    return not c["is_dependent"] and c["relationship_to_subscriber"] == "self"


def _bkey(c):
    d = c["subscriber_dob"]
    if not d:
        return None
    y = date.fromisoformat(d)
    return (y.month, y.day)


def rule_holds(primary: dict, secondary: dict, rule: str) -> bool:
    p, s = primary, secondary
    gov = {"medicare", "medicaid"}
    if rule == "R0":
        return p.get("manual_order_override") is not None
    if rule == "R1":
        return (p["coverage_type"] not in gov and s["coverage_type"] not in gov
                and p["coverage_type"] not in INACTIVE_EMPLOYMENT
                and s["coverage_type"] not in INACTIVE_EMPLOYMENT
                and _is_own(p) and not _is_own(s))
    if rule == "R2":
        return s["coverage_type"] == "medicaid" and p["coverage_type"] != "medicaid"
    if rule == "R3":
        kp, ks = _bkey(p), _bkey(s)
        return (p["is_dependent"] and s["is_dependent"]
                and p["relationship_to_subscriber"] == "child"
                and s["relationship_to_subscriber"] == "child"
                and kp is not None and ks is not None and kp < ks)
    if rule == "R4":
        return (p["coverage_type"] in ACTIVE_EMPLOYMENT and s["coverage_type"] in INACTIVE_EMPLOYMENT)
    if rule == "R5":
        if p["coverage_type"] == "medicare":
            return s["coverage_type"] in MEDICARE_PRIMARY_OVER
        if s["coverage_type"] == "medicare":
            return p["coverage_type"] in ACTIVE_EMPLOYMENT
        return False
    if rule == "R6":
        return date.fromisoformat(p["effective_date"]) < date.fromisoformat(s["effective_date"])
    if rule == "R7":
        kp = (p["payer_name"], p["member_id"], p["id"])
        ks = (s["payer_name"], s["member_id"], s["id"])
        return kp <= ks
    return False


# --------------------------------------------------------------------------- #
# 1. worked cases (§6.3)
# --------------------------------------------------------------------------- #
def worked_cases() -> None:
    print("  -- worked cases (agent doc §6.3) --")

    # own employer_active + spouse's plan covering patient as dependent -> own (R1)
    own = cov(id="own", coverage_type="employer_active")
    spouse = cov(id="spouse", coverage_type="employer_active", relationship_to_subscriber="spouse",
                 is_dependent=True)
    res, by = placements_by_id([own, spouse])
    check("own plan primary over spouse's dependent plan (R1)",
          by["own"].order == "primary" and by["own"].rule == "R1"
          and by["spouse"].order == "secondary", str(res))

    # child on mom's plan (Mar 3) + dad's plan (Nov 12) -> mom's (R3)
    mom = cov(id="mom", is_dependent=True, relationship_to_subscriber="child", subscriber_dob="1988-03-03")
    dad = cov(id="dad", is_dependent=True, relationship_to_subscriber="child", subscriber_dob="1985-11-12")
    res, by = placements_by_id([dad, mom])
    check("birthday rule: earlier-in-year parent (Mar 3) primary (R3)",
          by["mom"].order == "primary" and by["mom"].rule == "R3", str(res))

    # both parents born Jun 1 -> tie -> earlier effective_date (R3 -> R6)
    p1 = cov(id="p1", is_dependent=True, relationship_to_subscriber="child",
             subscriber_dob="1980-06-01", effective_date="2019-01-01")
    p2 = cov(id="p2", is_dependent=True, relationship_to_subscriber="child",
             subscriber_dob="1990-06-01", effective_date="2021-01-01")
    res, by = placements_by_id([p2, p1])
    check("birthday tie -> longer-covered plan primary (R6)",
          by["p1"].order == "primary" and by["p1"].rule == "R6", str(res))

    # employer_active + medicaid -> employer primary, medicaid secondary (R2)
    emp = cov(id="emp", coverage_type="employer_active")
    mcd = cov(id="mcd", coverage_type="medicaid")
    res, by = placements_by_id([mcd, emp])
    check("Medicaid is always last (R2)",
          by["emp"].order == "primary" and by["mcd"].order == "secondary"
          and by["mcd"].rule == "R2", str(res))

    # employer_retiree + medicare -> Medicare primary (R5)
    ret = cov(id="ret", coverage_type="employer_retiree")
    mcr = cov(id="mcr", coverage_type="medicare")
    res, by = placements_by_id([ret, mcr])
    check("Medicare primary over a retiree plan (R5)",
          by["mcr"].order == "primary" and by["mcr"].rule == "R5", str(res))

    # employer_active + medicare -> employer primary, Medicare secondary (R5)
    act = cov(id="act", coverage_type="employer_active")
    mcr2 = cov(id="mcr2", coverage_type="medicare")
    res, by = placements_by_id([mcr2, act])
    check("Medicare secondary to an active group plan (R5)",
          by["act"].order == "primary" and by["mcr2"].order == "secondary"
          and by["mcr2"].rule == "R5", str(res))

    # cobra + employer_active (spouse's, as dependent) -> active primary (R4)
    cobra = cov(id="cobra", coverage_type="cobra")
    activ = cov(id="activ", coverage_type="employer_active", relationship_to_subscriber="spouse",
                is_dependent=True)
    res, by = placements_by_id([cobra, activ])
    check("active-employment plan primary over COBRA (R4)",
          by["activ"].order == "primary" and by["activ"].rule == "R4"
          and by["cobra"].order == "secondary", str(res))

    # two identical individual plans -> deterministic by payer name (R7), flagged
    i1 = cov(id="i1", coverage_type="individual", payer_name="Zenith", member_id="X")
    i2 = cov(id="i2", coverage_type="individual", payer_name="Acme", member_id="X")
    res, by = placements_by_id([i1, i2])
    check("no distinguishing rule -> deterministic order by payer name (R7)",
          by["i2"].order == "primary" and by["i2"].rule == "R7"
          and "confirm" in by["i2"].rationale.lower(), str(res))


# --------------------------------------------------------------------------- #
# 2. THE EXHAUSTIVE FUZZ
# --------------------------------------------------------------------------- #
COVERAGE_TYPES = ("employer_active", "employer_retiree", "cobra", "individual",
                  "medicare", "medicaid", "tricare", "other")
RELATIONSHIPS = ("self", "child", "spouse")
DEPENDENT = (True, False)
EFF_ORDER = ("a_earlier", "b_earlier", "same")
BDAY_ORDER = ("a_earlier", "b_earlier", "same")


def fuzz() -> None:
    print("  -- exhaustive fuzz over coverage pairs --")
    variants = list(itertools.product(RELATIONSHIPS, DEPENDENT, COVERAGE_TYPES))
    n_cases = 0
    bad_det = bad_perm = bad_rule = bad_order = 0
    examples: list[str] = []

    for (ra, da, ta), (rb, db, tb) in itertools.product(variants, repeat=2):
        for eff in EFF_ORDER:
            for bday in BDAY_ORDER:
                n_cases += 1
                ea, eb = {
                    "a_earlier": ("2018-01-01", "2022-01-01"),
                    "b_earlier": ("2022-01-01", "2018-01-01"),
                    "same": ("2020-01-01", "2020-01-01"),
                }[eff]
                doba, dobb = {
                    "a_earlier": ("1980-02-10", "1980-09-20"),
                    "b_earlier": ("1980-09-20", "1980-02-10"),
                    "same": ("1980-05-05", "1980-05-05"),
                }[bday]
                a = cov(id="A", payer_name="Alpha", member_id="A1", relationship_to_subscriber=ra,
                        is_dependent=da, coverage_type=ta, effective_date=ea, subscriber_dob=doba)
                b = cov(id="B", payer_name="Beta", member_id="B1", relationship_to_subscriber=rb,
                        is_dependent=db, coverage_type=tb, effective_date=eb, subscriber_dob=dobb)

                r1 = determine_cob([a, b], today=TODAY, patient_dob=None)
                r1b = determine_cob([a, b], today=TODAY, patient_dob=None)
                r2 = determine_cob([b, a], today=TODAY, patient_dob=None)

                if [(p.coverage_id, p.order, p.rule) for p in r1] != \
                   [(p.coverage_id, p.order, p.rule) for p in r1b]:
                    bad_det += 1
                    if len(examples) < 5:
                        examples.append(f"non-deterministic: {ta} vs {tb}")
                    continue

                orders = sorted(p.order for p in r1)
                if orders != ["primary", "secondary"] or len(r1) != 2:
                    bad_perm += 1
                    if len(examples) < 5:
                        examples.append(f"bad perm {orders}: {ta}/{ra}/{da} vs {tb}/{rb}/{db}")
                    continue

                prim = next(p for p in r1 if p.order == "primary")
                sec = next(p for p in r1 if p.order == "secondary")
                pc = a if prim.coverage_id == "A" else b
                sc = b if prim.coverage_id == "A" else a

                if prim.rule not in RULE_CRITERION or prim.rule == "R0":
                    bad_rule += 1
                    if len(examples) < 5:
                        examples.append(f"bad rule id {prim.rule!r}")
                    continue
                if not rule_holds(pc, sc, prim.rule):
                    bad_rule += 1
                    if len(examples) < 5:
                        examples.append(
                            f"rule {prim.rule} cited but criterion fails: "
                            f"{pc['coverage_type']}/{pc['relationship_to_subscriber']}/dep={pc['is_dependent']}"
                            f" over {sc['coverage_type']}/{sc['relationship_to_subscriber']}/dep={sc['is_dependent']}")
                    continue
                # both placements cite the same pairwise rule for a 2-set
                if sec.rule != prim.rule:
                    bad_rule += 1
                    if len(examples) < 5:
                        examples.append(f"pair cites two rules {prim.rule}/{sec.rule}")
                    continue

                if [(p.coverage_id, p.order) for p in r1] != [(p.coverage_id, p.order) for p in r2]:
                    bad_order += 1
                    if len(examples) < 5:
                        examples.append(f"order-dependent: {ta} vs {tb}")

    check(f"fuzz ran {n_cases} coverage-pair cases", n_cases > 3000, str(n_cases))
    check("every case deterministic (two identical calls agree)", bad_det == 0, f"{bad_det} failures")
    check("every case: exactly one primary, orders are a clean {primary,secondary}",
          bad_perm == 0, f"{bad_perm} failures; e.g. {examples[:3]}")
    check("every cited rule is R1..R7 and its criterion holds in the chosen direction",
          bad_rule == 0, f"{bad_rule} failures; e.g. {examples[:3]}")
    check("every case order-independent (swapping the two inputs = same placements)",
          bad_order == 0, f"{bad_order} failures; e.g. {examples[:3]}")


# --------------------------------------------------------------------------- #
# 3. three-coverage slice + 4. manual override
# --------------------------------------------------------------------------- #
def three_and_override() -> None:
    print("  -- 3-coverage slice + manual override --")

    own = cov(id="own", coverage_type="employer_active")
    spouse = cov(id="sp", coverage_type="employer_active", relationship_to_subscriber="spouse",
                 is_dependent=True)
    mcd = cov(id="mcd", coverage_type="medicaid")
    res, by = placements_by_id([mcd, spouse, own])
    check("3-set: own (R1) primary, spouse (R1) secondary, Medicaid (R2) tertiary",
          (by["own"].order, by["own"].rule) == ("primary", "R1")
          and (by["sp"].order, by["sp"].rule) == ("secondary", "R1")
          and (by["mcd"].order, by["mcd"].rule) == ("tertiary", "R2"), str(res))
    check("3-set: exactly one primary",
          sum(1 for p in res if p.order == "primary") == 1, str(res))

    # pin the Medicaid plan to position 1 — honoured, cited R0
    mcd_pinned = cov(id="mp", coverage_type="medicaid", manual_order_override=1)
    a = cov(id="a", coverage_type="employer_active")
    res, by = placements_by_id([a, mcd_pinned])
    check("manual_order_override=1 pins Medicaid to primary, cited R0",
          by["mp"].order == "primary" and by["mp"].rule == "R0"
          and "staff" in by["mp"].rationale.lower(), str(res))
    check("the unpinned coverage fills the remaining slot",
          by["a"].order == "secondary", str(res))

    # two pins, positions 2 and 1
    x = cov(id="x", coverage_type="employer_active", manual_order_override=2)
    y = cov(id="y", coverage_type="individual", manual_order_override=1)
    z = cov(id="z", coverage_type="employer_retiree")
    res, by = placements_by_id([x, y, z])
    check("two pins honoured exactly (y->1, x->2), free coverage z->3",
          by["y"].order == "primary" and by["x"].order == "secondary"
          and by["z"].order == "tertiary", str(res))
    check("still exactly one primary with pins",
          sum(1 for p in res if p.order == "primary") == 1, str(res))


def run() -> int:
    print("04 coordination of benefits — the pure R0-R7 rule engine\n")
    worked_cases()
    fuzz()
    three_and_override()
    print()
    print("=" * 62)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — determine_cob matches §6: deterministic, "
              "exactly one primary, every ordering explained by a cited rule.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
