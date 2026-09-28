"""Resolve explicit references to one recently saved event within the same owner."""

import re
from datetime import timedelta

from sqlalchemy import select

from app.ai.dates import folded_date_text
from app.ai.schemas import ParsedIntent
from app.db.models import Event, InboundMessage
from app.events.categories import EventCategory


def refers_to_recent_event(text):
    return bool(
        re.search(
            r"\b(?:corrigir|corrige|corrija|alterar|altere|altera|mudar|mude|muda|"
            r"remarcar|remarque|remarca)\s+(?:o |esse |este |aquele |meu )?"
            r"(?:ultimo )?(?:agendamento|compromisso|evento)"
            r"(?=\s+(?:para|pra|pro|as|hoje|amanha|depois|neste|nesta)\b|\s*[.!?]*$)",
            folded_date_text(text),
        )
    )


async def recent_event_draft(session, user, inbound, now):
    previous = await session.scalar(
        select(InboundMessage)
        .where(
            InboundMessage.phone == user.phone,
            InboundMessage.id < inbound.id,
            InboundMessage.status == "processed",
            InboundMessage.plan["event_id"].as_string().is_not(None)
            | (InboundMessage.plan["kind"].as_string() == "batch"),
            InboundMessage.received_at >= now - timedelta(minutes=10),
            InboundMessage.received_at <= now,
        )
        .order_by(InboundMessage.id.desc())
        .limit(1)
    )
    if not previous or not previous.plan:
        return None
    plan = previous.plan
    # A batch has several possible referents. Never silently pick one of them.
    if plan.get("kind") == "batch" or plan.get("intent", {}).get("intent") not in {
        "create_event",
        "update_event",
        "update_reminder",
    }:
        return None
    event = await session.scalar(
        select(Event).where(
            Event.id == plan.get("event_id"),
            Event.user_id == user.id,
            Event.status == "active",
        )
    )
    if event is None:
        return None
    return event.id, ParsedIntent(
        intent="update_event",
        target_event=event.title,
        category=EventCategory(event.category),
        start_at=event.start_at,
        end_at=event.end_at,
        recurrence_rule=event.recurrence_rule,
        reminder_minutes=event.reminder_minutes,
        reminder_offsets_minutes=event.reminder_offsets_minutes or [],
        reminder_mode="exclusive" if event.reminders_exclusive else "preserve",
        confidence=1.0,
    )
