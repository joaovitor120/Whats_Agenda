"""Append an owner-scoped agenda preview without changing conversation decisions."""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.db.models import CalendarConnection, Event
from app.events.service import list_events

log = structlog.get_logger()
WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


async def append_upcoming(session, user, inbound, answer: str) -> str:
    plan = inbound.plan or {}
    if plan.get("kind") == "batch" and plan.get("completed") == len(plan.get("plans", [])):
        return answer
    if plan.get("intent", {}).get("intent") == "create_event":
        created = await session.scalar(
            select(Event.id).where(
                Event.id == plan.get("event_id"),
                Event.user_id == user.id,
                Event.status == "active",
            )
        )
        if created:
            return answer

    # Flush the primary operation before the savepoint. A failed preview must not
    # roll back an update/cancellation or commit the inbound message prematurely.
    await session.flush()
    try:
        async with session.begin_nested(), asyncio.timeout(15):
            if not settings.fake_calendar:
                connection = await session.scalar(
                    select(CalendarConnection.id).where(
                        CalendarConnection.user_id == user.id,
                        CalendarConnection.active.is_(True),
                    )
                )
                if connection is None:
                    return (
                        f"{answer}\n\n📅 Quero te mostrar seus próximos compromissos! 😊\n"
                        "Conecte seu calendário primeiro, com a ajuda do administrador."
                    )
            now = datetime.now(UTC)
            beginning = now
            upcoming: list[tuple[datetime, datetime, str]] = []
            # Stop once five are found; busy calendars need only a short query.
            for days in (7, 30, 90, 365, 730):
                ending = now + timedelta(days=days)
                events = await list_events(session, user, beginning, ending, commit_tokens=False)
                upcoming.extend(event for event in events if beginning <= event[0] < ending)
                upcoming = sorted(upcoming)[:5]
                if len(upcoming) == 5:
                    break
                beginning = ending
    except Exception as exc:
        log.warning("agenda_preview_unavailable", error_type=type(exc).__name__)
        return (
            f"{answer}\n\n📅 Não consegui consultar seus próximos compromissos agora. "
            "Tente novamente daqui a pouco. 🙂"
        )

    if not upcoming:
        preview = "📅 Nenhum compromisso futuro encontrado nos próximos 2 anos.\nQuando quiser, me envie um novo agendamento! 😊"
    else:
        count = len(upcoming)
        preview = (
            "📅 Seu próximo compromisso 😊"
            if count == 1
            else f"📅 Seus próximos {count} compromissos 😊"
        )
        zone = ZoneInfo(user.timezone)
        for start, _, title in upcoming:
            local = start.astimezone(zone)
            title = " ".join(title.split())[:180] or "Compromisso"
            preview += (
                f"\n\n📌 {title}\n"
                f"🗓️ {local:%d/%m/%Y} ({WEEKDAYS[local.weekday()]}) · 🕒 {local:%H:%M}"
            )
        preview += "\n\nPode contar comigo para organizar sua agenda! ✨"
    return f"{answer}\n\n{preview}"
