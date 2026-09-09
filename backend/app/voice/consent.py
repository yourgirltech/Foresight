"""Consent resolution — pure, fail-closed.

`current_voice_consent()` reduces a contact's append-only `patient_consents`
ledger to a single current state. `consent_gate()` turns that plus a phone number
into a go / no-go, defaulting to NO on any missing or ambiguous input.

The /due and mark-calling endpoints call these against the LIVE ledger every
time — a revocation any time before the dial stops the call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass(frozen=True)
class VoiceConsent:
    granted: bool
    event_id: str | None      # the authoritative patient_consents row id, or None
    source: str | None
    recorded_at: str | None


@dataclass(frozen=True)
class ConsentDecision:
    can_call: bool
    reason: str               # "" | "no_consent" | "consent_revoked" | "no_phone" | "bad_phone_format"


def current_voice_consent(consent_rows: list[dict]) -> VoiceConsent:
    """The current voice-channel consent = the ledger row with the greatest
    recorded_at. No rows -> not granted."""
    voice_rows = [r for r in (consent_rows or []) if r.get("channel", "voice") == "voice"]
    if not voice_rows:
        return VoiceConsent(False, None, None, None)
    latest = max(voice_rows, key=lambda r: r.get("recorded_at") or "")
    return VoiceConsent(
        granted=latest.get("state") == "granted",
        event_id=latest.get("id"),
        source=latest.get("source"),
        recorded_at=latest.get("recorded_at"),
    )


def consent_gate(phone: str | None, consent: VoiceConsent) -> ConsentDecision:
    """Fail-closed. Only an unambiguous granted-consent + valid-E.164 pair -> True."""
    if not consent.granted and consent.event_id is None:
        return ConsentDecision(False, "no_consent")
    if not consent.granted:
        return ConsentDecision(False, "consent_revoked")
    phone = (phone or "").strip()
    if not phone:
        return ConsentDecision(False, "no_phone")
    if not _E164_RE.match(phone):
        return ConsentDecision(False, "bad_phone_format")
    return ConsentDecision(True, "")
