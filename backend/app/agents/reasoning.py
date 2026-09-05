"""07 — reasoning-agent.

Takes 06's structured issue list and explains it in plain language a biller can
act on. Calls the Claude API (real key, from the environment — never hardcoded).

Strictly grounded: the system prompt forbids inventing issues, codes, amounts,
dates, or payer rules that are not in the input, and forbids recommending an
action (that is 08's job). If the model is unavailable the agent raises
`ReasoningUnavailable`; the orchestrator turns that into an `agent.error`
trigger and the Commander escalates (R5).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from ..config import get_settings


class ReasoningUnavailable(RuntimeError):
    """The reasoning model could not be reached (missing key, API error)."""


@dataclass(frozen=True)
class ReasoningResult:
    summary: str
    detail: list[dict]  # [{"issue_type": ..., "explanation": ...}, ...]


_SYSTEM = (
    "You are a revenue-cycle analyst assistant for a healthcare clinic. You are "
    "given a claim and a list of issues that a deterministic rule engine has "
    "ALREADY identified. Explain those issues in plain, specific language a "
    "biller can act on.\n\n"
    "Strict rules:\n"
    "- Ground every sentence in the issues provided. Do NOT invent issues, "
    "procedure codes, dollar amounts, dates, or payer rules that are not in the "
    "input.\n"
    "- Do NOT recommend an action, a next step, or a decision — a separate step "
    "does that. You only explain what each issue means and why it puts the "
    "claim's reimbursement at risk.\n"
    "- If the issue list is empty, say the claim has no rule-engine findings.\n"
    "- Keep it tight: a 1-2 sentence overall summary, then 2-3 sentences per "
    "issue."
)


def _prompt(claim: dict, payer: dict, issues: list[dict]) -> str:
    if issues:
        lines = "\n".join(
            f"{n}. {i['issue_type']} (severity: {i['severity']}) — {i['description']}"
            for n, i in enumerate(issues, 1)
        )
    else:
        lines = "(none)"
    return (
        f"Claim {claim.get('claim_id')} — patient {claim.get('patient_name')}, "
        f"billed amount ${claim.get('amount')}.\n"
        f"Payer: {payer.get('name')}.\n"
        f"Rule-engine risk: {claim.get('risk_level')} ({claim.get('risk_score')}/100).\n\n"
        f"Issues found ({len(issues)}):\n{lines}\n\n"
        "Respond with ONLY a JSON object (no markdown, no prose around it):\n"
        '{\n'
        '  "summary": "<1-2 sentences: what is wrong with this claim overall>",\n'
        '  "issues": [\n'
        '    {"issue_type": "<one of the issue_type values above>", '
        '"explanation": "<2-3 sentences grounded strictly in that issue>"}\n'
        "  ]\n"
        "}\n"
        "Include exactly one \"issues\" entry per issue above, in the same order."
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start : end + 1])


async def explain(claim: dict, payer: dict, issues: list[dict]) -> ReasoningResult:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ReasoningUnavailable(
            "ANTHROPIC_API_KEY is not set — 07-reasoning-agent cannot run"
        )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.reasoning_model,
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _prompt(claim, payer, issues)}],
        )
    except anthropic.APIError as exc:  # network, auth, rate limit, 5xx
        raise ReasoningUnavailable(f"Claude API error: {exc}") from exc
    finally:
        await client.close()

    text = "".join(b.text for b in resp.content if b.type == "text").strip()

    try:
        parsed = _extract_json(text)
        summary = str(parsed.get("summary", "")).strip()
        detail = [
            {"issue_type": str(d.get("issue_type", "")), "explanation": str(d.get("explanation", "")).strip()}
            for d in parsed.get("issues", [])
            if isinstance(d, dict)
        ]
    except (ValueError, json.JSONDecodeError):
        # The model answered but not as JSON — keep the prose, lose the per-issue split.
        summary = text
        detail = []

    if not summary:
        summary = text or "No explanation was produced."
    return ReasoningResult(summary=summary, detail=detail)
