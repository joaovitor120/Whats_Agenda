"""Offline demonstration using an isolated in-memory database and explicit fake settings."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.models import Event, InboundMessage, Reminder, User
from app.events.conversation import handle_text
from app.integrations.evolution.client import EvolutionClient


async def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings.app_env = "test"
    settings.fake_calendar = settings.fake_llm = settings.fake_evolution = True
    settings.rate_limit_enabled = False
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    date = datetime.now(UTC) + timedelta(days=10)
    async with factory() as session:
        user = User(phone="5511999999999", timezone="America/Sao_Paulo")
        inbound = InboundMessage(
            external_id="offline-demo", phone=user.phone, payload={}, correlation_id="offline-demo"
        )
        session.add_all([user, inbound])
        await session.flush()
        reply = await handle_text(
            session, user, inbound, f"Tenho prova de cálculo dia {date:%d/%m/%Y} às 13h"
        )
        await session.commit()
        event = await session.scalar(select(Event))
        reminders = (await session.scalars(select(Reminder))).all()
        await EvolutionClient().send_text(user.phone, reply)
        print(reply)
        print(
            f"Categoria: {event.category}; lembretes futuros: {len(reminders)}; integrações: fake"
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
