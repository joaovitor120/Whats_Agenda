import asyncio
import math
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import aliased

from app.ai.transcription import TranscriptionUnavailable, transcribe_audio
from app.core.config import settings
from app.core.limits import QuotaExceeded, consume
from app.core.locks import sender_lock
from app.core.time import utc
from app.db.models import (
    Event,
    InboundMessage,
    OutboundMessage,
    Reminder,
    User,
)
from app.db.session import SessionLocal
from app.events.categories import EventCategory
from app.events.conversation import handle_text
from app.events.service import apply_plan
from app.events.upcoming import append_upcoming
from app.integrations.evolution.client import EvolutionClient, validate_audio
from app.reminders.policies import reminder_expiry
from app.reminders.renderer import render_reminder
from app.workers.celery_app import celery

log = structlog.get_logger()
AUDIO_DISABLED_REPLY = (
    "No momento, estou atendendo apenas por texto. "
    "Por favor, envie sua solicitação em uma mensagem de texto para que eu possa ajudar. 😊"
)


@celery.task(name="app.workers.inbound.process_message", ignore_result=True)
def process_message(message_id: str):
    asyncio.run(_process_message(message_id))


@celery.task(name="app.workers.inbound.dispatch_pending", ignore_result=True)
def dispatch_pending():
    asyncio.run(_dispatch_pending())


@celery.task(name="app.workers.inbound.send_outbound", ignore_result=True)
def send_outbound(outbound_id: int):
    asyncio.run(_send_outbound(outbound_id))


async def _dispatch_pending():
    async with SessionLocal() as session:
        older = aliased(InboundMessage)
        pending = (
            await session.scalars(
                select(InboundMessage.external_id)
                .where(
                    InboundMessage.status == "queued",
                    ~select(older.id)
                    .where(
                        older.phone == InboundMessage.phone,
                        older.status == "queued",
                        older.id < InboundMessage.id,
                    )
                    .exists(),
                )
                .order_by(InboundMessage.id)
                .limit(100)
            )
        ).all()
        outbound = (
            await session.scalars(
                select(OutboundMessage.id)
                .where(OutboundMessage.status == "pending")
                .order_by(OutboundMessage.id)
                .limit(100)
            )
        ).all()
        # Sending has an ambiguous outcome after a process crash. Never automatically resend it.
        await session.execute(
            update(OutboundMessage)
            .where(
                OutboundMessage.status == "sending",
                OutboundMessage.claimed_at < datetime.now(UTC) - timedelta(minutes=3),
            )
            .values(status="uncertain")
        )
        await session.commit()
    for message_id in pending:
        process_message.delay(message_id)
    for outbound_id in outbound:
        send_outbound.delay(outbound_id)


async def _send_outbound(outbound_id):
    async with SessionLocal() as session:
        phone = await session.scalar(
            select(OutboundMessage.phone).where(OutboundMessage.id == outbound_id)
        )
    if phone:
        async with sender_lock(phone):
            await _deliver_outbound(outbound_id)


async def _deliver_outbound(outbound_id):
    async with SessionLocal() as session:
        now = datetime.now(UTC)
        item = (
            await session.execute(
                update(OutboundMessage)
                .where(OutboundMessage.id == outbound_id, OutboundMessage.status == "pending")
                .values(status="sending", claimed_at=now, attempts=OutboundMessage.attempts + 1)
                .returning(OutboundMessage)
            )
        ).scalar_one_or_none()
        if item is None:
            return
        if not settings.whatsapp_allowed(item.phone):
            item.status = "cancelled"
            if item.dedupe_key.startswith("reminder:"):
                await session.execute(
                    update(Reminder)
                    .where(Reminder.id == int(item.dedupe_key.split(":")[1]))
                    .values(status="cancelled")
                )
            await session.commit()
            return
        if item.expires_at and utc(item.expires_at) <= now:
            item.status = "expired"
            if item.dedupe_key.startswith("reminder:"):
                await session.execute(
                    update(Reminder)
                    .where(Reminder.id == int(item.dedupe_key.split(":")[1]))
                    .values(status="expired")
                )
            await session.commit()
            return
        # Destinations originate in persisted identities; never in the model output.
        user = await session.scalar(select(User).where(User.phone == item.phone))
        if user is None:
            item.status = "failed"
            await session.commit()
            return
        if item.dedupe_key.startswith("reminder:"):
            reminder = await session.get(Reminder, int(item.dedupe_key.split(":")[1]))
            event = await session.get(Event, reminder.event_id) if reminder else None
            if (
                not reminder
                or reminder.status != "queued"
                or not event
                or event.status != "active"
                or event.user_id != user.id
            ):
                item.status = "cancelled"
                await session.commit()
                return
            if (
                reminder_expiry(
                    utc(reminder.send_at),
                    utc(reminder.occurrence_at or event.start_at),
                    settings.reminder_max_lateness_minutes,
                )
                <= now
            ):
                item.status = reminder.status = "expired"
                await session.commit()
                return
            # Render at delivery too: a queued reminder can cross local midnight.
            zone = ZoneInfo(user.timezone)
            item.text = render_reminder(
                event.title,
                EventCategory(event.category),
                utc(reminder.occurrence_at or event.start_at).astimezone(zone),
                now.astimezone(zone),
            )
        phone, text = user.phone, item.text
        await session.commit()
        try:
            await EvolutionClient().send_text(phone, text)
        except Exception:
            item.status = "uncertain"
            log.warning("outbound_requires_reconciliation", outbound_id=outbound_id)
        else:
            item.status = "sent"
        if item.dedupe_key.startswith("reminder:"):
            await session.execute(
                update(Reminder)
                .where(Reminder.id == int(item.dedupe_key.split(":")[1]))
                .values(status=item.status, sent_at=now if item.status == "sent" else None)
            )
        await session.commit()


