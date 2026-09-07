"""05 — cost-estimate-agent: a No Surprises Act Good Faith Estimate.

The split is strict (agent doc §1):

    price()   — deterministic arithmetic, subtotal = Σ base_price.  NO AI.
    phrase()  — ONE Claude call that rewrites the ALREADY-COMPUTED numbers in
                plain language. It may not change, add, or remove a figure.
    the disclaimer — a versioned CONSTANT rendered verbatim (never model output).

THE NON-NEGOTIABLE (agent doc §2): a GFE under 45 CFR §149.610 is for uninsured /
self-pay individuals. `gate_reason()` refuses unless BOTH hold — staff affirmed
`self_pay` AND the patient has no active coverage. The number is computed before
the AI is called; the AI response is never parsed for a dollar amount.

05 is NOT a Commander agent. commander.py is untouched by Phase 4.
"""
from __future__ import annotations

from dataclasses import dataclass

import anthropic

from ..config import get_settings

# ---------------------------------------------------------------------------
# Federal Patient-Provider Dispute Resolution thresholds (45 CFR §149.620).
# Named, not inlined — they appear in the disclaimer, the plain template, and
# the tests. If the rule changes, change them here.
# ---------------------------------------------------------------------------
GFE_DISPUTE_THRESHOLD_USD = 400   # billed >= estimate + $400 -> the patient may dispute
GFE_DISPUTE_WINDOW_DAYS = 120     # ...within 120 days of the bill

# ---------------------------------------------------------------------------
# THE NSA GOOD FAITH ESTIMATE DISCLAIMER — a versioned compliance constant (E4).
#
# REQUIRES LEGAL SIGN-OFF BEFORE PRODUCTION. This text is drafted faithfully to
# the CMS model language for the "Right to Receive a Good Faith Estimate" notice
# and the estimate-document disclaimer, but it has NOT been reviewed by counsel.
# A lawyer / compliance consultant must approve the exact wording before any real
# patient receives an estimate. Tracked as PHASE-4.md open item O-3 and
# docs/BEFORE-PHI.md item C-3.
#
# When the wording is revised (after sign-off), bump NSA_GFE_DISCLAIMER_VERSION.
# Stored cost_estimates snapshot both the text and the version, so old estimates
# are unaffected.
# ---------------------------------------------------------------------------
NSA_GFE_DISCLAIMER_VERSION = "nsa-gfe-2026-01"

NSA_GFE_DISCLAIMER = f"""\
Your Rights and Protections — Good Faith Estimate

This Good Faith Estimate shows the costs of items and services that are
reasonably expected for your health care needs for the services listed above.
The estimate is based on information known at the time it was created. It does
not include any unknown or unexpected costs that may come up during your
treatment.

You may be charged more if complications or special circumstances occur. If this
happens, federal law allows you to dispute (appeal) the bill.

If you are billed for ${GFE_DISPUTE_THRESHOLD_USD} (four hundred dollars) or more
above this Good Faith Estimate, you have the right to dispute the bill through
the federal patient-provider dispute resolution process. You must start the
dispute process within {GFE_DISPUTE_WINDOW_DAYS} days (about 4 months) of the
date on the original bill. There is a small fee to use the dispute process. If the dispute
resolution entity agrees with you, you will pay the price on this Good Faith
Estimate. If it disagrees and sides with the provider, you will pay the higher
amount.

To learn more and get a form to start the process, go to
www.cms.gov/nosurprises or call 1-800-985-3059.

This Good Faith Estimate is not a contract. It does not require you to get the
items or services from this provider. Keep a copy of this estimate in a safe
place. You may want to compare it with any bills you get later.
"""


class PhrasingUnavailable(RuntimeError):
    """The phrasing model could not be reached — the estimate is still valid
    (numbers + disclaimer); patient_summary falls back to a plain template."""


@dataclass(frozen=True)
class EstimateLine:
    procedure_code: str
    description: str
    base_price: float

    def as_dict(self) -> dict:
        return {"procedure_code": self.procedure_code, "description": self.description,
                "base_price": round(float(self.base_price), 2)}


@dataclass(frozen=True)
class PricedEstimate:
    lines: list[EstimateLine]
    subtotal: float
    unpriced_codes: list[str]     # codes with no active procedure_prices row


# ---------------------------------------------------------------------------
# The gate (E1) — pure, so it is table-tested exhaustively over
# (self_pay, has_active_coverage). Returns None if allowed, else the refusal
# message (which the endpoint returns as a 409).
# ---------------------------------------------------------------------------
def gate_reason(self_pay: bool, active_coverages: list[dict]) -> str | None:
    if not self_pay:
        return (
            "Confirm this is a self-pay encounter to generate a Good Faith "
            "Estimate. A GFE under the No Surprises Act is for uninsured / "
            "self-pay individuals."
        )
    if active_coverages:
        payer = next(
            (c.get("payer_name") for c in active_coverages if c.get("payer_name")),
            "an active plan",
        )
        return (
            f"This patient has active coverage ({payer}). A Good Faith Estimate "
            "under the No Surprises Act is for uninsured / self-pay individuals — "
            "not for a patient with insurance."
        )
    return None


