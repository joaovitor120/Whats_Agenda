import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import ConversationState, Event, InboundMessage, OutboundMessage, Reminder, User
from app.events.service import find_target, list_events
from app.integrations.evolution.client import EvolutionClient
from app.workers import inbound, reminders

PHONE = "5511999999999"


async def message(db, ident, text, phone=PHONE):
    async with db() as session:
        session.add(
            InboundMessage(
                external_id=ident, phone=phone, payload={"text": text}, correlation_id=ident
            )
        )
        await session.commit()
    await inbound._process_message(ident)
    async with db() as session:
        return await session.scalar(
            select(OutboundMessage).where(OutboundMessage.dedupe_key == f"reply:{ident}")
        )


async def test_multiturn_and_duplicate_task(db):
    future = datetime.now(UTC) + timedelta(days=10)
    await message(db, "a", f"Tenho prova de cálculo dia {future:%d/%m/%Y}")
    reply = await message(db, "b", "13h")
    assert "adicionado" in reply.text
    await asyncio.gather(inbound._process_message("b"), inbound._process_message("b"))
    async with db() as session:
        events = (await session.scalars(select(Event))).all()
        assert len(events) == 1
        assert events[0].category == "exam"
        assert events[0].start_at.hour == 16  # 13:00 Sao Paulo persisted as UTC.
        assert await session.get(ConversationState, events[0].user_id) is None
        assert len((await session.scalars(select(Reminder))).all()) == 4


async def test_time_without_date_never_creates(db):
    await message(db, "a", "Tenho prova de cálculo")
    await message(db, "b", "13h")
    async with db() as session:
        assert not (await session.scalars(select(Event))).all()


async def test_weekly_confirmation_is_exact_and_single_use(db):
    await message(db, "a", "Academia toda terça às 18h")
    async with db() as session:
        event = await session.scalar(select(Event))
        assert event and event.recurrence_rule
        assert len((await session.scalars(select(Reminder))).all()) > 40
    prompt = await message(db, "b", "apaga academia")
    assert "toda a série" in prompt.text
    code = re.search(r"CONFIRMAR ([0-9A-F]+)", prompt.text)[1]
    other = await message(db, "c", f"CONFIRMAR {code}", phone="5511888888888")
    assert "cancelado" not in other.text
    reply = await message(db, "d", f"CONFIRMAR {code}")
    assert "cancelado" in reply.text
    await message(db, "e", f"CONFIRMAR {code}")
    async with db() as session:
        assert (await session.get(Event, event.id)).status == "deleted"


async def test_plain_yes_and_expired_code_do_not_delete(db):
    await message(db, "a", "Dentista amanhã às 18h")
    await message(db, "b", "apaga dentista")
    await message(db, "c", "sim")
    prompt = await message(db, "d", "apaga dentista")
    code = re.search(r"CONFIRMAR ([0-9A-F]+)", prompt.text)[1]
    async with db() as session:
        state = await session.scalar(select(ConversationState))
        state.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await message(db, "e", f"CONFIRMAR {code}")
    async with db() as session:
        assert (await session.scalar(select(Event))).status == "active"


async def test_update_multiturn_preserves_operation(db):
    await message(db, "a", "Dentista amanhã às 18h")
    future = datetime.now(UTC) + timedelta(days=4)
    await message(db, "b", f"remarca dentista para dia {future:%d/%m/%Y}")
    prompt = await message(db, "c", "14h")
    code = re.search(r"CONFIRMAR ([0-9A-F]+)", prompt.text)[1]
    await message(db, "d", f"CONFIRMAR {code}")
    async with db() as session:
        events = (await session.scalars(select(Event))).all()
        assert len(events) == 1 and events[0].start_at.hour == 17
        assert events[0].title == "Dentista"


async def test_cross_user_and_wildcard_selection(db):
    await message(db, "a", "Dentista amanhã às 18h")
    async with db() as session:
        other = await session.scalar(select(User).where(User.phone == "5511888888888"))
        owner = await session.scalar(select(User).where(User.phone == PHONE))
        for user, target in [(other, "Dentista"), (owner, "%"), (owner, "_"), (owner, "")]:
            with pytest.raises(ValueError):
                await find_target(session, user, target)
        now = datetime.now(UTC)
        assert await list_events(session, other, now, now + timedelta(days=7)) == []


async def test_outbound_atomic_claim_and_ambiguous_failure(db, monkeypatch):
    reply = await message(db, "a", "oi")
    sent = []

    async def send(self, number, text):
        sent.append(number)
        await asyncio.sleep(0.02)
        raise TimeoutError("response lost after delivery")

    monkeypatch.setattr(EvolutionClient, "send_text", send)
    await asyncio.gather(inbound._send_outbound(reply.id), inbound._send_outbound(reply.id))
    await inbound._send_outbound(reply.id)
    assert sent == [PHONE]
    async with db() as session:
        assert (await session.get(OutboundMessage, reply.id)).status == "uncertain"


