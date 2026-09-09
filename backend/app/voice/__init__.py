"""Phase 6 — voice appointment reminders (agent 17).

17 is NOT a Commander agent. It is an n8n workflow that calls the
`/api/automation/voice-reminders/*` endpoints. This package holds the **pure,
tested** logic those endpoints use — variable rendering, consent resolution,
outcome classification — plus the shared constants. No I/O lives here.

Full spec: docs/agents/17-voice-reminder-agent.md.
"""
from __future__ import annotations

import re

from .consent import ConsentDecision, VoiceConsent, consent_gate, current_voice_consent
from .outcomes import ReminderOutcome, classify_outcome
from .variables import ReminderVariables, resolve_variables

# --- named constants (Phase 4 discipline: deterministic knobs are never inlined) ---
CONFIRMED_OUTCOME = "confirmed"

# raw end_reminder_call outcome -> voice_reminder_status
OUTCOME_TO_STATUS = {
    "confirmed": "confirmed",
    "reschedule_needed": "reschedule_requested",
    "wrong_person": "wrong_person",
    "out_of_scope": "out_of_scope",
}

# a non-confirmed terminal status -> the escalation reason_code (12-escalation table)
STATUS_TO_ESCALATION_REASON = {
    "reschedule_requested": "voice_reminder_outcome_needs_human",
    "wrong_person": "voice_reminder_outcome_needs_human",
    "out_of_scope": "voice_reminder_outcome_needs_human",
    "no_answer": "voice_reminder_no_outcome_needs_human",
    "call_failed": "voice_reminder_call_failed",
    "error": "voice_reminder_error",
    "skipped_no_consent": "voice_reminder_no_consent_needs_human",
    "skipped_no_phone": "voice_reminder_no_phone_needs_human",
}

# Vapi endedReason values that mean "the call ended with no structured outcome"
TERMINAL_ENDED_REASONS_NO_OUTCOME = frozenset({
    "customer-did-not-answer", "voicemail", "customer-busy",
    "customer-ended-call-before-outcome", "silence-timed-out",
    "no-answer", "customer-did-not-give-microphone-permission",
})

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

# decision §11.2 — a missed/failed/non-confirmed/lost reminder is ALWAYS a human
# decision. There is no automatic redial anywhere in Phase 6.
NO_AUTORETRY = True

__all__ = [
    "ReminderVariables", "resolve_variables",
    "VoiceConsent", "ConsentDecision", "current_voice_consent", "consent_gate",
    "ReminderOutcome", "classify_outcome",
    "CONFIRMED_OUTCOME", "OUTCOME_TO_STATUS", "STATUS_TO_ESCALATION_REASON",
    "TERMINAL_ENDED_REASONS_NO_OUTCOME", "E164_RE", "NO_AUTORETRY",
]
