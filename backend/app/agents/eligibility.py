"""01 — eligibility-agent: the deterministic eligibility simulation.

Pure Python, no LLM, no I/O, no call-time randomness. Given a patient's identity
and their payer's config, it returns one `eligibility_status` plus a
`result_payload` explaining how it got there.

Phase 2 does NOT call a real clearinghouse. This is a documented, reproducible
simulation — the eligibility equivalent of 06's `rules.py`. The exact contract is
docs/agents/01-eligibility-agent.md §7; this module is the implementation, that
section is the spec. Keep them in lockstep.

    bucket(member_id, payer_id) = int(sha256(f"{payer_id}|{member_key}")[:8], 16) % 100

    1. not enough identity to check        -> insufficient_info   (EXPECTED for ER patients)
    2. payer unknown                        -> insufficient_info
    3. payer not on the eligibility network -> check_failed
    4. bucket < payer.active_threshold      -> verified_active
    5. otherwise                            -> verified_inactive

`insufficient_info` and `check_failed` are normal outcomes, not errors. For an
emergency they simply mean "re-check later when more patient info exists"
(Commander rule E4).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

BASIS = "deterministic-sim-v1"

# identity too thin to run a real check against
SENTINEL_MEMBER_IDS = {"", "UNKNOWN", "NA", "NONE", "NULL", "DEMO000", "PENDING", "TBD"}
SENTINEL_NAMES = {
    "", "UNKNOWN", "JOHN DOE", "JANE DOE", "DOE JOHN", "DOE JANE",
    "UNIDENTIFIED", "TRAUMA", "PATIENT UNKNOWN", "ER PATIENT",
}


@dataclass(frozen=True)
class EligibilityResult:
    status: str            # public.eligibility_status value
    result_payload: dict


def _member_key(member_id: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (member_id or "")).upper()


def _name_key(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip().upper()


def has_min_identity(patient: dict) -> bool:
    """Enough to attempt a verification at all. Deliberately strict: an
    unidentified / unconscious ER patient must fall through to insufficient_info,
    not get a bogus coverage answer."""
    key = _member_key(patient.get("member_id"))
    name = _name_key(patient.get("name"))
    return (
        key not in SENTINEL_MEMBER_IDS
        and len(key) >= 4
        and name not in SENTINEL_NAMES
        and len(name) >= 3
    )


def bucket(member_id: str | None, payer_id: str | None) -> int:
    """Stable 0..99 bucket for (member, payer). SHA-256 of a fixed string, so it
    is identical across runs, machines, and Python versions. Different payers
    give the same member a different bucket."""
    seed = f"{payer_id or ''}|{_member_key(member_id)}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def simulate(patient: dict, payer: dict | None, *, now: datetime | None = None) -> EligibilityResult:
    """Pure. `patient` = {name, member_id}; `payer` = a payers row or None.
    docs/agents/01-eligibility-agent.md §7 is the contract."""
    generated_at = (now or datetime.now(timezone.utc)).isoformat()

    def payload(status: str, *, reason: str, member_bucket: int | None,
                active_threshold: int | None, recheck: bool) -> dict:
        return {
            "simulated": True,
            "basis": BASIS,
            "checked_against": (payer or {}).get("name"),
            "member_bucket": member_bucket,
            "active_threshold": active_threshold,
            "determination": status,
            "reason": reason,
            "recheck_recommended": recheck,
            "generated_at": generated_at,
        }

    # 1. identity ----------------------------------------------------------------
    if not has_min_identity(patient):
        return EligibilityResult("insufficient_info", payload(
            "insufficient_info",
            reason="not enough patient identity to run a verification "
                   "(missing / sentinel member id or name)",
            member_bucket=None, active_threshold=None, recheck=True,
        ))

    # 2. payer known? ----------------------------------------------------------------
    if not payer or not payer.get("id"):
        return EligibilityResult("insufficient_info", payload(
            "insufficient_info",
            reason="no payer on record to verify coverage against",
            member_bucket=None, active_threshold=None, recheck=True,
        ))

    # 3. payer reachable? ----------------------------------------------------------------
    if not payer.get("eligibility_verification_supported", True):
        return EligibilityResult("check_failed", payload(
            "check_failed",
            reason=f"{payer.get('name', 'this payer')} is not on the real-time "
                   "eligibility network; the check could not be completed",
            member_bucket=None, active_threshold=None, recheck=True,
        ))

    # 4/5. coverage determination ----------------------------------------------------------------
    threshold = int(payer.get("eligibility_active_threshold", 85))
    b = bucket(patient.get("member_id"), payer.get("id"))
    if b < threshold:
        return EligibilityResult("verified_active", payload(
            "verified_active",
            reason=f"member bucket {b} is below the active threshold {threshold} for this payer",
            member_bucket=b, active_threshold=threshold, recheck=False,
        ))
    return EligibilityResult("verified_inactive", payload(
        "verified_inactive",
        reason=f"member bucket {b} is not below the active threshold {threshold} for this payer",
        member_bucket=b, active_threshold=threshold, recheck=False,
    ))
