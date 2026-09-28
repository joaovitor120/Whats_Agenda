"""Exact reminder choices, confirmations and recurrence with isolated test data."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.ai.anthropic import intent_schema
from app.ai.interpreter import IntentInterpreter
from app.ai.schemas import ParsedIntent
from app.core.time import utc
from app.db.models import Event, InboundMessage, OutboundMessage, Reminder, User
from app.events import conversation, review, service
from app.reminders.policies import reminder_offsets
from app.reminders.preferences import preference_values, reminder_label

ZONE = ZoneInfo("America/Sao_Paulo")
REFERENCE = datetime(2026, 9, 28, 9, tzinfo=ZONE)
START = REFERENCE + timedelta(days=2)
LEADS = [720, 60, 15]


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return REFERENCE.astimezone(tz) if tz else REFERENCE.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    for module in (conversation, review, service):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


def request(**changes):
    return ParsedIntent.model_validate(
        {
            "intent": "update_reminder",
            "target_event": "Dentista",
            "confidence": 1,
            "reminder_offsets_minutes": LEADS,
            "reminder_mode": "exclusive",
            **changes,
        }
    )


def minutes(offsets):
    return [int(offset.total_seconds() / 60) for offset in offsets]


def test_exclusive_offsets_have_no_automatic_alerts():
    parsed = request()
    assert minutes(reminder_offsets(parsed.category, **preference_values(parsed))) == LEADS
    assert reminder_label(parsed) == "Somente 12h, 1h, 15min antes"


@pytest.mark.parametrize("leads", [[30], [5], [0], [5, 0]])
def test_exclusive_five_and_start_only_when_requested(leads):
    parsed = request(reminder_offsets_minutes=leads)
    assert minutes(reminder_offsets(parsed.category, **preference_values(parsed))) == leads


def test_custom_and_legacy_preferences_keep_default_alerts():
    parsed = request(reminder_mode="custom")
    assert minutes(reminder_offsets(parsed.category, **preference_values(parsed))) == [*LEADS, 5, 0]
    old_plan = ParsedIntent(intent="update_reminder", reminder_minutes=30)
    assert old_plan.reminder_mode == "custom"
    assert minutes(reminder_offsets(old_plan.category, **preference_values(old_plan))) == [30, 5, 0]


def test_offsets_are_distinct_and_sorted():
    assert request(reminder_offsets_minutes=[15, 60, 15, 720]).reminder_offsets_minutes == LEADS


@pytest.mark.parametrize("leads", [[-1], [10081], [True], [1.5], ["60"], list(range(11))])
def test_invalid_offsets_are_rejected(leads):
    with pytest.raises(ValidationError):
        request(reminder_offsets_minutes=leads)


def test_standard_requires_an_empty_preference():
    with pytest.raises(ValidationError):
        request(reminder_mode="standard")


def test_wire_schema_does_not_exceed_claude_union_limit():
    schema = intent_schema()

    def check(node):
        if isinstance(node, list):
            return sum(check(child) for child in node)
        if not isinstance(node, dict):
            return 0
        properties = node.get("properties", {})
        if "intent" in properties:
            assert "reminder_minutes" not in properties
            assert "reminder_offsets_minutes" in properties
            assert "reminder_mode" in properties
            assert set(node["required"]) == set(properties)
        return int("anyOf" in node) + sum(check(child) for child in node.values())

    assert check(schema) <= 16


def test_draft_date_correction_preserves_exclusive_choice():
    previous = request(intent="create_event", title="Dentista", start_at=START)
    # The real Claude schema always supplies an empty list/preserve on an unrelated correction.
    correction = ParsedIntent.model_validate(
        ParsedIntent(intent="create_event", start_at=START + timedelta(days=1)).model_dump()
    )
    merged = IntentInterpreter._merge_draft(correction, previous, str(ZONE))
    assert merged.start_at == START + timedelta(days=1)
    assert merged.reminder_offsets_minutes == LEADS
    assert merged.reminder_mode == "exclusive"


def test_draft_standard_choice_clears_legacy_offset():
    previous = ParsedIntent(intent="create_event", title="Dentista", reminder_minutes=30)
    correction = ParsedIntent(intent="update_reminder", reminder_mode="standard")
    merged = IntentInterpreter._merge_draft(correction, previous, str(ZONE))
    assert preference_values(merged) == {
        "reminder_minutes": None,
        "reminder_offsets_minutes": None,
        "reminders_exclusive": False,
    }


async def incoming(session, user, text):
    item = InboundMessage(
        external_id=f"reminder-test-{uuid4()}",
        phone=user.phone,
        payload={"text": text},
        correlation_id="reminder-test",
        received_at=REFERENCE,
    )
    session.add(item)
    await session.flush()
    return item


async def event_for(session, user, **changes):
    event = Event(
        user_id=user.id,
        title="Dentista",
        start_at=START,
        category="medical_appointment",
        provider="fake",
        **changes,
    )
    session.add(event)
    await session.flush()
    await service.materialize_reminders(session, event, user.timezone)
    await session.flush()
    return event


async def pending_leads(session, event):
    await session.flush()
    reminders = (
        await session.scalars(
            select(Reminder).where(
                Reminder.event_id == event.id, Reminder.status.in_(["pending", "queued"])
            )
        )
    ).all()
    return sorted(
        (
            int((utc(item.occurrence_at) - utc(item.send_at)).total_seconds() / 60)
            for item in reminders
        ),
        reverse=True,
    )


async def test_preview_then_confirmation_cancels_extra_outboxes_only_for_target(
    db, clock, monkeypatch
):
    monkeypatch.setattr(
        conversation.IntentInterpreter, "interpret", lambda *args, **kwargs: model()
    )

    async def model():
        return request()

    async with db() as session:
        users = (await session.scalars(select(User).order_by(User.phone))).all()
        user, other = users
        event = await event_for(session, user)
        unrelated = await event_for(session, user, recurrence_rule="FREQ=DAILY")
        foreign = await event_for(session, other)
        others = {e.id: await pending_leads(session, e) for e in (unrelated, foreign)}
        reminders = (
            await session.scalars(select(Reminder).where(Reminder.event_id == event.id))
        ).all()
        extra = [
            r
            for r in reminders
            if int((utc(r.occurrence_at) - utc(r.send_at)).total_seconds() / 60) in {5, 0}
        ]
        for reminder in extra:
            reminder.status = "queued"
            session.add(
                OutboundMessage(
                    dedupe_key=f"reminder:{reminder.id}", phone=user.phone, text="old reminder"
                )
            )
        before = await pending_leads(session, event)
        # Disambiguate the same owner's second event by its title.
        unrelated.title = "Inglês"
        item = await incoming(session, user, "Lembre apenas 12h, 1h e 15min antes do Dentista")
        answer = await conversation.handle_text(session, user, item, item.payload["text"])
        assert "Somente 12h, 1h, 15min antes" in answer and "Posso salvar" in answer
        assert await pending_leads(session, event) == before
        assert event.reminder_offsets_minutes is None
        confirm = await incoming(session, user, "SIM")
        result = await conversation.handle_text(session, user, confirm, "SIM")
        assert "Somente 12h, 1h, 15min antes" in result
        assert event.reminder_offsets_minutes == LEADS and event.reminders_exclusive
        assert await pending_leads(session, event) == LEADS
        assert all(
            out.status == "cancelled"
            for out in (await session.scalars(select(OutboundMessage))).all()
        )
        for other_event in (unrelated, foreign):
            assert await pending_leads(session, other_event) == others[other_event.id]


async def test_recurring_refresh_does_not_reintroduce_default_alerts(db, clock):
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        event = await event_for(
            session,
            user,
            recurrence_rule="FREQ=DAILY",
            reminder_offsets_minutes=LEADS,
            reminders_exclusive=True,
        )
        before = await pending_leads(session, event)
        assert len(before) > 100 and set(before) == set(LEADS)
        await service.materialize_reminders(session, event, user.timezone)
        assert await pending_leads(session, event) == before
        reminders = (
            await session.scalars(select(Reminder).where(Reminder.event_id == event.id))
        ).all()
        counts = {}
        for reminder in reminders:
            counts[reminder.occurrence_at] = counts.get(reminder.occurrence_at, 0) + 1
        assert set(counts.values()) == {3}


@pytest.mark.parametrize("rename", [True, False])
async def test_rename_or_reschedule_preserves_saved_exclusive_preference(db, clock, rename):
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        event = await event_for(
            session, user, reminder_offsets_minutes=LEADS, reminders_exclusive=True
        )
        parsed = ParsedIntent(
            intent="update_event",
            target_event=event.title,
            title="Consulta",
            start_at=None if rename else START + timedelta(days=1),
            confidence=1,
        )
        item = await incoming(session, user, "alterar")
        item.plan = await service.prepare_plan(session, user, item, parsed, event.id)
        await service.apply_plan(session, user, item)
        assert event.title == "Consulta"
        assert event.reminder_offsets_minutes == LEADS and event.reminders_exclusive
        assert await pending_leads(session, event) == LEADS


async def test_restore_category_defaults_reactivates_cancelled_not_sent_reminders(db, clock):
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        event = await event_for(session, user)
        before = await pending_leads(session, event)
        sent = await session.scalar(
            select(Reminder).where(Reminder.event_id == event.id).order_by(Reminder.send_at)
        )
        sent.status = "sent"
        sent.sent_at = REFERENCE.astimezone(UTC)
        old_sent_at = utc(sent.send_at)
        item = await incoming(session, user, "custom")
        item.plan = await service.prepare_plan(session, user, item, request(), event.id)
        await service.apply_plan(session, user, item)
        standard = request(reminder_mode="standard", reminder_offsets_minutes=[])
        item = await incoming(session, user, "standard")
        item.plan = await service.prepare_plan(session, user, item, standard, event.id)
        await service.apply_plan(session, user, item)
        sent_offset = int((utc(event.start_at) - old_sent_at).total_seconds() / 60)
        assert await pending_leads(session, event) == [
            value for value in before if value != sent_offset
        ]
        assert event.reminder_offsets_minutes is None and not event.reminders_exclusive
        assert sent.status == "sent"


async def test_create_exclusive_then_correct_clock_requires_new_confirmation(
    db, clock, monkeypatch
):
    results = iter(
        [
            request(intent="create_event", title="Dentista", start_at=START),
            request(intent="create_event", title="Dentista", start_at=START + timedelta(hours=1)),
        ]
    )

    async def model(*args, **kwargs):
        return next(results)

    monkeypatch.setattr(conversation.IntentInterpreter, "interpret", model)
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        item = await incoming(session, user, "Dentista daqui a 2 dias, apenas 12h, 1h e 15min")
        answer = await conversation.handle_text(session, user, item, item.payload["text"])
        assert "Somente 12h, 1h, 15min antes" in answer
        correction = await incoming(session, user, "Na verdade às 10h")
        answer = await conversation.handle_text(
            session, user, correction, correction.payload["text"]
        )
        assert "10:00" in answer and "Posso salvar" in answer
        assert not (await session.scalars(select(Event))).all()
        confirm = await incoming(session, user, "SIM")
        await conversation.handle_text(session, user, confirm, "SIM")
        event = await session.scalar(select(Event))
        assert utc(event.start_at) == utc(START + timedelta(hours=1))
        assert await pending_leads(session, event) == LEADS


async def test_incomplete_exclusive_creation_asks_leads_instead_of_defaults(db, clock, monkeypatch):
    async def model(*args, **kwargs):
        return request(
            intent="create_event", title="Dentista", start_at=START, reminder_offsets_minutes=[]
        )

    monkeypatch.setattr(conversation.IntentInterpreter, "interpret", model)
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        item = await incoming(session, user, "Dentista, quero apenas alguns avisos")
        answer = await conversation.handle_text(session, user, item, item.payload["text"])
        assert "quantos minutos ou horas" in answer and "Posso salvar" not in answer
        assert not (await session.scalars(select(Event))).all()
