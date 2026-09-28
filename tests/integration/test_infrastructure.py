"""Opt-in checks against disposable infrastructure; no production database is touched."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings
from app.core.limits import QuotaExceeded, consume
from app.core.locks import sender_lock
from app.db.base import Base
from app.db.models import Event, OutboundMessage, Reminder, User
from app.workers import reminders


@pytest.mark.skipif(
    not os.environ.get("INTEGRATION_DATABASE_URL"), reason="Disposable PostgreSQL not configured"
)
async def test_postgres_claims_and_session_lock(monkeypatch):
    url = os.environ["INTEGRATION_DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Integration database name must end in _test")
    schema = "test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(CreateSchema(schema))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(reminders, "SessionLocal", factory)
        monkeypatch.setattr(settings, "database_url", url)
        monkeypatch.setattr("app.db.session.engine", engine)
        async with factory() as session:
            user = User(phone="5511999999999")
            session.add(user)
            await session.flush()
            event = Event(
                user_id=user.id,
                title="Integration",
                start_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add(event)
            await session.flush()
            session.add(
                Reminder(
                    event_id=event.id,
                    occurrence_at=event.start_at,
                    send_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            await session.commit()
        await asyncio.gather(reminders._dispatch_due(), reminders._dispatch_due())
        async with factory() as session:
            assert len((await session.scalars(select(OutboundMessage))).all()) == 1
        await reminders._refresh_recurring()
        async with sender_lock(user.phone):
            with pytest.raises(RuntimeError):
                async with sender_lock(user.phone):
                    pytest.fail("Concurrent sender lock acquired")
        async with sender_lock(user.phone):
            pass
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await admin.dispose()


@pytest.mark.skipif(
    not os.environ.get("INTEGRATION_REDIS_URL"), reason="Disposable Redis not configured"
)
async def test_redis_atomic_quota(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", os.environ["INTEGRATION_REDIS_URL"])
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    subject = uuid4().hex

    # Long period avoids crossing a bucket boundary during a slow CI run. Entries expire automatically.
    async def bounded_attempt():
        try:
            await consume("integration", subject, 3, 60)
            return True
        except QuotaExceeded:
            return False

    assert sum(await asyncio.gather(*(bounded_attempt() for _ in range(10)))) == 3