# ---------------------------------------------------------------------------
# price() — deterministic (E3). No AI, no I/O.
# ---------------------------------------------------------------------------
def price(procedure_codes: list[str], price_rows: list[dict]) -> PricedEstimate:
    by_code: dict[str, dict] = {}
    for row in price_rows:
        if row.get("active", True):
            by_code[str(row["procedure_code"])] = row

    lines: list[EstimateLine] = []
    unpriced: list[str] = []
    for raw in procedure_codes:
        code = str(raw).strip()
        if not code:
            continue
        row = by_code.get(code)
        if row is None:
            if code not in unpriced:
                unpriced.append(code)
            continue
        lines.append(EstimateLine(
            procedure_code=code,
            description=str(row.get("description", "")),
            base_price=round(float(row["base_price"]), 2),
        ))
    subtotal = round(sum(line.base_price for line in lines), 2)
    return PricedEstimate(lines=lines, subtotal=subtotal, unpriced_codes=unpriced)


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def plain_template(estimate: PricedEstimate, *, patient_name: str = "") -> str:
    """Deterministic plain-language summary used when phrase() is unavailable."""
    who = patient_name.split()[0] if patient_name.strip() else "you"
    n = len(estimate.lines)
    services = "this service" if n == 1 else f"these {n} services"
    return (
        f"Based on the services planned for the visit, the estimated cost for "
        f"{services} is {_money(estimate.subtotal)}. This is an estimate, not a "
        f"bill. The final amount may be different if the care {who} needs changes "
        f"during the visit. Please ask us if you have any questions about this "
        f"estimate."
    )


# ---------------------------------------------------------------------------
# phrase() — AI, wording only (E3). Never returns a number the caller did not
# pass in; raises PhrasingUnavailable on API error.
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You write the plain-language summary paragraph of a medical Good Faith "
    "Estimate for a self-pay patient. You are given the clinic name, the "
    "patient's name, an itemised list of services with prices, and a computed "
    "total.\n\n"
    "Rules:\n"
    "- State the ESTIMATED TOTAL only, exactly as given. Do NOT repeat the "
    "individual line-item prices — the itemised list is shown separately on the "
    "document. The only dollar figure in your text is the total.\n"
    "- Do not change, round, or recompute the total. It is fixed.\n"
    "- You may name the services in words (without their prices) so the patient "
    "knows what the estimate covers.\n"
    "- Do not mention insurance, coverage, copays, or deductibles — this patient "
    "is self-pay.\n"
    "- Do not give medical advice or speculate about additional services.\n"
    "- Two short paragraphs, warm and clear, at a 6th-8th-grade reading level: "
    "what the visit is estimated to cost and that it is an estimate, not a bill.\n"
    "- Do not restate the legal disclaimer — that is added separately.\n"
    "- Respond with ONLY the paragraph text, no headings or preamble."
)


def _prompt(estimate: PricedEstimate, *, patient_name: str, clinic_name: str) -> str:
    # Line-item PRICES are deliberately NOT passed — the model cannot restate a
    # figure it never saw. It gets the service names (for context) and the one
    # total it is allowed to state.
    services = "\n".join(
        f"- {line.description or line.procedure_code}" for line in estimate.lines
    ) or "- (no priced services)"
    return (
        f"Clinic: {clinic_name}\n"
        f"Patient: {patient_name}\n"
        f"Services covered by this estimate:\n{services}\n"
        f"Estimated total (the only figure you may state): {_money(estimate.subtotal)}\n\n"
        "Write the plain-language summary now."
    )


async def phrase(estimate: PricedEstimate, *, patient_name: str, clinic_name: str) -> tuple[str, str]:
    """Returns (summary_paragraph, model). Raises PhrasingUnavailable on error."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise PhrasingUnavailable("ANTHROPIC_API_KEY is not set — 05 phrasing cannot run")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.cost_estimate_model,
            max_tokens=600,
            system=_SYSTEM,
            messages=[{"role": "user",
                       "content": _prompt(estimate, patient_name=patient_name, clinic_name=clinic_name)}],
        )
    except anthropic.APIError as exc:
        raise PhrasingUnavailable(f"Claude API error: {exc}") from exc
    finally:
        await client.close()

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise PhrasingUnavailable("the phrasing model returned an empty response")
    return text, getattr(resp, "model", settings.cost_estimate_model)
