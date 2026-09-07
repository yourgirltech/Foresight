"""04 — cob-agent: the deterministic coordination-of-benefits rule engine.

Pure Python, no LLM, no I/O, no randomness, no time-dependence except a `today`
that is passed in. Given a patient's ACTIVE coverages for ONE `plan_kind`, it
returns one placement per coverage — `primary` / `secondary` / `tertiary` — each
carrying the numbered NAIC rule that decided its rank and a plain-language
rationale. 04 is `rules.py` (06) in a new domain.

`docs/agents/04-cob-agent.md` §6 is the contract; this module is the
implementation. Keep them in lockstep.

04 REPORTS, it does not act (agent doc §2): `determine_cob()` is pure and writes
nothing; a human reads the insurance summary and can override the order
(`manual_order_override` -> rule R0). 04 is NOT a Commander agent.

The ladder (evaluated top to bottom; the first rule that distinguishes a pair
fixes their relative order):

    R0  manual override .............. a pinned coverage sits exactly where staff put it
    R1  non-dependent over dependent . the patient's own plan beats a plan covering them as a dependent
    R2  Medicaid is always last ...... payer of last resort, behind every non-Medicaid coverage
    R3  birthday rule ............... dependent child on two parents' plans -> earlier-in-year parent birthday
    R4  active over inactive employ .. employer_active beats employer_retiree / cobra
    R5  Medicare Secondary Payer ..... Medicare secondary to employer_active; primary over retiree/cobra/individual
    R6  longer-covered plan is primary earlier effective_date
    R7  deterministic final tie-break  order by (payer_name, member_id, id); rationale says "confirm with the payers"

R1/R3/R4/R6 do not fire on a pair that involves a government program (Medicaid,
Medicare) — those are governed by R2 and R5, which is why R2 sits above R3-R6
and Medicaid still lands last even against a plan that covers the patient as a
dependent. R1 additionally skips any pair involving a retiree / COBRA plan:
NAIC's continuation-coverage rule makes the *other* plan primary regardless of
dependent status, so that pair is settled by R4/R5 (matches agent doc §6.3, the
"cobra + employer_active as dependent" case).

Out of scope (flagged in the rationale where relevant): divorce/custody decrees
(R3 birthday rule is the NAIC default without one), the 20-employee MSP
threshold (R5 assumes the group-plan-primary case and says so).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import cmp_to_key

GOVERNMENT_TYPES = {"medicare", "medicaid"}
ACTIVE_EMPLOYMENT = {"employer_active"}
INACTIVE_EMPLOYMENT = {"employer_retiree", "cobra"}
MEDICARE_PRIMARY_OVER = {"employer_retiree", "cobra", "individual"}

ORDER_LABELS = ("primary", "secondary", "tertiary", "quaternary")

# rule id -> the criterion, for tests and the UI legend
RULE_CRITERION = {
    "R0": "set by staff",
    "R1": "non-dependent over dependent",
    "R2": "Medicaid is always last",
    "R3": "birthday rule",
    "R4": "active employment over retiree / COBRA",
    "R5": "Medicare Secondary Payer",
    "R6": "longer-covered plan is primary",
    "R7": "deterministic tie-break — confirm with the payers",
}

_MSP = "assumes an employer with 20+ employees; verify with the plan"
_R7_WHY = (
    "no rule distinguishes these coverages; ordered by payer name for a stable "
    "result — confirm the order with the payers"
)


@dataclass(frozen=True)
class CobPlacement:
    coverage_id: str
    order: str          # "primary" | "secondary" | "tertiary" | ...
    rule: str           # "R0".."R7" — the rule that decided this coverage's rank ("" for a sole coverage)
    rationale: str      # one plain-language sentence


# --------------------------------------------------------------------------- #
# active-window helper (used by the endpoint; determine_cob assumes the caller
# already filtered but is safe either way)
# --------------------------------------------------------------------------- #
def _as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_active(coverage: dict, today: date) -> bool:
    eff = _as_date(coverage.get("effective_date"))
    term = _as_date(coverage.get("termination_date"))
    if eff is None or eff > today:
        return False
    return term is None or term >= today


# --------------------------------------------------------------------------- #
# the pairwise rules — each returns (sign, rule_id, why_primary, why_secondary)
# or None ("this rule does not distinguish the pair"). sign < 0 => `a` is primary.
# `why_primary` reads as a sentence about the primary coverage, `why_secondary`
# about the secondary one. Every rule is antisymmetric.
# --------------------------------------------------------------------------- #
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _is_own(c: dict) -> bool:
    return not c.get("is_dependent") and c.get("relationship_to_subscriber") == "self"


def _is_dependent_child(c: dict) -> bool:
    return bool(c.get("is_dependent")) and c.get("relationship_to_subscriber") == "child"


def _birthday_key(c: dict) -> tuple[int, int] | None:
    d = _as_date(c.get("subscriber_dob"))
    return (d.month, d.day) if d else None


def _md(c: dict) -> str:
    d = _as_date(c.get("subscriber_dob"))
    return f"{_MONTHS[d.month - 1]} {d.day}" if d else "an unknown date"


def _involves_government(a: dict, b: dict) -> bool:
    return a.get("coverage_type") in GOVERNMENT_TYPES or b.get("coverage_type") in GOVERNMENT_TYPES


def _r1(a: dict, b: dict):
    if _involves_government(a, b):
        return None  # Medicaid -> R2, Medicare -> R5
    if a.get("coverage_type") in INACTIVE_EMPLOYMENT or b.get("coverage_type") in INACTIVE_EMPLOYMENT:
        return None  # retiree / COBRA continuation -> settled by R4 (NAIC continuation-coverage rule)
    a_own, b_own = _is_own(a), _is_own(b)
    if a_own == b_own:
        return None
    wp = "covers the patient as the subscriber/employee; the other covers them as a dependent"
    ws = "covers the patient as a dependent; the plan covering them as the subscriber is primary"
    return (-1, "R1", wp, ws) if a_own else (1, "R1", wp, ws)


def _r2(a: dict, b: dict):
    a_mcd, b_mcd = a.get("coverage_type") == "medicaid", b.get("coverage_type") == "medicaid"
    if a_mcd == b_mcd:
        return None
    wp = "is not Medicaid, so it pays before the Medicaid coverage"
    ws = "is Medicaid — the payer of last resort, behind every other coverage"
    return (1, "R2", wp, ws) if a_mcd else (-1, "R2", wp, ws)


def _r3(a: dict, b: dict):
    if _involves_government(a, b):
        return None
    if not (_is_dependent_child(a) and _is_dependent_child(b)):
        return None
    ka, kb = _birthday_key(a), _birthday_key(b)
    if ka is None or kb is None or ka == kb:
        return None  # cannot apply / tie -> fall through to R4..R6
    earlier, later = (_md(a), _md(b)) if ka < kb else (_md(b), _md(a))
    wp = (f"has the subscriber whose birthday ({earlier}) falls earlier in the calendar year "
          f"than the other subscriber's ({later}) — the birthday rule, the NAIC default absent a court decree")
    ws = (f"has the subscriber whose birthday ({later}) falls later in the calendar year "
          f"than the other subscriber's ({earlier}) — the birthday rule")
    return (-1, "R3", wp, ws) if ka < kb else (1, "R3", wp, ws)


def _r4(a: dict, b: dict):
    if _involves_government(a, b):
        return None
    a_act, a_in = a.get("coverage_type") in ACTIVE_EMPLOYMENT, a.get("coverage_type") in INACTIVE_EMPLOYMENT
    b_act, b_in = b.get("coverage_type") in ACTIVE_EMPLOYMENT, b.get("coverage_type") in INACTIVE_EMPLOYMENT
    wp = "is through active employment; the other is a retiree / COBRA plan"
    ws = "is a retiree / COBRA plan; the active-employment plan is primary"
    if a_act and b_in:
        return (-1, "R4", wp, ws)
    if b_act and a_in:
        return (1, "R4", wp, ws)
    return None


def _r5(a: dict, b: dict):
    a_mcr, b_mcr = a.get("coverage_type") == "medicare", b.get("coverage_type") == "medicare"
    if a_mcr == b_mcr:
        return None
    other = b if a_mcr else a
    ot = other.get("coverage_type")
    if ot in ACTIVE_EMPLOYMENT:  # Medicare is SECONDARY to an active group plan
        wp = f"is an active employer group plan; Medicare is secondary to it ({_MSP})"
        ws = f"is Medicare — secondary to an active employer group plan ({_MSP})"
        return (1, "R5", wp, ws) if a_mcr else (-1, "R5", wp, ws)
    if ot in MEDICARE_PRIMARY_OVER:  # Medicare is PRIMARY over retiree / COBRA / individual
        wp = "is Medicare, which pays before a retiree / COBRA / individual plan"
        ws = "pays after Medicare (a retiree / COBRA / individual plan)"
        return (-1, "R5", wp, ws) if a_mcr else (1, "R5", wp, ws)
    return None  # Medicare vs tricare/other -> fall through


def _r6(a: dict, b: dict):
    ea, eb = _as_date(a.get("effective_date")), _as_date(b.get("effective_date"))
    if ea is None or eb is None or ea == eb:
        return None
    lo, hi = (ea, eb) if ea < eb else (eb, ea)
    wp = f"has covered the patient longer (effective {lo.isoformat()} vs {hi.isoformat()})"
    ws = f"is the newer coverage (effective {hi.isoformat()} vs {lo.isoformat()}); the longer-held plan is primary"
    return (-1, "R6", wp, ws) if ea < eb else (1, "R6", wp, ws)


def _r7(a: dict, b: dict):
    ka = (a.get("payer_name", ""), a.get("member_id", ""), _cid(a))
    kb = (b.get("payer_name", ""), b.get("member_id", ""), _cid(b))
    return (-1, "R7", _R7_WHY, _R7_WHY) if ka <= kb else (1, "R7", _R7_WHY, _R7_WHY)


_RULES = (_r1, _r2, _r3, _r4, _r5, _r6, _r7)


def _cmp(a: dict, b: dict) -> tuple[int, str, str, str]:
    for rule in _RULES:
        res = rule(a, b)
        if res is not None:
            return res
    return _r7(a, b)  # unreachable — _r7 always returns


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _cid(c: dict) -> str:
    return str(c.get("id") or c.get("coverage_id") or "")


def _sentence(order: str, rule: str, why: str, position: int) -> str:
    cap = order.capitalize()
    if rule == "R0":
        return f"{cap} — set by staff (pinned to position {position})."
    if not rule:
        return f"{cap} — {why}"
    return f"{cap} — this plan {why}."


def determine_cob(
    coverages: list[dict], *, today: date, patient_dob: date | None = None
) -> list[CobPlacement]:
    """Pure. `coverages` are the ACTIVE coverages for ONE patient and ONE
    plan_kind. Returns one placement per coverage, primary first. Deterministic
    and order-independent — the input order of `coverages` does not matter.

    `patient_dob` is accepted for interface stability; the current ladder keys
    the birthday rule off `subscriber_dob`, not the patient's own dob.
    """
    coverages = list(coverages)
    n = len(coverages)
    if n == 0:
        return []
    if n == 1:
        return [CobPlacement(_cid(coverages[0]), "primary", "",
                             "Only active coverage of this kind — primary by default.")]

    pinned = [c for c in coverages if c.get("manual_order_override") is not None]
    free = [c for c in coverages if c.get("manual_order_override") is None]
    free_sorted = sorted(free, key=cmp_to_key(lambda x, y: _cmp(x, y)[0]))

    slots: list[tuple[dict, bool] | None] = [None] * n
    for cov in sorted(pinned, key=lambda c: (int(c["manual_order_override"]), _cid(c))):
        idx = min(max(int(cov["manual_order_override"]) - 1, 0), n - 1)
        while slots[idx] is not None:
            idx = (idx + 1) % n
        slots[idx] = (cov, True)
    fi = 0
    for i in range(n):
        if slots[i] is None:
            slots[i] = (free_sorted[fi], False)
            fi += 1

    free_positions = [i for i, s in enumerate(slots) if s and not s[1]]

    placements: list[CobPlacement] = []
    for i, s in enumerate(slots):
        assert s is not None
        cov, is_pinned = s
        order = ORDER_LABELS[i] if i < len(ORDER_LABELS) else f"rank {i + 1}"
        if is_pinned:
            rule, why = "R0", ""
        else:
            fp = free_positions.index(i)
            if fp > 0:  # compare with the free coverage directly above
                ref = slots[free_positions[fp - 1]][0]  # type: ignore[index]
                _, rule, _wp, why = _cmp(ref, cov)
            elif len(free_positions) > 1:  # this is the primary among the free coverages
                other = slots[free_positions[1]][0]  # type: ignore[index]
                _, rule, why, _ws = _cmp(cov, other)
            else:
                rule, why = "", "the only coverage not pinned by staff."
        placements.append(CobPlacement(
            coverage_id=_cid(cov),
            order=order,
            rule=rule,
            rationale=_sentence(order, rule, why, i + 1),
        ))
    return placements


def cob_summary(coverages: list[dict], *, today: date, patient_dob: date | None = None) -> dict:
    """Group `coverages` by `plan_kind`, run `determine_cob` over the ACTIVE ones
    in each group, and return {plan_kind: [placement dict, ...]}. A group with a
    single active coverage still gets a placement. Inactive coverages are
    omitted from the result (the caller still shows them, just without a rank).
    """
    by_kind: dict[str, list[dict]] = {}
    for c in coverages:
        if is_active(c, today):
            by_kind.setdefault(c.get("plan_kind") or "medical", []).append(c)

    out: dict[str, list[dict]] = {}
    for kind, group in by_kind.items():
        out[kind] = [
            {"coverage_id": p.coverage_id, "order": p.order, "rule": p.rule,
             "rule_criterion": RULE_CRITERION.get(p.rule, ""), "rationale": p.rationale}
            for p in determine_cob(group, today=today, patient_dob=patient_dob)
        ]
    return out
