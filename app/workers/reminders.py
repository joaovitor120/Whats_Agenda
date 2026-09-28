import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import settings
from app.core.time import utc
from app.db.models import (
    CalendarConnection,
    ConversationState,
    Event,
    InboundMessage,
    OAuthState,
    OutboundMessage,
    PendingConnection,
    Reminder,
    User,
)
from app.db.session import SessionLocal
from app.events.categories import EventCategory
from app.events.service import materialize_reminders
from app.reminders.policies import reminder_expiry
from app.reminders.renderer import render_reminder
from app.workers.celery_app import celery

log = structlog.get_logger()


@celery.task(name="app.workers.reminders.dispatch_due", ignore_result=True)
def dispatch_due():
    asyncio.run(_dispatch_due())


async def _dispatch_due():
    now = datetime.now(UTC)
    outbound_ids = []
    async with SessionLocal() as session:
        due_ids = (
            select(Reminder.id)
            .where(Reminder.status == "pending", Reminder.send_at <= now)
            .order_by(Reminder.send_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        # One atomic claim; outbox insertion and claim commit in the same transaction.
        due = (
            await session.scalars(
                update(Reminder)
                .where(Reminder.id.in_(due_ids), Reminder.status == "pending")
                .values(status="queued")
                .returning(Reminder)
            )
        ).all()
        for reminder in due:
            event = await session.get(Event, reminder.event_id)
            if not event or event.status != "active":
                reminder.status = "cancelled"
                continue
            if event.connection_id:
                connection = await session.get(CalendarConnection, event.connection_id)
                if not connection or not connection.active or connection.user_id != event.user_id:
                    reminder.status = "cancelled"
                    continue
            expiry = reminder_expiry(
                utc(reminder.send_at),
                utc(reminder.occurrence_at or event.start_at),
                settings.reminder_max_lateness_minutes,
            )
            if expiry <= now:
                reminder.status = "expired"
                continue
            user = await session.get(User, event.user_id)
            if user is None:
                reminder.status = "failed"
                continue
            if not settings.whatsapp_allowed(user.phone):
                reminder.status = "cancelled"
                continue
            zone = ZoneInfo(user.timezone)
            text = render_reminder(
                event.title,
                EventCategory(event.category),
                utc(reminder.occurrence_at or event.start_at).astimezone(zone),
                now.astimezone(zone),
            )
            insert = (
                sqlite_insert
                if session.bind is not None and session.bind.dialect.name == "sqlite"
                else pg_insert
            )
            result = await session.execute(
                insert(OutboundMessage)
                .values(
                    dedupe_key=f"reminder:{reminder.id}",
                    phone=user.phone,
                    text=text,
                    expires_at=expiry,
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(OutboundMessage.id)
            )
            outbound_id = result.scalar_one_or_none()
            if outbound_id is not None:
                outbound_ids.append(outbound_id)
        await session.commit()
    from app.workers.inbound import send_outbound

    for outbound_id in outbound_ids:
        try:
            send_outbound.delay(outbound_id)
        except Exception as exc:
            # The persisted outbox remains available to the periodic recovery task.
            log.warning("reminder_dispatch_deferred", error_type=type(exc).__name__)


@celery.task(name="app.workers.reminders.refresh_recurring", ignore_result=True)
def refresh_recurring():
    asyncio.run(_refresh_recurring())


async def _refresh_recurring(*, include_single=False):
    async with SessionLocal() as session:
        # Page by ID rather than materializing an unbounded database into RAM.
        after = None
        while True:
            eligible = (
                Event.recurrence_rule.is_not(None) | (Event.start_at > datetime.now(UTC))
                if include_single
                else Event.recurrence_rule.is_not(None)
            )
            query = select(Event).where(Event.status == "active", eligible)
            if after is not None:
                query = query.where(Event.id > after)
            events = (
                await session.scalars(
                    query.order_by(Event.id)
                    .limit(50)
                    .with_for_update(skip_locked=not include_single)
                )
            ).all()
            if not events:
                break
            for event in events:
                user = await session.get(User, event.user_id)
                if user:
                    await materialize_reminders(session, event, user.timezone)
            after = events[-1].id
            await session.commit()


@celery.task(name="app.workers.reminders.cleanup", ignore_result=True)
def cleanup():
    asyncio.run(_cleanup())


async def _cleanup():
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=settings.retention_days)
    async with SessionLocal() as session:
        for model in (OAuthState, PendingConnection, ConversationState):
            await session.execute(delete(model).where(model.expires_at < now))
        await session.execute(
            update(InboundMessage)
            .where(
                InboundMessage.received_at < cutoff,
                InboundMessage.status.in_(["processed", "failed", "ignored"]),
            )
            .values(payload={}, transcription=None, plan=None)
        )
        await session.execute(
            update(OutboundMessage)
            .where(
                OutboundMessage.created_at < cutoff,
                OutboundMessage.status.in_(["sent", "expired", "cancelled"]),
            )
            .values(text="")
        )
        await session.commit()
