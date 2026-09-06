"""02 — prior-auth-agent: the deterministic prior-authorization simulation.

Pure Python, no LLM, no I/O, no call-time randomness. Three pure functions:

    determine(encounter, payer)        -> Determination   (is auth required?)
    draft_request(encounter, payer, patient) -> dict       (the request packet)
    simulate_response(encounter, payer, patient) -> PayerResponse

Phase 3 does NOT call a real payer. This is a documented, reproducible simulation
- the prior-auth equivalent of 06's `rules.py` and 01's `eligibility.py`. The
exact contract is docs/agents/02-prior-auth-agent.md §7; this module is the
implementation, that section is the spec. Keep them in lockstep.

THE CARE-SAFETY INVARIANT (agent doc §2, mechanism P5): `determine` returns
`required = False` (status `emergency_exempt`) UNCONDITIONALLY when the encounter
is an emergency / urgent service. The branch that produces `required_draft` is
unreachable in that case - it is structurally impossible for an emergency
service to yield an auth requirement, a drafted request, or a submission.

    1. emergency / place_of_service == 'emergency' -> emergency_exempt   (P5)
    2. procedure code blank / sentinel / too short -> insufficient_info
    3. payer unknown                                -> insufficient_info
    4. procedure in ALWAYS_AUTH_PROCEDURES          -> required_draft
    5. required_default payer + ELECTIVE_AUTH set   -> required_draft
    6. otherwise                                    -> not_required
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

BASIS = "deterministic-sim-v1"

# Illustrative, NOT clinical guidance - the point is a reproducible simulation,
# exactly like 06's severity weights. Kept in lockstep with agent doc §4.4 / §7.2.
ALWAYS_AUTH_PROCEDURES = {
    "70553",       # MRI brain w/ & w/o contrast
    "72148",       # MRI lumbar spine w/o contrast
    "72141",       # MRI cervical spine w/o contrast
    "97110-EXT",   # extended PT course
    "J3489",       # zoledronic acid
    "J0178",       # aflibercept
    "43239-SURG",  # (synthetic) elective surgical bundle
}
ELECTIVE_AUTH_PROCEDURES = {
    "29881",   # knee arthroscopy w/ meniscectomy
    "29827",   # shoulder arthroscopy w/ rotator cuff repair
    "62323",   # transforaminal epidural injection, lumbar
    "64483",   # transforaminal epidural injection, single level
    "95810",   # polysomnography / sleep study
}
SENTINEL_PROCEDURE_CODES = {"", "UNKNOWN", "TBD", "NA", "NONE", "NULL", "PENDING", "MISC"}

# a fixed per-procedure clinical justification sentence - a simulation, not a
# narrative generator (agent doc §13, decision noted as a real integration point)
_JUSTIFICATION = {
    "70553": "Focal neurological deficit with red-flag features; advanced imaging indicated per payer policy.",
    "72148": "Conservative therapy 6+ weeks without improvement; imaging indicated per payer policy.",
    "72141": "Progressive radiculopathy despite conservative management; MRI indicated per payer policy.",
    "97110-EXT": "Documented functional deficits; extended course of therapy indicated to meet goals.",
    "J3489": "Osteoporosis with prior fragility fracture; parenteral therapy indicated.",
    "J0178": "Diabetic macular edema with vision impairment; anti-VEGF therapy indicated.",
    "43239-SURG": "Refractory symptoms with objective findings; elective surgical intervention indicated.",
    "29881": "Mechanical symptoms with imaging-confirmed meniscal tear; arthroscopy indicated.",
    "29827": "Full-thickness rotator cuff tear with functional loss; repair indicated.",
    "62323": "Radicular pain refractory to oral therapy; image-guided injection indicated.",
    "64483": "Single-level radicular pain refractory to conservative care; injection indicated.",
    "95810": "High pretest probability of obstructive sleep apnea; attended study indicated.",
}
_DEFAULT_JUSTIFICATION = "Service meets payer medical-necessity criteria; supporting documentation on file."

# a coarse synthetic diagnosis hint per procedure, for the drafted packet only
_DIAGNOSIS_HINT = {
    "70553": "R51.9", "72148": "M54.5", "72141": "M54.12", "97110-EXT": "M62.81",
    "J3489": "M80.08", "J0178": "E11.311", "43239-SURG": "K21.9",
    "29881": "M23.203", "29827": "M75.100", "62323": "M54.16",
    "64483": "M54.16", "95810": "G47.33",
}


@dataclass(frozen=True)
class Determination:
    status: str                 # public.prior_auth_status value
    required: bool | None       # True / False, or None when it cannot be determined
    determination_payload: dict


@dataclass(frozen=True)
class PayerResponse:
    response_status: str        # approved | info_needed | denied
    response_payload: dict
    authorization_number: str | None


def procedure_key(code: str | None) -> str:
    return re.sub(r"\s+", "", (code or "")).upper()


def _member_key(member_id: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (member_id or "")).upper()


def _is_emergency_encounter(encounter: dict) -> bool:
    return bool(encounter.get("is_emergency")) or encounter.get("place_of_service") == "emergency"


def _channel(payer: dict | None) -> str:
    return "electronic" if (payer or {}).get("prior_auth_supported", True) else "manual_fax"


def determine(encounter: dict, payer: dict | None, *, now: datetime | None = None) -> Determination:
    """Pure. `encounter` = {procedure_code, procedure_description, place_of_service,
    is_emergency}; `payer` = a payers row or None.
    docs/agents/02-prior-auth-agent.md §7.3 is the contract."""
    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    key = procedure_key(encounter.get("procedure_code"))

    def payload(status: str, *, required, reason: str, matched_rule: str | None, recheck: bool) -> dict:
        return {
            "simulated": True,
            "basis": BASIS,
            "payer": (payer or {}).get("name"),
            "procedure_code": encounter.get("procedure_code", ""),
            "required": required,
            "determination": status,
            "matched_rule": matched_rule,
            "channel": _channel(payer),
            "reason": reason,
            "recheck_recommended": recheck,
            "generated_at": generated_at,
        }

    # 1. emergency / urgent — prior auth does not apply. P5: unconditional, first. -
    if _is_emergency_encounter(encounter):
        return Determination("emergency_exempt", False, payload(
            "emergency_exempt", required=False,
            reason="emergency / urgent service — prior authorization does not apply "
                   "(EMTALA; contractual emergency exemption)",
            matched_rule="emergency_exemption", recheck=False,
        ))

    # 2. enough to determine against? -
    if key in SENTINEL_PROCEDURE_CODES or len(key) < 3:
        return Determination("insufficient_info", None, payload(
            "insufficient_info", required=None,
            reason="no procedure code on the appointment to check auth rules against",
            matched_rule=None, recheck=True,
        ))
    if not payer or not payer.get("id"):
        return Determination("insufficient_info", None, payload(
            "insufficient_info", required=None,
            reason="no payer on record to check auth rules against",
            matched_rule=None, recheck=True,
        ))

    # 3. always-auth set -
    if key in ALWAYS_AUTH_PROCEDURES:
        return Determination("required_draft", True, payload(
            "required_draft", required=True,
            reason=f"{key} always requires prior authorization",
            matched_rule="ALWAYS_AUTH_PROCEDURES", recheck=False,
        ))

    # 4. elective set, for payers that require it -
    if payer.get("prior_auth_required_default") and key in ELECTIVE_AUTH_PROCEDURES:
        return Determination("required_draft", True, payload(
            "required_draft", required=True,
            reason=f"{(payer.get('name') or 'this payer')} requires prior authorization "
                   f"for elective procedure {key}",
            matched_rule="ELECTIVE_AUTH_PROCEDURES", recheck=False,
        ))

    # 5. otherwise -
    return Determination("not_required", False, payload(
        "not_required", required=False,
        reason=f"{(payer.get('name') or 'this payer')} does not require prior "
               f"authorization for procedure {key}",
        matched_rule=None, recheck=False,
    ))


def draft_request(encounter: dict, payer: dict | None, patient: dict,
                  *, now: datetime | None = None) -> dict:
    """The drafted PA request packet. Only called when `determine` returned
    `required_draft`. Deterministic bar `drafted_at`. Agent doc §7.5."""
    drafted_at = (now or datetime.now(timezone.utc)).isoformat()
    key = procedure_key(encounter.get("procedure_code"))
    return {
        "simulated": True,
        "channel": _channel(payer),
        "payer_name": (payer or {}).get("name"),
        "member_id": patient.get("member_id", ""),
        "patient_name": patient.get("name", ""),
        "procedure_code": encounter.get("procedure_code", ""),
        "procedure_description": encounter.get("procedure_description", ""),
        "place_of_service": encounter.get("place_of_service", "office"),
        "diagnosis_hint": _DIAGNOSIS_HINT.get(key, "R69"),
        "clinical_justification": _JUSTIFICATION.get(key, _DEFAULT_JUSTIFICATION),
        "requested_units": 1,
        "drafted_at": drafted_at,
    }


def response_bucket(member_id: str | None, payer_id: str | None, procedure_code: str | None) -> int:
    """Stable 0..99 bucket for (member, payer, procedure). SHA-256 of a fixed
    string, identical across runs, machines, and Python versions."""
    seed = f"{payer_id or ''}|{_member_key(member_id)}|{procedure_key(procedure_code)}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


# width of the info_needed band above the approval threshold (agent doc §13 #6)
INFO_NEEDED_BAND = 12
# a resubmit carries added clinical documentation; the simulation models the
# improved odds by shifting the effective bucket down (agent doc §7.6)
RESUBMIT_APPROVAL_BONUS = 18


def simulate_response(encounter: dict, payer: dict, patient: dict,
                      *, is_resubmit: bool = False, now: datetime | None = None) -> PayerResponse:
    """The (simulated) payer's answer to a submitted request. Computed by the
    orchestrator only AFTER 02.submit runs (i.e. after a human approved).
    Deterministic. Agent doc §7.6.

    `is_resubmit` models "additional clinical documentation attached": the
    effective bucket is shifted down by RESUBMIT_APPROVAL_BONUS, so a resubmit
    genuinely improves the odds of approval."""
    responded_at = (now or datetime.now(timezone.utc)).isoformat()
    threshold = int(payer.get("prior_auth_approval_threshold", 80))
    raw = response_bucket(patient.get("member_id"), payer.get("id"), encounter.get("procedure_code"))
    b = max(0, raw - RESUBMIT_APPROVAL_BONUS) if is_resubmit else raw

    def payload(status: str, *, reason: str, auth_number: str | None) -> dict:
        return {
            "simulated": True,
            "response_status": status,
            "authorization_number": auth_number,
            "bucket": b,
            "raw_bucket": raw,
            "resubmit": is_resubmit,
            "threshold": threshold,
            "reason": reason,
            "responded_at": responded_at,
        }

    year = (now or datetime.now(timezone.utc)).year
    code = procedure_key(encounter.get("procedure_code")) or "NA"

    if b < threshold:
        auth_number = f"AUTH-{year}-{b:04d}-{code}"
        return PayerResponse("approved", payload(
            "approved",
            reason="request meets payer medical-necessity criteria (simulated)",
            auth_number=auth_number,
        ), auth_number)

    if b < threshold + INFO_NEEDED_BAND:
        return PayerResponse("info_needed", payload(
            "info_needed",
            reason="payer requires additional clinical documentation before a "
                   "determination (simulated)",
            auth_number=None,
        ), None)

    return PayerResponse("denied", payload(
        "denied",
        reason="request does not meet payer medical-necessity criteria on the "
               "information submitted (simulated)",
        auth_number=None,
    ), None)
