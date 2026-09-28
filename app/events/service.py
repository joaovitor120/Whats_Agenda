"""Deterministic authorization and event mutations; the interpreter cannot select owners."""

import hashlib
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr  # type: ignore[import-untyped]
from sqlalchemy import select, update

from app.ai.schemas import ParsedIntent
from app.core.config import settings
from app.core.time import utc
from app.db.models import CalendarConnection, Event, OutboundMessage, Reminder
from app.events.categories import EventCategory
from app.events.formatting import format_local_datetime
from app.events.recurrence import recurrence_label
from app.integrations.calendars.service import get_calendar_provider, provider_for_connection
from app.reminders.policies import reminder_times
from app.reminders.preferences import (
    apply_reminder_preference,
    has_reminder_preference,
    preference_values,
    reminder_label,
)


async def active_connection(session, user):
    connections = (
        await session.scalars(
            select(CalendarConnection)
            .where(CalendarConnection.user_id == user.id, CalendarConnection.active.is_(True))
            .limit(2)
        )
    ).all()
    if len(connections) > 1:
        raise ValueError("Mais de um calendário conectado; solicite a seleção ao administrador.")
    if connections:
        if settings.fake_calendar:
            raise ValueError("Modo de calendário incompatível com a conexão.")
        return connections[0]
    if not settings.fake_calendar:
        raise ValueError("Conecte primeiro um calendário pelo administrador.")
    return None


async def owned_event(session, user, event_id):
    event = await session.scalar(
        select(Event)
        .where(Event.id == event_id, Event.user_id == user.id, Event.status == "active")
        .with_for_update()
    )
    if event is None:
        raise ValueError("O evento não está disponível para esta conta.")
    return event


class AmbiguousEvent(ValueError):
    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__("Choose one event")


def normalized_title(value: str) -> str:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )
    return " ".join(folded.split())


async def find_target(session, user, target):
    if (
        not target
        or not target.strip()
        or target.strip().lower() in {"todos", "tudo", "agenda", "eventos", "compromissos"}
    ):
        raise ValueError("Informe um único evento pelo título.")
    # Compare literal, accent-insensitive titles within this owner's bounded dataset.
    events = (
        await session.scalars(
            select(Event)
            .where(
                Event.user_id == user.id,
                Event.status == "active",
            )
            .order_by(Event.start_at, Event.id)
            .limit(1001)
        )
    ).all()
    if len(events) > 1000:
        raise ValueError("Too many events for a bounded title lookup")
    needle = normalized_title(target)
    if not needle:
        raise ValueError("Informe o nome do compromisso")
    exact = [event for event in events if normalized_title(event.title) == needle]
    matches = exact or [event for event in events if needle in normalized_title(event.title)]
    if len(matches) > 1:
        raise AmbiguousEvent(matches[:5])
    if not matches:
        raise ValueError("Não consegui identificar um único evento. Informe o título completo.")
    return matches[0]


def validate_schedule(parsed):
    now = datetime.now(UTC)
    if not parsed.start_at or not now < utc(parsed.start_at) <= now + timedelta(days=730):
        raise ValueError("Informe uma data futura, dentro dos próximos dois anos.")
    if parsed.end_at and parsed.end_at - parsed.start_at > timedelta(days=7):
        raise ValueError("A duração máxima é de sete dias.")


def is_title_only_update(parsed: ParsedIntent) -> bool:
    return (
        parsed.intent == "update_event"
        and bool(parsed.title)
        and parsed.start_at is None
        and parsed.event_date is None
        and parsed.end_at is None
        and parsed.recurrence_rule is None
    )


