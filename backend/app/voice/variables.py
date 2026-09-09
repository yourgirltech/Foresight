"""resolve_variables() — render the four Vapi dynamic variables from real rows.

The Vapi assistant's prompt is fixed in Vapi and only interpolates
{{clinic_name}}, {{patient_name}}, {{appointment_date}}, {{appointment_time}}.
Nothing else ever reaches the variable map.

Pure. Deterministic given (appointment, organization). Raises on an invalid IANA
timezone so a mis-timed call is never placed — the /due endpoint catches the
raise, records the row `error`, and escalates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class ReminderVariables:
    clinic_name: str
    patient_name: str        # first name only ("Hi, is this Maria?")
    appointment_date: str    # e.g. "Tuesday, September 15"
    appointment_time: str    # e.g. "2:30 PM"

    def as_variable_values(self) -> dict:
        return {
            "clinic_name": self.clinic_name,
            "patient_name": self.patient_name,
            "appointment_date": self.appointment_date,
            "appointment_time": self.appointment_time,
        }


def _fmt_date(dt: datetime) -> str:
    # cross-platform: strftime("%-d") is not portable, so trim the zero-pad by hand
    return f"{dt.strftime('%A, %B')} {dt.day}"


def _fmt_time(dt: datetime) -> str:
    hour12 = dt.hour % 12 or 12
    return f"{hour12}:{dt.minute:02d} {dt.strftime('%p')}"


def _parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_variables(appointment: dict, organization: dict, *, now=None) -> ReminderVariables:
    tz_name = (organization.get("timezone") or "").strip()
    if not tz_name:
        raise ValueError("organization has no timezone")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid IANA timezone {tz_name!r}") from exc

    scheduled_at = appointment.get("scheduled_at")
    if not scheduled_at:
        raise ValueError("appointment has no scheduled_at")
    local = _parse_ts(scheduled_at).astimezone(tz)

    clinic_name = (organization.get("name") or "").strip()
    if not clinic_name:
        raise ValueError("organization has no name")

    full_name = (appointment.get("patient_name") or "").strip()
    if not full_name:
        raise ValueError("appointment has no patient_name")
    first_name = full_name.split()[0]

    return ReminderVariables(
        clinic_name=clinic_name,
        patient_name=first_name,
        appointment_date=_fmt_date(local),
        appointment_time=_fmt_time(local),
    )
