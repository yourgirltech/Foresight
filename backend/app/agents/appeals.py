"""11 — appeals-agent.

Drafts an appeal for a claim the payer has DENIED, so a biller can review, approve,
and send it. It extends the Phase 1 claims pipeline (06 -> 07 -> 08 -> 09/10) and,
unlike the synchronous 03/04/05, IS a Commander agent — a fourth disjoint family
(AP1-AP12, docs/agents/00-commander.md §14).

docs/agents/11-appeals-agent.md is the spec; keep this module in lockstep.

THE NON-NEGOTIABLES:

* GROUNDING (§2.1, same rule as 07 and 03). `appeal_basis()` is a PURE function
  that collects the citable grounds from the claim's real rows — 06's
  claim_issues, a recorded claims.denial_reason, actions the team already took.
  `draft_appeal()` is called ONLY with that grounds list and asserts it is
  non-empty. No grounds -> `insufficient_basis`, the model is never called, and
  the Commander routes it to a human (AP3). 11 never pads a hollow letter, never
  invents a diagnosis / code / date / document.

* NO HOLLOW FALLBACK. If the drafting model is unreachable, `AppealsUnavailable`
  propagates — the orchestrator records `error` and the Commander escalates
  (AP11). A hollow appeal is worse than none (07's discipline, not 05's template).

* THE SIMULATION IS DETERMINISTIC. `simulate_resolution()` is pure — a
  SHA-256-seeded bucket, the appeal analogue of 02's `simulate_response`. The AI
  output is stored prose only; nothing is parsed from it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import anthropic

from ..config import get_settings

# ---------------------------------------------------------------------------
# Deterministic simulation knobs — NAMED module constants, never inlined
# (Phase 4 discipline). Kept in lockstep with 11-appeals-agent.md §5.
# ---------------------------------------------------------------------------
APPEAL_BASE_WIN_THRESHOLD = 40   # base of the "approved" band, before the grounds bonus
APPEAL_GROUNDS_BONUS = 12        # added to the threshold per DISTINCT ground cited
APPEAL_MAX_WIN_THRESHOLD = 82    # cap on the approved band
APPEAL_PARTIAL_BAND = 16         # [win, win + this) -> partial; >= win + this -> denied
APPEAL_RESUBMIT_BONUS = 15       # a second-level appeal (added documentation) shifts the bucket down

_RESOLUTION_OUTCOMES = ("approved", "partial", "denied")


class AppealsUnavailable(RuntimeError):
    """The drafting model could not be reached (missing key, API error). The
    orchestrator records appeals.status = 'error' and emits appeal_error."""


@dataclass(frozen=True)
class AppealGround:
    source: str          # "rule_engine_issue" | "payer_denial_reason" | "action_taken"
    ref: str | None      # issue_type / action_type / None
    detail: str          # the human-readable fact, verbatim from the source row

    def as_dict(self) -> dict:
        return {"source": self.source, "ref": self.ref, "detail": self.detail}


@dataclass(frozen=True)
class AppealBasis:
    grounds: list[AppealGround] = field(default_factory=list)
    has_basis: bool = False
    distinct_grounds: int = 0   # distinct (source, ref) keys — drives the win threshold


@dataclass(frozen=True)
class AppealResolution:
    outcome: str            # "approved" | "partial" | "denied"
    bucket: int             # 0..99, the deterministic roll
    reversed_amount: float  # full claim amount / a deterministic fraction / 0.0
    payload: dict


# ---------------------------------------------------------------------------
# appeal_basis() — PURE (G1/G2). Collects the citable grounds from real rows.
# ---------------------------------------------------------------------------
def _was_executed(recommendations: list[dict], follow_ups: list[dict]) -> bool:
    """An action was taken on this claim if a recommendation was approved AND an
    execution agent (09/10) wrote a follow_ups row."""
    approved = any(r.get("approval_status") == "approved" for r in recommendations)
    return approved and len(follow_ups) > 0


def appeal_basis(
    claim: dict,
    issues: list[dict],
    recommendations: list[dict],
    follow_ups: list[dict],
    denial_reason: str | None,
) -> AppealBasis:
    """Pure. `grounds` is drawn ONLY from the rows passed in — never a string
    that is not already documented for this claim."""
    grounds: list[AppealGround] = []

    # a) rule-engine findings — each documented issue is a citable ground
    for issue in issues:
        it = issue.get("issue_type")
        grounds.append(AppealGround(
            "rule_engine_issue", it,
            str(issue.get("description") or it or "rule-engine finding").strip(),
        ))

    # b) the payer's stated denial reason, if recorded
    if denial_reason and denial_reason.strip():
        grounds.append(AppealGround("payer_denial_reason", None, denial_reason.strip()))

    # c) an action the team already took (an approved recommendation that executed)
    if _was_executed(recommendations, follow_ups):
        approved = next(r for r in recommendations if r.get("approval_status") == "approved")
        at = approved.get("action_type")
        grounds.append(AppealGround(
            "action_taken", at,
            f"{at} was approved and completed on this claim",
        ))

    distinct = len({(g.source, g.ref) for g in grounds})
    return AppealBasis(grounds=grounds, has_basis=len(grounds) >= 1, distinct_grounds=distinct)


# ---------------------------------------------------------------------------
# draft_appeal() — Claude, drafting only (§6.3). No fallback template.
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You draft a health-insurance claim appeal letter for a medical billing "
    "team. You are given a claim, the payer, the payer's denial reason (if "
    "recorded), and a list of GROUNDS — each a specific fact already documented "
    "in our system (a rule-engine finding, the recorded denial reason, or an "
    "action our team already took on this claim).\n\n"
    "Strict rules:\n"
    "- Ground every sentence in the GROUNDS and the claim fields provided. Do "
    "NOT invent a diagnosis, a procedure or diagnosis code, a date, a policy or "
    "authorization number, a medical-necessity rationale, or any supporting "
    "document that is not in the input.\n"
    "- If the grounds are thin, write a short, honest letter that states only "
    "what is documented. Do not pad it with unsupported assertions.\n"
    "- Do not state that documentation is attached unless a ground says it is.\n"
    "- This is a DRAFT for a biller to review, edit, and send. No signature "
    "block, no specific staff name, no letterhead.\n"
    "- Plain, professional, specific. Reference the claim id and the payer by "
    "name.\n"
    "- Respond with ONLY the letter body."
)


def _prompt(claim: dict, payer: dict, basis: AppealBasis) -> str:
    lines = "\n".join(
        f"- [{g.source}{f': {g.ref}' if g.ref else ''}] {g.detail}" for g in basis.grounds
    )
    denial = (claim.get("denial_reason") or "").strip()
    return (
        f"Claim {claim.get('claim_id')} — patient {claim.get('patient_name')}, "
        f"billed amount ${claim.get('amount')}.\n"
        f"Payer: {payer.get('name') or 'the payer'}.\n"
        f"Payer denial reason: {denial or '(not recorded)'}\n\n"
        f"GROUNDS (the only facts you may cite):\n{lines}\n\n"
        "Write the appeal letter body now."
    )


async def draft_appeal(claim: dict, payer: dict, basis: AppealBasis) -> tuple[str, str]:
    """One Claude call. Returns (letter_text, model). Raises AppealsUnavailable
    on API error. Asserts the grounds list is non-empty (G1)."""
    if not basis.grounds:
        raise ValueError("draft_appeal called with no grounds — appeal_basis must gate this")

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AppealsUnavailable("ANTHROPIC_API_KEY is not set — 11-appeals-agent cannot draft")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.appeals_model,
            max_tokens=1400,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _prompt(claim, payer or {}, basis)}],
        )
    except anthropic.APIError as exc:  # network, auth, rate limit, 5xx
        raise AppealsUnavailable(f"Claude API error: {exc}") from exc
    finally:
        await client.close()

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise AppealsUnavailable("the drafting model returned an empty response")
    return text, getattr(resp, "model", settings.appeals_model)


# ---------------------------------------------------------------------------
# simulate_resolution() — PURE, deterministic (§6.4). Analogue of 02.simulate_response.
# ---------------------------------------------------------------------------
def resolution_bucket(claim_id: str, appeal_id: str) -> int:
    """Stable 0..99 bucket for (claim, appeal). SHA-256 of a fixed string —
    identical across runs, machines, and Python versions."""
    digest = hashlib.sha256(f"{claim_id}|{appeal_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def win_threshold(distinct_grounds: int) -> int:
    """The width of the `approved` band. A stronger appeal (more distinct
    grounds) widens it, up to the cap."""
    return min(
        APPEAL_MAX_WIN_THRESHOLD,
        APPEAL_BASE_WIN_THRESHOLD + APPEAL_GROUNDS_BONUS * max(0, distinct_grounds),
    )


def simulate_resolution(
    claim: dict, appeal_id: str, basis: AppealBasis, *, is_resubmit: bool = False
) -> AppealResolution:
    """Pure, deterministic. Computed by the orchestrator only AFTER 11.submit
    runs (i.e. after a human approved). `is_resubmit` models added documentation:
    the effective bucket is shifted down by APPEAL_RESUBMIT_BONUS."""
    raw = resolution_bucket(str(claim.get("id")), str(appeal_id))
    eff = max(0, raw - APPEAL_RESUBMIT_BONUS) if is_resubmit else raw
    win = win_threshold(basis.distinct_grounds)

    amount = round(float(claim.get("amount") or 0.0), 2)
    if eff < win:
        outcome, reversed_amount = "approved", amount
    elif eff < win + APPEAL_PARTIAL_BAND:
        pct = 0.35 + (raw % 46) / 100.0          # deterministic 0.35 .. 0.80
        outcome, reversed_amount = "partial", round(amount * pct, 2)
    else:
        outcome, reversed_amount = "denied", 0.0

    return AppealResolution(
        outcome=outcome,
        bucket=raw,
        reversed_amount=reversed_amount,
        payload={
            "simulated": True,
            "outcome": outcome,
            "bucket": raw,
            "effective_bucket": eff,
            "win_threshold": win,
            "partial_band": APPEAL_PARTIAL_BAND,
            "distinct_grounds": basis.distinct_grounds,
            "is_resubmit": is_resubmit,
            "reversed_amount": reversed_amount,
            "claim_amount": amount,
        },
    )
