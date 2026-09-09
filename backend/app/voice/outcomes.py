"""classify_outcome() — map a Vapi end-of-call-report to a voice_reminder_status.

Pure. NEVER returns "confirmed" unless the structured end_reminder_call outcome
is literally "confirmed". Anything unrecognised / missing -> "no_answer" (which
routes to a human). NEVER reads or returns transcript text or a recording URL,
even when the payload contains them (decision §11.4).
"""
from __future__ import annotations

from dataclasses import dataclass

_OUTCOME_TO_STATUS = {
    "confirmed": "confirmed",
    "reschedule_needed": "reschedule_requested",
    "wrong_person": "wrong_person",
    "out_of_scope": "out_of_scope",
}

_COULD_NOT_CONNECT = frozenset({
    "twilio-failed-to-connect-call", "vonage-failed-to-connect-call",
    "call.failed", "pipeline-error", "provider-fault", "phone-call-provider-closed-websocket",
    "assistant-not-found", "no-phone-number", "invalid-phone-number",
})


@dataclass(frozen=True)
class ReminderOutcome:
    status: str               # a voice_reminder_status value
    outcome: str | None       # the raw end_reminder_call string, or None
    payload: dict             # {outcome, ended_reason, duration_seconds, vapi_call_status, cost}


def _first(d: dict, *paths):
    """Walk a list of dotted paths, return the first non-None value."""
    for path in paths:
        cur = d
        ok = True
        for seg in path.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


def _extract_structured_outcome(message: dict):
    """The end_reminder_call arguments can arrive in a few shapes across Vapi
    payload versions. Try them in order; the exact path is pinned at build once a
    real payload is captured (§11.8)."""
    # analysis.structuredData
    sd = _first(message, "analysis.structuredData", "analysis.structured_data")
    if isinstance(sd, dict) and sd.get("outcome"):
        return sd["outcome"]
    # toolCalls / toolWithToolCallList
    for key in ("toolCalls", "toolCallList", "toolWithToolCallList"):
        calls = message.get(key)
        if isinstance(calls, list):
            for c in calls:
                fn = c.get("function") or c.get("toolCall", {}).get("function") or {}
                name = fn.get("name") or c.get("name")
                if name == "end_reminder_call":
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        import json
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    if isinstance(args, dict) and args.get("outcome"):
                        return args["outcome"]
    # messages[] transcript of a tool call result
    for m in message.get("messages", []) or []:
        if isinstance(m, dict) and m.get("role") == "tool_call_result" and m.get("name") == "end_reminder_call":
            r = m.get("result")
            if isinstance(r, dict) and r.get("outcome"):
                return r["outcome"]
    return None


def classify_outcome(message: dict) -> ReminderOutcome:
    message = message or {}
    raw = _extract_structured_outcome(message)
    ended = _first(message, "endedReason", "call.endedReason", "ended_reason")
    duration = _first(message, "durationSeconds", "duration_seconds", "call.duration")
    vapi_status = _first(message, "status", "call.status")
    cost = _first(message, "cost", "call.cost")

    payload = {
        "outcome": raw,
        "ended_reason": ended,
        "duration_seconds": duration,
        "vapi_call_status": vapi_status,
        "cost": cost,
    }

    if raw in _OUTCOME_TO_STATUS:
        return ReminderOutcome(_OUTCOME_TO_STATUS[raw], raw, payload)

    if ended in _COULD_NOT_CONNECT:
        return ReminderOutcome("call_failed", None, payload)

    # no structured outcome, any ended reason (voicemail / no pickup / early hangup / unknown)
    return ReminderOutcome("no_answer", None, payload)
