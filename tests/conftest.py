import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.models import User
from app.db.session import get_session
from app.main import app
from app.workers import inbound, reminders


@pytest.fixture(autouse=True)
def fake_integrations(monkeypatch):
    for field in ("fake_llm", "fake_calendar", "fake_evolution"):
        monkeypatch.setattr(settings, field, True)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-secret")
    monkeypatch.setattr(settings, "evolution_webhook_secret", "test-webhook-secret")


@pytest.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(inbound, "SessionLocal", factory)
    monkeypatch.setattr(reminders, "SessionLocal", factory)

    async def dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    locks = {}

    @asynccontextmanager
    async def lock(phone):
        async with locks.setdefault(phone, asyncio.Lock()):
            yield

    monkeypatch.setattr(inbound, "sender_lock", lock)
    async with factory() as session:
        session.add_all([User(phone="5511999999999"), User(phone="5511888888888")])
        await session.commit()
    yield factory
    app.dependency_overrides.clear()
    await engine.dispose()
