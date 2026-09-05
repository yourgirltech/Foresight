"""06 — analyzer-agent: the deterministic rule engine.

Pure Python, no LLM, no I/O. Given a claim's evidence flags plus its payer's
config, it returns the list of issues, a weighted risk score, and a risk level.

The exact scoring formula is documented in docs/architecture.md § "Risk scoring
(06-analyzer-agent)". It is duplicated there in prose on purpose — this module
is the implementation, that section is the contract. Keep them in lockstep.

    weight   low = 10   medium = 30   high = 50
    risk_score = min(100, sum of the weights of the issues found)
    risk_level = High  if risk_score >= 70
                 Medium if 40 <= risk_score <= 69
                 Low    if risk_score < 40

Issue -> severity (fixed in the engine; the payer config only gates whether a
check applies):

    missing_authorization   high    (payer requires auth, claim has none)
    missing_documentation   high    (payer requires docs, claim has none)
    code_mismatch           medium  (submitted coding does not match the record)
    overdue_follow_up       low     (no payer follow-up within the threshold)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

SEVERITY_WEIGHT: dict[str, int] = {"low": 10, "medium": 30, "high": 50}

# issue_type -> severity. The single source of truth for the engine.
ISSUE_SEVERITY: dict[str, str] = {
    "missing_authorization": "high",
    "missing_documentation": "high",
    "code_mismatch": "medium",
    "overdue_follow_up": "low",
}

RISK_LEVEL_HIGH_MIN = 70
RISK_LEVEL_MEDIUM_MIN = 40


@dataclass(frozen=True)
class Issue:
    issue_type: str
    severity: str
    description: str
    evidence: dict


@dataclass(frozen=True)
class AnalysisResult:
    issues: list[Issue] = field(default_factory=list)
    risk_score: int = 0
    risk_level: str = "Low"


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def risk_level_for(score: int) -> str:
    if score >= RISK_LEVEL_HIGH_MIN:
        return "High"
    if score >= RISK_LEVEL_MEDIUM_MIN:
        return "Medium"
    return "Low"


def analyze(claim: dict, payer: dict, *, now: datetime | None = None) -> AnalysisResult:
    """Run every rule against one claim + its payer config. Pure function."""
    now = now or datetime.now(timezone.utc)
    issues: list[Issue] = []

    # --- missing_authorization ------------------------------------------------
    if payer.get("authorization_required") and not claim.get("authorization_present"):
        issues.append(
            Issue(
                issue_type="missing_authorization",
                severity=ISSUE_SEVERITY["missing_authorization"],
                description=(
                    f"{payer.get('name', 'This payer')} requires prior authorization for this "
                    "service and none is recorded on the claim."
                ),
                evidence={
                    "payer_authorization_required": True,
                    "claim_authorization_present": bool(claim.get("authorization_present")),
                },
            )
        )

    # --- missing_documentation ---------------------------------------------------
    if payer.get("documentation_required") and not claim.get("documentation_present"):
        issues.append(
            Issue(
                issue_type="missing_documentation",
                severity=ISSUE_SEVERITY["missing_documentation"],
                description=(
                    f"{payer.get('name', 'This payer')} requires supporting documentation and "
                    "none is attached to the claim."
                ),
                evidence={
                    "payer_documentation_required": True,
                    "claim_documentation_present": bool(claim.get("documentation_present")),
                },
            )
        )

    # --- code_mismatch ---------------------------------------------------------
    if not claim.get("coding_matches", True):
        issues.append(
            Issue(
                issue_type="code_mismatch",
                severity=ISSUE_SEVERITY["code_mismatch"],
                description="Submitted procedure/diagnosis coding does not match the clinical record.",
                evidence={"claim_coding_matches": bool(claim.get("coding_matches", True))},
            )
        )

    # --- overdue_follow_up ---------------------------------------------------------
    threshold = payer.get("follow_up_threshold_days")
    if threshold:
        anchor = _parse_ts(claim.get("last_followup_at")) or _parse_ts(claim.get("created_at"))
        if anchor is not None:
            age_days = (now - anchor).total_seconds() / 86400.0
            if age_days > threshold:
                issues.append(
                    Issue(
                        issue_type="overdue_follow_up",
                        severity=ISSUE_SEVERITY["overdue_follow_up"],
                        description=(
                            f"No payer follow-up in {age_days:.0f} days; "
                            f"{payer.get('name', 'this payer')}'s threshold is {threshold} days."
                        ),
                        evidence={
                            "threshold_days": threshold,
                            "age_days": round(age_days, 1),
                            "anchored_on": "last_followup_at"
                            if claim.get("last_followup_at")
                            else "created_at",
                        },
                    )
                )

    raw = sum(SEVERITY_WEIGHT[i.severity] for i in issues)
    score = min(100, raw)
    return AnalysisResult(issues=issues, risk_score=score, risk_level=risk_level_for(score))
