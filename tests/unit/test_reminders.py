from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.events.categories import EventCategory
from app.reminders.policies import reminder_times


def test_exam_and_meeting_reminder_policy_and_drop_past():
    tz = ZoneInfo("America/Sao_Paulo")
    start = datetime(2026, 10, 1, 13, tzinfo=tz)
    now = datetime(2026, 9, 26, 9, tzinfo=tz)
    exam = reminder_times(EventCategory.EXAM, start, now)
    assert exam == [
        start - timedelta(days=3),
        start - timedelta(days=1),
        start - timedelta(hours=2),
    ]
    meeting = reminder_times(EventCategory.MEETING, start, start - timedelta(minutes=20))
    assert meeting == [start - timedelta(minutes=15)]
