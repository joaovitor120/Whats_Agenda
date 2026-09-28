from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.ai.interpreter import IntentInterpreter
from app.events.categories import EventCategory


@pytest.mark.asyncio
async def test_exam_intent_and_sao_paulo_timezone():
    now = datetime(2026, 9, 26, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    parsed = await IntentInterpreter().interpret(
        "Tenho prova de cálculo dia 1/10/2026 às 13h", "America/Sao_Paulo", now
    )
    assert parsed.intent == "create_event"
    assert parsed.category == EventCategory.EXAM
    assert parsed.start_at == datetime(2026, 10, 1, 13, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert "prova" in parsed.title.lower()


@pytest.mark.asyncio
async def test_missing_time_is_not_invented():
    parsed = await IntentInterpreter().interpret(
        "Tenho prova de cálculo dia 10/10/2026", "America/Sao_Paulo"
    )
    assert parsed.intent == "create_event"
    assert parsed.start_at is None
    assert "start_at" in parsed.missing_fields


@pytest.mark.asyncio
async def test_portuguese_weekday_and_written_hour():
    now = datetime(2026, 9, 26, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    parsed = await IntentInterpreter().interpret(
        "Tenho reunião com Gustavo sexta às duas da tarde", "America/Sao_Paulo", now
    )
    assert parsed.start_at is not None
    assert parsed.start_at == datetime(2026, 10, 2, 14, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


@pytest.mark.asyncio
async def test_weekly_recurring_gym_event():
    now = datetime(2026, 9, 26, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    parsed = await IntentInterpreter().interpret(
        "Marca academia toda terça às 18h", "America/Sao_Paulo", now
    )
    assert parsed.category == EventCategory.GYM
    assert parsed.start_at == datetime(2026, 9, 29, 18, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert parsed.recurrence_rule == "FREQ=WEEKLY;BYDAY=TU"
