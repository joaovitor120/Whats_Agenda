"""Regression coverage for the September 27 conversation, with isolated calendar data."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.ai.dates import anchor_relative_day, relative_date
from app.ai.interpreter import IntentInterpreter
from app.ai.schemas import ParsedIntent
from app.core.config import settings
from app.db.models import ConversationState, Event, InboundMessage, User
from app.events import conversation, review, service
from app.events.recent import recent_event_draft, refers_to_recent_event

ZONE = ZoneInfo("America/Sao_Paulo")
REFERENCE = datetime(2026, 9, 27, 17, 0, tzinfo=ZONE)
BIKE = "Avaliação de Mecânica da Bicicleta será no domingo, neste domingo, às 18 horas."
CORRECTION = "Corrigir o agendamento para domingo dia 27 de 9 de 2026 a 18 horas."


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return REFERENCE.astimezone(tz) if tz else REFERENCE.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    for module in (conversation, review, service):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)


@pytest.mark.parametrize("phrase", ["neste domingo", "nesse domingo", "este domingo", "hoje"])
def test_same_sunday_includes_today(phrase):
    assert relative_date(phrase, REFERENCE) == REFERENCE.date()


def test_correct_model_date_preserves_clock_and_duration():
    wrong = ParsedIntent(
        intent="create_event",
        title="Avaliação de Mecânica da Bicicleta",
        start_at=REFERENCE.replace(day=4, month=10, hour=18),
        end_at=REFERENCE.replace(day=4, month=10, hour=19),
        confidence=1,
    )
    fixed = anchor_relative_day(wrong, BIKE, REFERENCE)
    assert fixed.start_at == REFERENCE.replace(hour=18)
    assert fixed.end_at == REFERENCE.replace(hour=19)
    assert fixed.event_date == REFERENCE.date()


def test_past_time_does_not_move_to_next_week(clock):
    wrong = ParsedIntent(
        intent="create_event", title="Estudo", start_at=REFERENCE.replace(day=4, month=10, hour=16)
    )
    fixed = anchor_relative_day(wrong, "Estudo neste domingo às 16 horas", REFERENCE)
    assert fixed.start_at == REFERENCE.replace(hour=16)
    with pytest.raises(ValueError, match="futura"):
        service.validate_schedule(fixed)


def test_batch_dates_are_not_overwritten_by_one_expression():
    parsed = ParsedIntent(
        intent="create_events",
        events=[
            {"intent": "create_event", "title": "A", "start_at": "2026-09-27T18:00:00-03:00"},
            {"intent": "create_event", "title": "B", "start_at": "2026-09-28T18:00:00-03:00"},
        ],
    )
    assert anchor_relative_day(parsed, "A hoje às 18h e B amanhã às 18h", REFERENCE) == parsed


def test_time_only_is_kept_without_inventing_today_then_day_completes_it():
    previous = ParsedIntent(intent="create_event", title="Bicicleta", confidence=1)
    guessed = ParsedIntent(
        intent="create_event", title="Bicicleta", confidence=1, start_at=REFERENCE.replace(hour=18)
    )
    pending = IntentInterpreter._finalize(
        guessed, previous, "O cobreu será às 18 horas.", REFERENCE, str(ZONE)
    )
    assert pending.start_at is None and pending.event_date is None
    assert pending.event_time == "18:00"
    answer = ParsedIntent(
        intent="create_event", title="Bicicleta", confidence=1, event_date=REFERENCE.date()
    )
    complete = IntentInterpreter._finalize(answer, pending, "Neste domingo", REFERENCE, str(ZONE))
    assert complete.start_at == REFERENCE.replace(hour=18)


async def test_time_only_followup_asks_only_for_day(db, clock, monkeypatch):
    async def model(message):
        return ParsedIntent(
            intent="create_event",
            title="Bicicleta",
            confidence=1,
            start_at=REFERENCE.replace(hour=18),
        )

    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr("app.ai.anthropic.interpret_with_claude", model)
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        await review.store_draft(
            session, user, ParsedIntent(intent="create_event", title="Bicicleta")
        )
        text = "O cobreu será às 18 horas."
        item = await incoming(session, user, text)
        answer = await conversation.handle_text(session, user, item, text)
        assert "qual é o dia?" in answer
        assert not (await session.scalars(select(Event))).all()


@pytest.mark.parametrize(
    "text",
    [
        "Corrigir o agendamento Dentista para amanhã",
        "Alterar o evento de outra pessoa",
        "Marcar um evento amanhã",
    ],
)
def test_named_or_new_event_is_not_a_recent_reference(text):
    assert not refers_to_recent_event(text)


async def incoming(session, user, text):
    item = InboundMessage(
        external_id=f"regression-{len((await session.scalars(select(InboundMessage))).all())}",
        phone=user.phone,
        payload={"text": text},
        correlation_id="regression",
        received_at=REFERENCE,
    )
    session.add(item)
    await session.flush()
    return item


async def test_real_interpreter_boundary_then_confirmation(db, clock, monkeypatch):
    async def model(message):
        return ParsedIntent(
            intent="create_event",
            title="Avaliação de Mecânica da Bicicleta",
            start_at=REFERENCE.replace(month=10, day=4, hour=18),
            confidence=1,
        )

    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr("app.ai.anthropic.interpret_with_claude", model)
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        item = await incoming(session, user, BIKE)
        answer = await conversation.handle_text(session, user, item, BIKE)
        assert "27/09/2026" in answer and "18:00" in answer and "Posso salvar" in answer
        assert not (await session.scalars(select(Event))).all()
        item.status = "processed"
        confirmation = await incoming(session, user, "SIM")
        await conversation.handle_text(session, user, confirmation, "SIM")
        confirmation.status = "processed"
        await session.commit()
        events = (await session.scalars(select(Event))).all()
        assert len(events) == 1
        assert events[0].start_at.astimezone(ZONE) == REFERENCE.replace(hour=18)


@pytest.mark.parametrize(
    "ownership,age,batch,expected",
    [
        (True, 1, False, True),
        (True, 11, False, False),
        (False, 1, False, False),
        (True, 1, True, False),
    ],
)
async def test_recent_target_owner_expiry_and_batch(db, ownership, age, batch, expected):
    async with db() as session:
        users = (await session.scalars(select(User).order_by(User.phone))).all()
        user, other = users
        event = Event(
            user_id=user.id if ownership else other.id,
            title="Bicicleta",
            category="other",
            start_at=REFERENCE.replace(month=10, day=4, hour=18),
            provider="fake",
        )
        session.add(event)
        await session.flush()
        previous = await incoming(session, user, "SIM")
        previous.received_at = REFERENCE - timedelta(minutes=age)
        previous.status = "processed"
        previous.plan = (
            {"kind": "batch", "plans": []}
            if batch
            else {
                "intent": {"intent": "create_event"},
                "event_id": event.id,
                "attempted": True,
            }
        )
        acknowledgment = await incoming(session, user, "Obrigado")
        acknowledgment.status = "processed"
        acknowledgment.plan = None
        current = await incoming(session, user, CORRECTION)
        found = await recent_event_draft(session, user, current, REFERENCE)
        assert bool(found) is expected


async def test_followup_corrects_recent_event_only_after_new_confirmation(db, clock, monkeypatch):
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        event = Event(
            user_id=user.id,
            title="Bicicleta",
            category="other",
            start_at=REFERENCE.replace(month=10, day=4, hour=18),
            provider="fake",
        )
        session.add(event)
        await session.flush()
        previous = await incoming(session, user, "SIM")
        previous.status = "processed"
        previous.plan = {
            "intent": {"intent": "create_event"},
            "event_id": event.id,
            "attempted": True,
        }
        item = await incoming(session, user, CORRECTION)

        async def model(text, timezone, now, *, draft_context=None, **kwargs):
            assert draft_context.target_event == "Bicicleta"
            return draft_context.model_copy(update={"start_at": REFERENCE.replace(hour=18)})

        monkeypatch.setattr(conversation.IntentInterpreter, "interpret", staticmethod(model))
        answer = await conversation.handle_text(session, user, item, CORRECTION)
        pending = await session.get(ConversationState, user.id)
        assert "Posso salvar" in answer and "27/09/2026" in answer
        assert pending.state["plan"]["event_id"] == event.id
        assert event.start_at.astimezone(ZONE).date().isoformat() == "2026-10-04"
        confirmation = await incoming(session, user, "SIM")
        await conversation.handle_text(session, user, confirmation, "SIM")
        assert event.start_at.astimezone(ZONE) == REFERENCE.replace(hour=18)
        assert len((await session.scalars(select(Event))).all()) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Realizar estudo para elaboração de um memorial discritivo técnico para o projeto de nova bicicleta elétrica neste domingo às 20 horas.",
        "Agedar estudo técnico para as 20 horas, hoje.",
    ],
)
async def test_study_request_reaches_confirmation(db, clock, monkeypatch, text):
    async def model(message):
        return ParsedIntent(
            intent="create_event",
            title="Estudo técnico",
            confidence=1,
            start_at=REFERENCE.replace(month=10, day=4, hour=20),
        )

    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr("app.ai.anthropic.interpret_with_claude", model)
    async with db() as session:
        user = await session.scalar(select(User).where(User.phone == "5511999999999"))
        item = await incoming(session, user, text)
        answer = await conversation.handle_text(session, user, item, text)
        assert "27/09/2026" in answer and "20:00" in answer and "Posso salvar" in answer
        assert not (await session.scalars(select(Event))).all()
