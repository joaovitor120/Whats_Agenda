from fastapi import APIRouter, HTTPException
from redis.asyncio import from_url
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter()


@router.get("/health/live")
async def liveness():
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness():
    redis = from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        await redis.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependencies unavailable") from exc
    finally:
        await redis.aclose()
    return {"status": "ready"}