async def prepare_plan(session, user, inbound, parsed, target_id=None, *, item_index=None):
    if parsed.intent not in {"create_event", "update_event", "delete_event", "update_reminder"}:
        raise ValueError("Unsupported operation")
    title_only = is_title_only_update(parsed)
    if parsed.intent not in {"delete_event", "update_reminder"} and not title_only:
        validate_schedule(parsed)
    if parsed.intent == "create_event":
        if not parsed.title:
            raise ValueError("Informe o título do evento.")
        connection = await active_connection(session, user)
        key = inbound.external_id if item_index is None else f"{inbound.external_id}:{item_index}"
        event_id = str(uuid5(NAMESPACE_URL, f"{user.id}:{key}"))
    else:
        event = await owned_event(session, user, target_id)
        event_id = event.id
        connection = await event_connection(session, user, event)
        if parsed.intent == "update_reminder" and not has_reminder_preference(parsed):
            raise ValueError("Missing reminder lead time")
        if parsed.intent == "update_event":
            if title_only:
                parsed = parsed.model_copy(
                    update={
                        "start_at": utc(event.start_at),
                        "end_at": event.end_at,
                        "recurrence_rule": event.recurrence_rule,
                    }
                )
            else:
                duration = (
                    utc(event.end_at) - utc(event.start_at)
                    if event.end_at
                    else timedelta(minutes=30)
                )
                parsed = parsed.model_copy(
                    update={
                        "title": parsed.title or event.title,
                        "end_at": parsed.end_at or parsed.start_at + duration,
                        "recurrence_rule": parsed.recurrence_rule or event.recurrence_rule,
                    }
                )
    return {
        "intent": parsed.model_dump(mode="json"),
        "event_id": event_id,
        "connection_id": connection.id if connection else None,
        "provider": connection.provider if connection else "fake",
        "operation_id": hashlib.sha256(
            f"{user.id}:{inbound.external_id}:{item_index}".encode()
        ).hexdigest(),
        "attempted": False,
        "title_only": title_only,
    }


async def event_connection(session, user, event):
    if event.provider == "fake":
        if not settings.fake_calendar:
            raise ValueError("Evento de demonstração não pode alterar um calendário real.")
        return None
    connection = await session.scalar(
        select(CalendarConnection).where(
            CalendarConnection.id == event.connection_id,
            CalendarConnection.user_id == user.id,
            CalendarConnection.provider == event.provider,
            CalendarConnection.active.is_(True),
        )
    )
    if not connection:
        raise ValueError(
            "A conexão original deste evento não está ativa. Reconcilie-o antes de alterar."
        )
    return connection


def occurrences(start, rule, beginning, end=None):
    if not rule:
        return [start]
    return rrulestr(rule, dtstart=start).between(
        beginning, end or beginning + timedelta(days=365), inc=True
    )


async def materialize_reminders(session, event, timezone):
    now = datetime.now(UTC)
    existing = (await session.scalars(select(Reminder).where(Reminder.event_id == event.id))).all()
    known = {(utc(r.occurrence_at), utc(r.send_at)) for r in existing if r.occurrence_at}
    cancelled = {
        (utc(r.occurrence_at), utc(r.send_at)): r
        for r in existing
        if r.occurrence_at and r.status == "cancelled"
    }
    for occurrence in occurrences(
        utc(event.start_at).astimezone(ZoneInfo(timezone)), event.recurrence_rule, now
    ):
        for send_at in reminder_times(
            EventCategory(event.category),
            occurrence,
            now,
            event.recurrence_rule,
            event.reminder_minutes,
            event.reminder_offsets_minutes,
            event.reminders_exclusive,
        ):
            pair = (utc(occurrence), utc(send_at))
            old = cancelled.get(pair)
            if old:
                from sqlalchemy import delete

                await session.execute(
                    delete(OutboundMessage).where(
                        OutboundMessage.dedupe_key == f"reminder:{old.id}",
                        OutboundMessage.status == "cancelled",
                    )
                )
                old.status = "pending"
            if pair not in known:
                session.add(Reminder(event_id=event.id, occurrence_at=occurrence, send_at=send_at))
                known.add(pair)


async def cancel_reminders(session, event):
    ids = (await session.scalars(select(Reminder.id).where(Reminder.event_id == event.id))).all()
    if ids:
        await session.execute(
            update(OutboundMessage)
            .where(
                OutboundMessage.dedupe_key.in_([f"reminder:{ident}" for ident in ids]),
                OutboundMessage.status == "pending",
            )
            .values(status="cancelled")
        )
    await session.execute(
        update(Reminder)
        .where(Reminder.event_id == event.id, Reminder.status.in_(["pending", "queued"]))
        .values(status="cancelled")
    )


