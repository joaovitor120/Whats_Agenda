import asyncio
from contextlib import asynccontextmanager

from redis import Redis
from sqlalchemy import text

from app.core.config import settings
from app.core.security import hash_state


@asynccontextmanager
async def sender_lock(phone):
    if settings.database_url.startswith("postgresql"):
        from app.db.session import engine

        key = int.from_bytes(bytes.fromhex(hash_state(phone))[:8], signed=True)
        async with engine.connect() as connection:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
            if not acquired:
                raise RuntimeError("Conversation is busy")
            try:
                async with asyncio.timeout(110):
                    yield
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        return
    client = Redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=3)
    # Both thread-pool calls must share the lock token. Hard task limit is below the lease.
    lock = client.lock(
        f"conversation:{hash_state(phone)}", timeout=180, blocking_timeout=2, thread_local=False
    )
    acquired = await asyncio.to_thread(lock.acquire)
    if not acquired:
        client.close()
        raise RuntimeError("Conversation is busy")
    try:
        async with asyncio.timeout(110):
            yield
    finally:
        try:
            await asyncio.to_thread(lock.release)
        finally:
            client.close()
