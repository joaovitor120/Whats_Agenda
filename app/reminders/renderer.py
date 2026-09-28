from datetime import datetime
from math import ceil

from app.events.categories import EventCategory


def render_reminder(
    title: str, category: EventCategory, start_at: datetime, send_at: datetime
) -> str:
    seconds = (start_at - send_at).total_seconds()
    if seconds <= 0:
        return f"🚨 Atenção! Você tem o compromisso “{title}” marcado para AGORA! ⏰"
    minutes = max(1, ceil(seconds / 60))
    icon = (
        "🚨"
        if seconds < 3600
        else "📚"
        if category == EventCategory.EXAM
        else "💳"
        if category == EventCategory.PAYMENT
        else "⏰"
    )
    if minutes >= 1440:
        period = f"{ceil(minutes / 1440)} dia(s)"
    elif minutes >= 60:
        period = f"{ceil(minutes / 60)} hora(s)"
    else:
        period = f"{minutes} minuto(s)"
    same_day = start_at.date() == send_at.astimezone(start_at.tzinfo).date()
    when = f"HOJE às {start_at:%H:%M}" if same_day else f"dia {start_at:%d/%m às %H:%M}"
    return f"{icon} {title}: em {period}, {when}."