class _BatchItemJournal:
    """Keep each item's recovery journal inside the persisted inbound batch."""

    def __init__(self, inbound, index):
        self.inbound = inbound
        self.index = index

    @property
    def plan(self):
        return self.inbound.plan["plans"][self.index]

    @plan.setter
    def plan(self, value):
        plans = list(self.inbound.plan["plans"])
        plans[self.index] = value
        self.inbound.plan = {**self.inbound.plan, "plans": plans}


async def apply_plan(session, user, inbound):
    if inbound.plan.get("kind") != "batch":
        return await _apply_single_plan(session, user, inbound)
    batch = inbound.plan
    # Validate every remaining schedule before starting another external mutation.
    for plan in batch["plans"][batch["completed"] :]:
        if not plan["attempted"]:
            validate_schedule(ParsedIntent.model_validate(plan["intent"]))
    for index in range(batch["completed"], len(batch["plans"])):
        answer = await _apply_single_plan(session, user, _BatchItemJournal(inbound, index))
        inbound.plan = {
            **inbound.plan,
            "completed": index + 1,
            "replies": [*inbound.plan["replies"], answer],
        }
        # Event changes and its completed cursor commit together, preventing duplicate creates.
        await session.commit()
    return "\n\n".join(inbound.plan["replies"])


