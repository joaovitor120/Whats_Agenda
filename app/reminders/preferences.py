"""Explicit preferences replace only the selected event's reminder schedule."""

from typing import TYPE_CHECKING, TypedDict

from app.events.categories import EventCategory
from app.reminders.policies import reminder_offsets

if TYPE_CHECKING:
    from app.ai.schemas import EventIntent
    from app.db.models import Event


class ReminderPreference(TypedDict, total=False):
    reminder_minutes: int | None
    reminder_offsets_minutes: list[int] | None
    reminders_exclusive: bool


def has_reminder_preference(parsed: "EventIntent") -> bool:
    return parsed.reminder_mode == "standard" or (
        parsed.reminder_mode in {"custom", "exclusive"}
        and bool(parsed.reminder_offsets_minutes or parsed.reminder_minutes is not None)
    )


def preference_values(parsed: "EventIntent") -> ReminderPreference:
    if parsed.reminder_mode == "preserve":
        return {}
    if parsed.reminder_mode == "standard":
        return {
            "reminder_minutes": None,
            "reminder_offsets_minutes": None,
            "reminders_exclusive": False,
        }
    offsets = parsed.reminder_offsets_minutes or (
        [parsed.reminder_minutes] if parsed.reminder_minutes is not None else []
    )
    if not offsets:
        raise ValueError("Specify at least one reminder offset")
    return {
        "reminder_minutes": offsets[0] if len(offsets) == 1 and offsets[0] > 0 else None,
        "reminder_offsets_minutes": list(offsets),
        "reminders_exclusive": parsed.reminder_mode == "exclusive",
    }


def apply_reminder_preference(event: "Event", parsed: "EventIntent") -> None:
    for name, value in preference_values(parsed).items():
        setattr(event, name, value)


def reminder_label(parsed: "EventIntent", event: "Event | None" = None) -> str:
    preference = preference_values(parsed)
    if not preference and event is not None:
        preference = {
            "reminder_minutes": event.reminder_minutes,
            "reminder_offsets_minutes": event.reminder_offsets_minutes,
            "reminders_exclusive": event.reminders_exclusive,
        }
    offsets = reminder_offsets(
        parsed.category if event is None else EventCategory(event.category),
        parsed.recurrence_rule or (event.recurrence_rule if event else None),
        **preference,
    )
    leads = [int(delta.total_seconds() / 60) for delta in offsets if delta.total_seconds() > 0]
    before = ", ".join(f"{value // 60}h" if value % 60 == 0 else f"{value}min" for value in leads)
    label = before + " antes" if before else ""
    if any(delta.total_seconds() == 0 for delta in offsets):
        label += (" e " if label else "") + "no início (AGORA)"
    if preference.get("reminders_exclusive"):
        label = "Somente " + label
    return label
