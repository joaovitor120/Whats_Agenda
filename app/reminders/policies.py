from datetime import timedelta

from app.events.categories import EventCategory

POLICIES: dict[EventCategory, tuple[timedelta, ...]] = {
    EventCategory.MEETING: (timedelta(hours=24), timedelta(hours=1), timedelta(minutes=15)),
    EventCategory.EXAM: (
        timedelta(days=7),
        timedelta(days=3),
        timedelta(hours=24),
        timedelta(hours=2),
    ),
    EventCategory.DEADLINE: (
        timedelta(days=7),
        timedelta(days=3),
        timedelta(hours=24),
        timedelta(hours=4),
    ),
    EventCategory.MEDICAL_APPOINTMENT: (timedelta(hours=24), timedelta(hours=2)),
    EventCategory.CLASS: (timedelta(hours=1), timedelta(minutes=15)),
    EventCategory.PERSONAL: (timedelta(hours=24), timedelta(hours=1)),
    EventCategory.GYM: (timedelta(hours=1),),
    EventCategory.TRAVEL: (
        timedelta(days=7),
        timedelta(hours=24),
        timedelta(hours=5),
        timedelta(hours=2),
    ),
    EventCategory.FLIGHT: (
        timedelta(days=7),
        timedelta(hours=24),
        timedelta(hours=5),
        timedelta(hours=2),
    ),
    EventCategory.TRANSPORT: (timedelta(hours=24), timedelta(hours=2), timedelta(minutes=30)),
    EventCategory.BIRTHDAY: (timedelta(days=7), timedelta(hours=24)),
    EventCategory.PAYMENT: (timedelta(days=7), timedelta(days=3), timedelta(hours=24)),
    EventCategory.TASK: (timedelta(hours=24), timedelta(hours=1)),
    EventCategory.PARTY: (timedelta(days=7), timedelta(hours=24), timedelta(hours=2)),
    EventCategory.INTERVIEW: (
        timedelta(days=3),
        timedelta(hours=24),
        timedelta(hours=2),
        timedelta(minutes=30),
    ),
    EventCategory.PRESENTATION: (
        timedelta(days=7),
        timedelta(days=3),
        timedelta(hours=24),
        timedelta(hours=2),
    ),
    EventCategory.OTHER: (timedelta(hours=24), timedelta(hours=1)),
}


def reminder_offsets(
    category: EventCategory,
    recurrence_rule=None,
    reminder_minutes=None,
    reminder_offsets_minutes=None,
    reminders_exclusive=False,
):
    offsets: tuple[timedelta, ...]
    if reminder_offsets_minutes is not None:
        offsets = tuple(timedelta(minutes=value) for value in reminder_offsets_minutes)
    elif reminder_minutes is not None:
        offsets = (timedelta(minutes=reminder_minutes),)
    elif recurrence_rule == "FREQ=DAILY":
        offsets = (timedelta(minutes=15),)
    else:
        offsets = POLICIES[category]
    if reminders_exclusive:
        return sorted(set(offsets), reverse=True)
    return sorted({*offsets, timedelta(minutes=5), timedelta(0)}, reverse=True)


def reminder_times(
    category: EventCategory,
    start_at,
    now,
    recurrence_rule=None,
    reminder_minutes=None,
    reminder_offsets_minutes=None,
    reminders_exclusive=False,
):
    return [
        start_at - delta
        for delta in reminder_offsets(
            category,
            recurrence_rule,
            reminder_minutes,
            reminder_offsets_minutes,
            reminders_exclusive,
        )
        if start_at - delta >= now
    ]


def reminder_expiry(send_at, occurrence_at, max_lateness_minutes):
    if send_at == occurrence_at:
        # Small grace for scheduler/network latency, without announcing an old event as NOW.
        return occurrence_at + timedelta(minutes=min(2, max_lateness_minutes))
    return min(send_at + timedelta(minutes=max_lateness_minutes), occurrence_at)