async def test_due_reminders_are_enqueued_once_and_expire(db):
    await message(db, "a", "Dentista amanhã às 18h")
    async with db() as session:
        event = await session.scalar(select(Event))
        for minutes in (2, 60):
            session.add(
                Reminder(
                    event_id=event.id,
                    occurrence_at=event.start_at,
                    send_at=datetime.now(UTC) - timedelta(minutes=minutes),
                )
            )
        await session.commit()
    await asyncio.gather(reminders._dispatch_due(), reminders._dispatch_due())
    async with db() as session:
        outbox = (
            await session.scalars(
                select(OutboundMessage).where(OutboundMessage.dedupe_key.like("reminder:%"))
            )
        ).all()
        assert len(outbox) == 1 and outbox[0].phone == PHONE
        assert await session.scalar(select(Reminder.id).where(Reminder.status == "expired"))


async def test_ordering_blocks_later_messages(db):
    async with db() as session:
        session.add_all(
            [
                InboundMessage(
                    external_id=str(n), phone=PHONE, payload={"text": "oi"}, correlation_id=str(n)
                )
                for n in (1, 2)
            ]
        )
        await session.commit()
    await inbound._process_message("2")
    async with db() as session:
        assert (
            await session.scalar(select(InboundMessage).where(InboundMessage.external_id == "2"))
        ).attempts == 0
    await inbound._process_message("1")
    await inbound._process_message("2")
    async with db() as session:
        assert len((await session.scalars(select(OutboundMessage))).all()) == 2


async def test_external_success_then_local_failure_reuses_durable_plan(db, monkeypatch):
    from app.ai.interpreter import IntentInterpreter
    from app.events import service
    from app.integrations.calendars.service import fake_calendar

    original_materialize = service.materialize_reminders
    original_interpret = IntentInterpreter.interpret
    operations, interpretations = [], []
    remote = set()

    async def create(*args, operation_id, **kwargs):
        operations.append(operation_id)
        remote.add(operation_id)
        return operation_id

    async def flaky_materialize(*args):
        if len(operations) == 1:
            raise RuntimeError("Simulated database failure after remote success")
        return await original_materialize(*args)

    async def interpret(self, *args):
        interpretations.append(1)
        return await original_interpret(self, *args)

    monkeypatch.setattr(fake_calendar, "create_event", create)
    monkeypatch.setattr(service, "materialize_reminders", flaky_materialize)
    monkeypatch.setattr(IntentInterpreter, "interpret", interpret)
    await message(db, "crash", "Dentista amanha as 18h")
    await inbound._process_message("crash")
    async with db() as session:
        assert len((await session.scalars(select(Event))).all()) == 1
        item = await session.scalar(
            select(InboundMessage).where(InboundMessage.external_id == "crash")
        )
        assert item.status == "processed" and item.plan["attempted"]
    assert len(remote) == 1 and len(operations) == 2 and len(interpretations) == 1


async def test_hard_crash_on_last_attempt_becomes_reviewable(db):
    async with db() as session:
        session.add(
            InboundMessage(
                external_id="crash-final",
                phone=PHONE,
                payload={},
                correlation_id="crash-final",
                attempts=5,
                plan={"attempted": True},
            )
        )
        await session.commit()
    await inbound._process_message("crash-final")
    async with db() as session:
        assert (
            await session.scalar(
                select(InboundMessage).where(InboundMessage.external_id == "crash-final")
            )
        ).status == "needs_review"


async def test_stale_reminder_outbox_is_not_sent_after_cancellation(db, monkeypatch):
    await message(db, "a", "Dentista amanha as 18h")
    async with db() as session:
        event = await session.scalar(select(Event))
        reminder = Reminder(
            event_id=event.id,
            occurrence_at=event.start_at,
            send_at=datetime.now(UTC) - timedelta(minutes=1),
            status="cancelled",
        )
        session.add(reminder)
        await session.flush()
        out = OutboundMessage(dedupe_key=f"reminder:{reminder.id}", phone=PHONE, text="stale")
        session.add(out)
        await session.commit()
    sent = []

    async def send(*args):
        sent.append(1)

    monkeypatch.setattr(EvolutionClient, "send_text", send)
    await inbound._send_outbound(out.id)
    assert sent == []
    async with db() as session:
        assert (await session.get(OutboundMessage, out.id)).status == "cancelled"