async def _queue_reply(session, inbound, phone, text):
    key = f"reply:{inbound.external_id}"
    existing = await session.scalar(
        select(OutboundMessage.id).where(OutboundMessage.dedupe_key == key)
    )
    if existing:
        return existing
    user = await session.scalar(select(User).where(User.phone == phone))
    if user is not None:
        text = await append_upcoming(session, user, inbound, text)
    item = OutboundMessage(
        dedupe_key=key,
        phone=phone,
        text=text[:10000],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(item)
    await session.flush()
    return item.id


async def _process_message(message_id):
    async with SessionLocal() as session:
        phone = await session.scalar(
            select(InboundMessage.phone).where(InboundMessage.external_id == message_id)
        )
    if not phone:
        return
    async with sender_lock(phone):
        async with SessionLocal() as session, AsyncExitStack() as presence:
            inbound = await session.scalar(
                select(InboundMessage).where(InboundMessage.external_id == message_id)
            )
            if not inbound or inbound.status != "queued":
                return
            if not settings.whatsapp_allowed(phone):
                inbound.status = "ignored"
                inbound.payload = {}
                inbound.transcription = None
                await session.commit()
                return
            earlier = await session.scalar(
                select(InboundMessage.id)
                .where(
                    InboundMessage.phone == phone,
                    InboundMessage.status == "queued",
                    InboundMessage.id < inbound.id,
                )
                .limit(1)
            )
            if earlier:
                return
            if inbound.attempts >= 5:
                inbound.status = "needs_review" if inbound.plan else "failed"
                await session.commit()
                return
            inbound.attempts += 1
            await session.commit()
            await presence.enter_async_context(EvolutionClient().typing(phone))
            outbound_id = None
            try:
                user = await session.scalar(select(User).where(User.phone == phone))
                if user is None:
                    user = User(
                        phone=phone,
                        timezone=settings.default_timezone,
                        display_name=settings.whatsapp_allowed_contacts[phone].strip(),
                    )
                    session.add(user)
                    await session.flush()
                else:
                    user.display_name = settings.whatsapp_allowed_contacts[phone].strip()
                if inbound.plan:
                    answer = await apply_plan(session, user, inbound)
                elif inbound.payload.get("message_type") == "audio" and not settings.audio_enabled:
                    answer = AUDIO_DISABLED_REPLY
                elif await session.scalar(
                    select(InboundMessage.id)
                    .where(InboundMessage.phone == phone, InboundMessage.status == "needs_review")
                    .limit(1)
                ):
                    answer = "Existe uma operação pendente de revisão. O administrador precisa reconciliá-la antes de novas ações."
                else:
                    try:
                        text = await _message_text(inbound, user)
                    except TranscriptionUnavailable:
                        answer = "Não consegui entender seu áudio com clareza ou a transcrição está indisponível. 🎤 Pode repetir em um áudio curto ou enviar por texto, por favor? 🙂"
                    except QuotaExceeded:
                        answer = "Você atingiu o limite diário de áudio. 🎤 Por favor, envie sua solicitação por texto enquanto isso. 🙂"
                    else:
                        answer = await handle_text(session, user, inbound, text)
                outbound_id = await _queue_reply(session, inbound, user.phone, answer)
                inbound.status = "processed"
                await session.commit()
                log.info("message_processed", correlation_id=inbound.correlation_id)
            except Exception as exc:
                outbound_id = None
                await session.rollback()
                inbound = await session.scalar(
                    select(InboundMessage).where(InboundMessage.external_id == message_id)
                )
                if inbound is None:
                    return
                if inbound.attempts >= 5:
                    inbound.status = "needs_review" if inbound.plan else "failed"
                    outbound_id = await _queue_reply(
                        session,
                        inbound,
                        phone,
                        "Não consegui concluir sua solicitação. O administrador precisa verificar antes de tentar novamente.",
                    )
                    await session.commit()
                log.warning("message_attempt_failed", error_type=type(exc).__name__)

            if outbound_id is not None:
                try:
                    # The reply is durable and this conversation lock is already held.
                    await _deliver_outbound(outbound_id)
                except Exception as exc:
                    log.warning("immediate_delivery_failed", error_type=type(exc).__name__)


async def _message_text(inbound, user):
    if inbound.payload.get("message_type") != "audio":
        return inbound.payload.get("text") or ""
    if inbound.transcription:
        return inbound.transcription
    media = inbound.payload.get("media") or {}
    # Reserve the declared duration, then charge any extra measured in the actual bytes.
    reserved = max(1, math.ceil(media["seconds"]))
    await _consume_audio_seconds(user.id, reserved)
    audio = await EvolutionClient().download_media(media)
    measured = math.ceil(validate_audio(audio, media["mimetype"]))
    if measured > reserved:
        await _consume_audio_seconds(user.id, measured - reserved)
    text = await transcribe_audio(audio, media["mimetype"])
    inbound.transcription = text
    return text


async def _consume_audio_seconds(user_id, seconds):
    await consume("audio-seconds", user_id, settings.audio_seconds_per_day, 86400, seconds)
    await consume("audio-global", "instance", 36000, 86400, seconds)
