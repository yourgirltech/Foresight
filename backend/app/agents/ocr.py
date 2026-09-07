"""03 — insurance-card OCR agent.

Turns one photo of an insurance card into four structured coverage fields, each
confidence-flagged. Calls Claude's vision capability directly (the same
`ANTHROPIC_API_KEY` already wired for 07) — there is no OCR vendor and no fake
response: the model reads the actual uploaded image.

docs/agents/03-ocr-agent.md is the spec; keep this module in lockstep with it.

THE NON-NEGOTIABLE (spec §2): this is a DATA-ENTRY AID, not an autonomous
action. Nothing here writes an appointment or patient record. `extract()`
returns a draft; `classify_extraction()` decides — deterministically — whether a
human must fill a blank before anything can be pre-filled. The only write-back
path is the human-triggered POST /api/card-scans/{id}/confirm endpoint.

03 is NOT a Commander agent (docs/PHASE-4.md): a synchronous request/response
tool, no workflow state, no routing. commander.py is untouched by Phase 4.
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass

import anthropic

from ..config import get_settings

# ---------------------------------------------------------------------------
# Image retention after a human confirms a scan.
#
# PROVISIONAL DEFAULT FOR THE DEMO / PILOT PHASE — REQUIRES COMPLIANCE REVIEW
# BEFORE PRODUCTION. A stored insurance-card image is real PHI. We keep it for a
# short window after confirm so a reviewer can re-check a corrected field
# against the picture, then a cleanup job deletes the Storage object (the
# extracted text stays on card_scans). The number below is a placeholder for
# that policy decision — legal / compliance must set the real retention period
# and sign off before any real patient's card is processed. A rejected scan's
# image is deleted immediately (retention 0); this constant governs *confirm*.
CONFIRM_IMAGE_RETENTION_DAYS = 90

# The four fields 03 extracts — nothing inferred beyond the card face.
FIELDS = ("member_id", "group_number", "payer_name", "plan_type")

# plan_type is the only optional one: a card may simply not print it.
OPTIONAL_FIELDS = ("plan_type",)

FLOOR_RANK = {"low": 0, "medium": 1, "high": 2}

# Bucket limit mirrors the card-scans Storage bucket (10 MiB) and the allow-list.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = ("image/jpeg", "image/png", "image/webp", "image/heic")


class OcrUnavailable(RuntimeError):
    """The vision model could not be reached (missing key, API error)."""


class UnreadableImage(ValueError):
    """The uploaded bytes are not a decodable image / not an allowed type."""


@dataclass(frozen=True)
class CardExtraction:
    fields: dict          # {member_id, group_number, payer_name, plan_type} — str or None
    confidence: dict      # {<field>: {"confidence": "high|medium|low", "legible": bool, "absent": bool}}
    model: str = ""

    def to_dict(self) -> dict:
        return {"fields": dict(self.fields), "confidence": dict(self.confidence)}


# ---------------------------------------------------------------------------
# The confidence gate — pure, deterministic, exhaustively tested (spec §6.4).
# ---------------------------------------------------------------------------
def classify_extraction(ext: CardExtraction, *, floor: str = "medium") -> str:
    """`pending` -> 'extracted' iff EVERY field is present, legible, and at or
    above the confidence floor; otherwise 'needs_review'.

    The model's self-reported confidence never silently promotes a field to
    "trusted" — a value below the floor, illegible, or missing always forces a
    human to look. `plan_type` is exempt only when the model flags it as not
    printed on the card (`absent: true`).
    """
    if floor not in FLOOR_RANK:
        floor = "medium"
    for name in FIELDS:
        value = ext.fields.get(name)
        meta = ext.confidence.get(name) or {}
        if name in OPTIONAL_FIELDS and value is None and meta.get("absent") is True:
            continue
        if value is None or str(value).strip() == "":
            return "needs_review"
        if meta.get("legible") is not True:
            return "needs_review"
        if FLOOR_RANK.get(meta.get("confidence", "low"), 0) < FLOOR_RANK[floor]:
            return "needs_review"
    return "extracted"


# ---------------------------------------------------------------------------
# Image handling — validate + normalise before the vision call.
# ---------------------------------------------------------------------------
_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}


def prepare_image(image_bytes: bytes, mime: str) -> tuple[bytes, str, str]:
    """Confirm the bytes decode as an image of an allowed type and are within
    the size cap. Returns (bytes, mime, file_extension). Raises UnreadableImage.

    We do NOT transcode: the bytes are stored and sent to the model as-is so the
    reviewer sees exactly what was captured. HEIC is passed straight through
    (Pillow may not decode it without a plugin — the vision API accepts it).
    """
    mime = (mime or "").lower().split(";")[0].strip()
    if mime not in ALLOWED_MIME:
        raise UnreadableImage(f"unsupported image type {mime!r}")
    if not image_bytes:
        raise UnreadableImage("empty upload")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise UnreadableImage(
            f"image is {len(image_bytes)} bytes, over the {MAX_IMAGE_BYTES}-byte limit"
        )
    if mime != "image/heic":
        try:
            from PIL import Image  # noqa: PLC0415 — optional dep, imported lazily

            with Image.open(io.BytesIO(image_bytes)) as im:
                im.verify()
        except Exception as exc:  # noqa: BLE001 — any decode failure is the same outcome
            raise UnreadableImage(f"could not decode the image: {exc}") from exc
    return image_bytes, mime, _EXT_BY_MIME[mime]


# ---------------------------------------------------------------------------
# The vision call.
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You extract insurance-card fields for a medical front desk. You are given "
    "one image of an insurance card. Return ONLY these four fields: member_id, "
    "group_number, payer_name, plan_type.\n\n"
    "Rules:\n"
    "- Extract ONLY what is visibly printed on this card. Read it; do not reason "
    "about what it 'should' be.\n"
    "- If a field is not present on the card, or is not clearly legible (blur, "
    "glare, cropping, low resolution), set its value to null and legible: false. "
    "NEVER infer, NEVER guess, NEVER substitute a typical or common value. A "
    "wrong value is far worse than null.\n"
    "- When a field is simply not printed on the card at all (common for "
    "plan_type), set value null, legible: false, and absent: true.\n"
    "- For each field report confidence as 'high' (clearly legible, no "
    "ambiguity), 'medium' (legible but some ambiguity — a character that could "
    "be 0/O or 1/I), or 'low' (barely legible).\n"
    "- payer_name is the insurer's name exactly as printed. Do not expand "
    "abbreviations or add a state / region that is not printed.\n"
    "- plan_type is one of HMO / PPO / EPO / POS / Medicare Advantage / "
    "Medicaid if printed; null + absent: true otherwise.\n"
    "- Do not read any other card (pharmacy, dental) or the back of the card "
    "unless that is the image provided."
)

_FIELD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "legible": {"type": "boolean"},
        "absent": {"type": "boolean"},
    },
    "required": ["confidence", "legible", "absent"],
}

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "properties": {name: {"type": ["string", "null"]} for name in FIELDS},
            "required": list(FIELDS),
        },
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {name: _FIELD_SCHEMA for name in FIELDS},
            "required": list(FIELDS),
        },
    },
    "required": ["fields", "confidence"],
}

_USER_TEXT = (
    "Extract the four fields from this insurance card. Respond with a single "
    "JSON object of the shape "
    '{"fields": {"member_id": ..., "group_number": ..., "payer_name": ..., '
    '"plan_type": ...}, "confidence": {"member_id": {"confidence": ..., '
    '"legible": ..., "absent": ...}, ...}}. Every field must appear in both '
    "objects. Use null for anything you cannot clearly read."
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


def _coerce(parsed: dict, model: str) -> CardExtraction:
    raw_fields = parsed.get("fields") or {}
    raw_conf = parsed.get("confidence") or {}
    fields: dict = {}
    confidence: dict = {}
    for name in FIELDS:
        value = raw_fields.get(name)
        if isinstance(value, str):
            value = value.strip() or None
        elif value is not None:
            value = str(value)
        fields[name] = value

        meta = raw_conf.get(name) or {}
        conf = meta.get("confidence")
        confidence[name] = {
            "confidence": conf if conf in FLOOR_RANK else "low",
            "legible": bool(meta.get("legible")) and value is not None,
            "absent": bool(meta.get("absent")),
        }
    return CardExtraction(fields=fields, confidence=confidence, model=model)


async def extract(image_bytes: bytes, mime: str) -> CardExtraction:
    """Send the card image to Claude vision and return the four fields, each
    confidence-flagged. Raises OcrUnavailable if the model cannot be reached."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise OcrUnavailable(
            "ANTHROPIC_API_KEY is not set — 03-ocr-agent cannot run"
        )

    _, norm_mime, _ = prepare_image(image_bytes, mime)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": norm_mime, "data": b64}},
        {"type": "text", "text": _USER_TEXT},
    ]

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        try:
            resp = await client.messages.create(
                model=settings.ocr_model,
                max_tokens=1200,
                system=_SYSTEM,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
        except (TypeError, anthropic.BadRequestError):
            # Older SDK without output_config, or the model rejected the schema —
            # fall back to a plain call and parse the JSON out of the text (07's path).
            resp = await client.messages.create(
                model=settings.ocr_model,
                max_tokens=1200,
                system=_SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
    except anthropic.APIError as exc:  # network, auth, rate limit, 5xx
        raise OcrUnavailable(f"Claude vision API error: {exc}") from exc
    finally:
        await client.close()

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        parsed = _extract_json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise OcrUnavailable(
            f"03 could not parse a JSON extraction from the model response: {exc}"
        ) from exc
    return _coerce(parsed, getattr(resp, "model", settings.ocr_model))
