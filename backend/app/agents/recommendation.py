"""08 — recommendation-agent.

Takes the issue list from 06 (and, in the pipeline, the plain-language write-up
from 07) and produces a single recommended action, a confidence band, and the
`low_confidence` flag the Commander keys off.

Deterministic — no LLM (decided 2026-09-02, docs/agents/00-commander.md §11.3).
The confidence formula is documented here and in docs/architecture.md alongside
06's scoring.

Action selection — the highest-priority issue present drives the action:

    1. missing_authorization  -> submit_authorization_request   (09-followup)
    2. missing_documentation  -> request_documentation          (09-followup)
    3. code_mismatch          -> resubmit_corrected_coding       (MANUAL — 00-commander.md §6.3.1)
    4. overdue_follow_up      -> payer_status_follow_up          (10-reminder)

Confidence band — how unambiguous the recommended action is:

    n issues, `driving` = the top-priority issue

    n >= 3            -> Low      compound problems; one action is unlikely to be the whole fix
    n == 2           -> Medium   a primary + a secondary issue
    n == 1 and driving severity in {high, medium} -> High
    n == 1 and driving severity == low            -> Medium

    low_confidence = (band == "Low")

A Low-confidence recommendation never becomes a one-click approval — the
Commander routes it straight to escalation (R13), and even a human "approve"
on one is rejected (R8).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .rules import Issue

# Priority order — index 0 is highest priority.
_ISSUE_PRIORITY: list[str] = [
    "missing_authorization",
    "missing_documentation",
    "code_mismatch",
    "overdue_follow_up",
]

_ACTION_FOR_ISSUE: dict[str, str] = {
    "missing_authorization": "submit_authorization_request",
    "missing_documentation": "request_documentation",
    "code_mismatch": "resubmit_corrected_coding",
    "overdue_follow_up": "payer_status_follow_up",
}

_ACTION_PHRASING: dict[str, str] = {
    "submit_authorization_request": "file a prior-authorization request with the payer",
    "request_documentation": "gather and attach the missing supporting documentation",
    "resubmit_corrected_coding": "resubmit the claim with corrected coding",
    "payer_status_follow_up": "open a payer status follow-up on the claim",
}

# Kept in sync with docs/agents/00-commander.md §6 constants.
EXECUTABLE_ACTIONS = {
    "submit_authorization_request",
    "request_documentation",
    "payer_status_follow_up",
}
MANUAL_ACTIONS = {"resubmit_corrected_coding"}


@dataclass(frozen=True)
class Recommendation:
    action_type: str
    confidence: str  # High | Medium | Low
    low_confidence: bool
    rationale: str
    cited_issue_types: list[str] = field(default_factory=list)


def _priority_sorted(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: _ISSUE_PRIORITY.index(i.issue_type))


def _confidence_band(n: int, driving: Issue) -> str:
    if n >= 3:
        return "Low"
    if n == 2:
        return "Medium"
    return "High" if driving.severity in ("high", "medium") else "Medium"


def recommend(issues: list[Issue]) -> Recommendation | None:
    """Issue list -> one recommendation. Returns None if there are no issues."""
    if not issues:
        return None

    ordered = _priority_sorted(issues)
    driving = ordered[0]
    action = _ACTION_FOR_ISSUE[driving.issue_type]
    band = _confidence_band(len(ordered), driving)

    cited = [i.issue_type for i in ordered]
    others = [t for t in cited if t != driving.issue_type]
    rationale = (
        f"The most significant issue is {driving.issue_type.replace('_', ' ')} "
        f"({driving.severity} severity). Recommended next step: "
        f"{_ACTION_PHRASING[action]}."
    )
    if others:
        rationale += (
            f" Note {len(others)} further issue(s) on this claim "
            f"({', '.join(t.replace('_', ' ') for t in others)}) that this action does not resolve."
        )
    if band == "Low":
        rationale += (
            " Confidence is Low because the claim has compound problems; a human should"
            " triage rather than approve a single automated action."
        )

    return Recommendation(
        action_type=action,
        confidence=band,
        low_confidence=(band == "Low"),
        rationale=rationale,
        cited_issue_types=cited,
    )
