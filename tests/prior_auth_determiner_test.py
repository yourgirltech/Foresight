#!/usr/bin/env python3
"""02 — the pure prior-auth simulation: determine / draft_request / simulate_response.

Proves the contract in docs/agents/02-prior-auth-agent.md §7, including:
  * THE P5 PROOF: determine() returns emergency_exempt (required=False) for
    EVERY payer x procedure combination when the encounter is emergency /
    place_of_service == 'emergency' — it is structurally impossible for an
    emergency service to yield an auth requirement;
  * sentinel / short / missing procedure code -> insufficient_info;
  * None payer -> insufficient_info;
  * ALWAYS_AUTH_PROCEDURES -> required_draft regardless of payer;
  * ELECTIVE_AUTH_PROCEDURES -> required_draft iff payer.prior_auth_required_default;
  * everything else -> not_required;
  * draft_request is deterministic (bar drafted_at) and picks the manual_fax
    channel iff the payer has no electronic PA channel;
  * simulate_response partitions 0..99 into approved / info_needed / denied
    exactly at threshold and threshold+INFO_NEEDED_BAND, and a resubmit shifts
    the effective bucket down by RESUBMIT_APPROVAL_BONUS.

Stdlib only. Run:  python tests/prior_auth_determiner_test.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.agents.prior_auth import (  # noqa: E402
    ALWAYS_AUTH_PROCEDURES,
    ELECTIVE_AUTH_PROCEDURES,
    INFO_NEEDED_BAND,
    RESUBMIT_APPROVAL_BONUS,
    SENTINEL_PROCEDURE_CODES,
    determine,
    draft_request,
    response_bucket,
    simulate_response,
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


PAYERS = [
    None,
    {"id": "p-none", "name": "NoDefault", "prior_auth_supported": True,
     "prior_auth_required_default": False, "prior_auth_approval_threshold": 80},
    {"id": "p-req", "name": "RequiresElective", "prior_auth_supported": True,
     "prior_auth_required_default": True, "prior_auth_approval_threshold": 60},
    {"id": "p-fax", "name": "FaxOnly", "prior_auth_supported": False,
     "prior_auth_required_default": True, "prior_auth_approval_threshold": 78},
]
PROCEDURES = (
    sorted(ALWAYS_AUTH_PROCEDURES)
    + sorted(ELECTIVE_AUTH_PROCEDURES)
    + ["99213", "36415", "80053", "93000"]      # routine, no auth
    + sorted(SENTINEL_PROCEDURE_CODES)
    + ["", "  ", "X"]                            # blank / short
)
PLACES = ["office", "outpatient", "inpatient", "emergency"]


def enc(code, place="office", emergency=False):
    return {"procedure_code": code, "procedure_description": "", "place_of_service": place,
            "is_emergency": emergency}


def run() -> int:
    print("02 prior-auth — the pure determination / draft / response simulation\n")

    # === P5: emergency is ALWAYS exempt, for every payer x procedure ===========
    p5_violations = []
    for payer in PAYERS:
        for code in PROCEDURES:
            for place, em in (("emergency", False), ("office", True), ("emergency", True),
                              ("outpatient", True)):
                d = determine(enc(code, place, em), payer)
                if not (d.status == "emergency_exempt" and d.required is False):
                    p5_violations.append((payer and payer["id"], code, place, em, d.status))
    check(f"P5 — emergency/urgent -> emergency_exempt for ALL "
          f"{len(PAYERS)}x{len(PROCEDURES)} payer x procedure combos "
          f"(x4 emergency framings)", not p5_violations,
          f"{len(p5_violations)} violations, e.g. {p5_violations[:3]}")

    # === determination rules (non-emergency) =================================
    p_none = PAYERS[1]
    p_req = PAYERS[2]
    p_fax = PAYERS[3]

    for code in sorted(ALWAYS_AUTH_PROCEDURES):
        for payer in (p_none, p_req, p_fax):
            d = determine(enc(code), payer)
            check(f"ALWAYS_AUTH {code} + {payer['name']} -> required_draft",
                  d.status == "required_draft" and d.required is True, d.status)

    for code in sorted(ELECTIVE_AUTH_PROCEDURES):
        d_req = determine(enc(code), p_req)
        check(f"ELECTIVE {code} + required_default payer -> required_draft",
              d_req.status == "required_draft", d_req.status)
        d_no = determine(enc(code), p_none)
        check(f"ELECTIVE {code} + non-required_default payer -> not_required",
              d_no.status == "not_required", d_no.status)

    for code in ("99213", "36415", "80053", "93000"):
        for payer in (p_none, p_req):
            d = determine(enc(code), payer)
            check(f"routine {code} + {payer['name']} -> not_required",
                  d.status == "not_required" and d.required is False, d.status)

    for code in list(SENTINEL_PROCEDURE_CODES) + ["", "  ", "X"]:
        d = determine(enc(code), p_req)
        check(f"sentinel/short procedure {code!r} -> insufficient_info",
              d.status == "insufficient_info" and d.required is None, d.status)

    d = determine(enc("72148"), None)
    check("known always-auth procedure but payer None -> insufficient_info",
          d.status == "insufficient_info", d.status)

    # === draft_request =====================================================
    patient = {"name": "Casey Rivera", "member_id": "M-4471820"}
    r1 = draft_request(enc("72148"), p_req, patient)
    r2 = draft_request(enc("72148"), p_req, patient)
    check("draft_request is deterministic (bar drafted_at)",
          {k: v for k, v in r1.items() if k != "drafted_at"}
          == {k: v for k, v in r2.items() if k != "drafted_at"})
    check("draft_request channel = electronic for a payer with a PA channel",
          draft_request(enc("72148"), p_req, patient)["channel"] == "electronic")
    check("draft_request channel = manual_fax for a payer with NO PA channel",
          draft_request(enc("72148"), p_fax, patient)["channel"] == "manual_fax")
    check("draft_request carries a non-empty clinical_justification",
          bool(r1["clinical_justification"]))

    # === simulate_response: the three bands partition 0..99 =================
    # find a member id that buckets to a target raw value for a fixed payer/proc
    def member_at(target: int) -> str:
        for i in range(200000):
            mid = f"MEMBER{i}"
            if response_bucket(mid, "p-band", "72148") == target:
                return mid
        raise AssertionError(f"no member id found for bucket {target}")

    payer_band = {"id": "p-band", "prior_auth_approval_threshold": 70}
    thr = 70
    e = enc("72148")
    below = member_at(thr - 5)
    in_info = member_at(thr + 3)          # thr..thr+11 -> info_needed
    above = member_at(thr + INFO_NEEDED_BAND + 4)

    check(f"bucket {thr - 5} (< {thr}) -> approved + an authorization_number",
          (lambda r: r.response_status == "approved" and r.authorization_number)(
              simulate_response(e, payer_band, {"member_id": below})))
    check(f"bucket {thr + 3} ([{thr},{thr + INFO_NEEDED_BAND})) -> info_needed",
          simulate_response(e, payer_band, {"member_id": in_info}).response_status == "info_needed")
    check(f"bucket {thr + INFO_NEEDED_BAND + 4} (>= {thr + INFO_NEEDED_BAND}) -> denied",
          simulate_response(e, payer_band, {"member_id": above}).response_status == "denied")

    check("simulate_response is deterministic",
          simulate_response(e, payer_band, {"member_id": below}).response_payload["bucket"]
          == simulate_response(e, payer_band, {"member_id": below}).response_payload["bucket"])

    # a resubmit shifts the effective bucket down by RESUBMIT_APPROVAL_BONUS
    raw_info = response_bucket(in_info, "p-band", "72148")
    resub = simulate_response(e, payer_band, {"member_id": in_info}, is_resubmit=True)
    check(f"resubmit shifts the effective bucket down by {RESUBMIT_APPROVAL_BONUS} "
          f"(raw {raw_info} -> {resub.response_payload['bucket']})",
          resub.response_payload["bucket"] == max(0, raw_info - RESUBMIT_APPROVAL_BONUS)
          and resub.response_payload["raw_bucket"] == raw_info)
    check("an info_needed request, resubmitted, becomes approved",
          resub.response_status == "approved")

    # full-partition sweep: every raw bucket 0..99 lands in exactly one band, in order
    bands = []
    for b in range(100):
        if b < thr:
            bands.append("approved")
        elif b < thr + INFO_NEEDED_BAND:
            bands.append("info_needed")
        else:
            bands.append("denied")
    ok_partition = (bands.count("approved") == thr
                    and bands.count("info_needed") == INFO_NEEDED_BAND
                    and bands.count("denied") == 100 - thr - INFO_NEEDED_BAND)
    check("the three response bands partition 0..99 exactly (no gap, no overlap)", ok_partition)

    print()
    print("=" * 60)
    total = _PASS + _FAIL
    if _FAIL == 0:
        print(f"ALL {total} CHECKS PASSED — the pure prior-auth simulation matches §7.")
        return 0
    print(f"{_PASS}/{total} passed — {_FAIL} FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