async def _apply_single_plan(session, user, inbound):
    plan = inbound.plan
    parsed = ParsedIntent.model_validate(plan["intent"])
    if parsed.intent == "update_reminder":
        event = await owned_event(session, user, plan["event_id"])
        if event.connection_id != plan["connection_id"] or event.provider != plan["provider"]:
            raise ValueError("Event connection changed")
        if not has_reminder_preference(parsed):
            raise ValueError("Missing reminder lead time")
        await cancel_reminders(session, event)
        apply_reminder_preference(event, parsed)
        await session.flush()
        await materialize_reminders(session, event, user.timezone)
        return f"🔔 Lembretes de “{event.title}” atualizados: {reminder_label(parsed, event)}. Os outros eventos mantêm suas configurações."
    if not plan["attempted"] and parsed.intent != "delete_event" and not plan.get("title_only"):
        try:
            validate_schedule(parsed)
        except ValueError:
            return "A data desta operação expirou ou ficou inválida. Envie uma nova solicitação."
    connection = None
    if plan["connection_id"] is not None:
        connection = await session.scalar(
            select(CalendarConnection).where(
                CalendarConnection.id == plan["connection_id"],
                CalendarConnection.user_id == user.id,
                CalendarConnection.active.is_(True),
            )
        )
        if connection is None or connection.provider != plan["provider"]:
            raise ValueError("A conexão desta operação não está mais ativa.")
    provider = (
        await provider_for_connection(connection, session, user_id=user.id, timezone=user.timezone)
        if connection
        else get_calendar_provider(plan["provider"])
    )
    if parsed.intent != "delete_event" and parsed.start_at is None:
        raise ValueError("Missing schedule")
    recover = plan["attempted"]
    inbound.plan = {**plan, "attempted": True}
    # Durable journal precedes any calendar mutation. A retry reuses this exact plan.
    await session.commit()
    if parsed.intent == "create_event":
        if parsed.start_at is None:
            raise ValueError("Missing schedule")
        remote_id = await provider.create_event(
            parsed.title,
            parsed.start_at,
            parsed.end_at,
            parsed.description,
            parsed.recurrence_rule,
            operation_id=plan["operation_id"],
            recover_only=recover,
        )
        event = Event(
            id=plan["event_id"],
            user_id=user.id,
            title=parsed.title,
            category=parsed.category.value,
            start_at=parsed.start_at,
            end_at=parsed.end_at,
            description=parsed.description,
            recurrence_rule=parsed.recurrence_rule,
            **preference_values(parsed),
            provider=plan["provider"],
            connection_id=plan["connection_id"],
            external_id=remote_id,
        )
        session.add(event)
        await session.flush()
        await materialize_reminders(session, event, user.timezone)
        answer = f"✅ {event.title} adicionado para {format_local_datetime(parsed.start_at.astimezone(ZoneInfo(user.timezone)))}."
        if event.recurrence_rule:
            answer += f"\n🔁 {recurrence_label(event.recurrence_rule)}."
        answer += f"\n🔔 Lembretes: {reminder_label(parsed, event)}."
        return answer
    event = await owned_event(session, user, plan["event_id"])
    if event.connection_id != plan["connection_id"] or event.provider != plan["provider"]:
        raise ValueError("Event connection changed")
    if parsed.intent == "delete_event":
        if event.external_id:
            await provider.delete_event(event.external_id)
        event.status = "deleted"
        await cancel_reminders(session, event)
        return f"🗑️ {event.title} foi cancelado."
    if parsed.start_at is None:
        raise ValueError("Missing schedule")
    if plan.get("title_only"):
        if not parsed.title:
            raise ValueError("Missing event title")
        if event.external_id:
            await provider.rename_event(event.external_id, parsed.title)
        event.title = parsed.title
        if has_reminder_preference(parsed):
            await cancel_reminders(session, event)
            apply_reminder_preference(event, parsed)
            await materialize_reminders(session, event, user.timezone)
            return f"✅ Nome alterado para “{event.title}”. A data e o horário foram mantidos.\n🔔 Lembretes: {reminder_label(parsed, event)}."
        # Keep reminder times; refresh only text that has not yet been sent.
        from app.reminders.renderer import render_reminder

        pending = (
            await session.scalars(
                select(Reminder).where(Reminder.event_id == event.id, Reminder.status == "queued")
            )
        ).all()
        now = datetime.now(UTC)
        for reminder in pending:
            await session.execute(
                update(OutboundMessage)
                .where(
                    OutboundMessage.dedupe_key == f"reminder:{reminder.id}",
                    OutboundMessage.status == "pending",
                )
                .values(
                    text=render_reminder(
                        event.title,
                        EventCategory(event.category),
                        utc(reminder.occurrence_at or event.start_at).astimezone(
                            ZoneInfo(user.timezone)
                        ),
                        now.astimezone(ZoneInfo(user.timezone)),
                    )
                )
            )
        return f"✅ Nome alterado para “{event.title}”. A data e o horário foram mantidos."
    if event.external_id:
        await provider.update_event(
            event.external_id, parsed.title, parsed.start_at, parsed.end_at, parsed.recurrence_rule
        )
    await cancel_reminders(session, event)
    event.title, event.start_at, event.end_at = parsed.title, parsed.start_at, parsed.end_at
    event.recurrence_rule = parsed.recurrence_rule
    apply_reminder_preference(event, parsed)
    await materialize_reminders(session, event, user.timezone)
    answer = f"✅ {event.title} atualizado para {format_local_datetime(parsed.start_at.astimezone(ZoneInfo(user.timezone)))}."
    if event.recurrence_rule:
        answer += f"\n🔁 {recurrence_label(event.recurrence_rule)}."
    answer += f"\n🔔 Lembretes: {reminder_label(parsed, event)}."
    return answer


async def list_events(session, user, beginning, ending, *, commit_tokens=True):
    connection = await active_connection(session, user)
    if connection:
        provider = await provider_for_connection(
            connection,
            session,
            user_id=user.id,
            timezone=user.timezone,
            commit_tokens=commit_tokens,
        )
        return await provider.list_events(beginning, ending)
    events = (
        await session.scalars(
            select(Event)
            .where(
                Event.user_id == user.id,
                Event.status == "active",
                Event.start_at < ending,
                (
                    Event.recurrence_rule.is_not(None)
                    | (Event.start_at >= beginning - timedelta(days=7))
                ),
            )
            .limit(1001)
        )
    ).all()
    if len(events) > 1000:
        raise ValueError("Muitos eventos para calcular disponibilidade.")
    result = []
    for event in events:
        start = utc(event.start_at).astimezone(ZoneInfo(user.timezone))
        duration = utc(event.end_at) - start if event.end_at else timedelta(minutes=30)
        for at in occurrences(start, event.recurrence_rule, beginning - duration, ending):
            if at < ending and at + duration > beginning:
                result.append((at, at + duration, event.title))
    return sorted(result)
