import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.models import InboundMessage
from app.main import app


def payload(**changes):
    body = {
        "event": "messages.upsert",
        "instance": settings.evolution_instance_name,
        "data": {
            "key": {"id": "once", "remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False},
            "messageTimestamp": int(time.time()),
            "message": {"conversation": "Oi"},
        },
    }
    body.update(changes)
    return body


@pytest.mark.asyncio
async def test_webhook_auth_and_deduplication(db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/webhooks/evolution", json=payload())).status_code == 401
        assert (
            await client.post("/webhooks/evolution/test-webhook-secret", json=payload())
        ).status_code == 404
        headers = {"Authorization": "Bearer test-webhook-secret"}
        first = await client.post("/webhooks/evolution", json=payload(), headers=headers)
        second = await client.post("/webhooks/evolution", json=payload(), headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["queued"] == 1 and second.json()["queued"] == 0
    async with db() as session:
        messages = (await session.scalars(select(InboundMessage))).all()
        assert len(messages) == 1
        assert messages[0].external_id == f"{settings.evolution_instance_name}:once"


@pytest.mark.parametrize(
    "kind",
    [
        "old",
        "future",
        "missing_time",
        "wrong_instance",
        "list",
        "oversized",
        "text_object",
        "invalid_json",
    ],
)
async def test_webhook_rejects_bad_inputs(db, kind):
    body = payload()
    if kind == "old":
        body["data"]["messageTimestamp"] -= 3600
    elif kind == "future":
        body["data"]["messageTimestamp"] += 3600
    elif kind == "missing_time":
        body["data"].pop("messageTimestamp")
    elif kind == "wrong_instance":
        body["instance"] = "attacker-instance"
    elif kind == "list":
        body = []
    elif kind == "text_object":
        body["data"]["message"]["conversation"] = {"phone": "attacker"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kwargs = {"json": body}
        if kind == "oversized":
            kwargs = {"content": b"x" * 256001}
        if kind == "invalid_json":
            kwargs = {"content": b"{"}
        response = await client.post(
            "/webhooks/evolution", headers={"Authorization": "Bearer test-webhook-secret"}, **kwargs
        )
        assert response.status_code == (413 if kind == "oversized" else 400)
    async with db() as session:
        assert not (await session.scalars(select(InboundMessage))).all()
