from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.calendars.service import (
    GoogleCalendar,
    MicrosoftCalendar,
    ReconciliationRequired,
)

START = datetime(2026, 10, 1, 13, tzinfo=UTC)


async def test_google_recovers_same_operation_after_conflict(monkeypatch):
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return {"_status": 409}
        return {"id": "abc123", "extendedProperties": {"private": {"operation": "abc123"}}}

    monkeypatch.setattr("app.integrations.calendars.service.request_json", request)
    provider = GoogleCalendar("secret", "user@example.com/a")
    result = await provider.create_event(
        "Dentista", START, None, operation_id="abc123", recover_only=True
    )
    assert result == "abc123"
    assert calls[0][2]["json"]["id"] == "abc123"
    assert "user%40example.com%2Fa" in calls[0][1]


async def test_google_does_not_adopt_unrelated_event(monkeypatch):
    async def request(method, *args, **kwargs):
        return {"_status": 409} if method == "POST" else {"id": "abc123"}

    monkeypatch.setattr("app.integrations.calendars.service.request_json", request)
    with pytest.raises(ReconciliationRequired):
        await GoogleCalendar("secret").create_event("x", START, None, operation_id="abc123")


async def test_microsoft_ambiguous_create_never_posts_again(monkeypatch):
    methods = []

    async def request(method, *args, **kwargs):
        methods.append(method)
        return {"value": []}

    monkeypatch.setattr("app.integrations.calendars.service.request_json", request)
    with pytest.raises(ReconciliationRequired):
        await MicrosoftCalendar("secret").create_event(
            "x", START, None, operation_id="abc123", recover_only=True
        )
    assert methods == ["GET"]


async def test_microsoft_reconciles_by_operation(monkeypatch):
    async def request(*args, **kwargs):
        return {"value": [{"id": "remote-id"}]}

    monkeypatch.setattr("app.integrations.calendars.service.request_json", request)
    assert (
        await MicrosoftCalendar("secret").create_event(
            "x", START, None, operation_id="abc123", recover_only=True
        )
        == "remote-id"
    )


async def test_google_all_day_events_block_availability(monkeypatch):
    async def request(*args, **kwargs):
        return {
            "items": [
                {
                    "start": {"date": "2026-10-01"},
                    "end": {"date": "2026-10-02"},
                    "summary": "Ocupado",
                }
            ]
        }

    monkeypatch.setattr("app.integrations.calendars.service.request_json", request)
    result = await GoogleCalendar("secret").list_events(START, START + timedelta(days=1))
    assert result[0][1] - result[0][0] == timedelta(days=1)


async def test_graph_rejects_pagination_to_attacker(monkeypatch):
    async def request(*args, **kwargs):
        return {"value": [], "@odata.nextLink": "https://attacker.example/?$skiptoken=abc"}

    monkeypatch.setattr("app.integrations.calendars.service.request_json", request)
    with pytest.raises(ValueError):
        await MicrosoftCalendar("secret").list_events(START, START + timedelta(days=1))
