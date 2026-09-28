import asyncio
import json
import time
from uuid import uuid4

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.limits import QuotaExceeded, consume
from app.core.security import verify_secret
from app.db.models import InboundMessage
from app.db.session import get_session
from app.integrations.evolution.normalizer import normalize_webhook

router = APIRouter(prefix="/webhooks/evolution")
log = structlog.get_logger()


def dispatch_received(message_ids: list[str]):
    from app.workers.inbound import process_message

    for message_id in message_ids:
        try:
            process_message.delay(message_id)
        except Exception as exc:
            # The committed rows remain available for Beat recovery.
            log.warning("immediate_dispatch_failed", error_type=type(exc).__name__)
            return


@router.post("")
async def receive(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    authorization = request.headers.get("authorization", "")
    if not settings.evolution_webhook_secret or not verify_secret(
        authorization, f"Bearer {settings.evolution_webhook_secret}"
    ):
        raise HTTPException(401, "Unauthorized")
    try:
        await consume("webhook-ip", request.client.host if request.client else "unknown", 120, 60)
        await consume("webhook-instance", settings.evolution_instance_name, 1000, 60)
        raw = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 256_000:
                    raise HTTPException(413, "Payload too large")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("instance") != settings.evolution_instance_name
        ):
            raise ValueError("Invalid instance")
        messages = normalize_webhook(payload)
        queued = 0
        message_ids = []
        for message in messages:
            if (
                message.timestamp is None
                or not -30 <= time.time() - message.timestamp <= settings.webhook_max_age_seconds
            ):
                raise ValueError("Expired or missing message timestamp")
            external_id = f"{settings.evolution_instance_name}:{message.message_id}"
            if await session.scalar(
                select(InboundMessage.id).where(InboundMessage.external_id == external_id)
            ):
                continue
            await consume("phone-minute", message.phone, settings.messages_per_minute, 60)
            await consume("phone-day", message.phone, settings.messages_per_day, 86400)
            dialect = session.bind.dialect.name if session.bind else "postgresql"
            statement = (sqlite_insert if dialect == "sqlite" else pg_insert)(
                InboundMessage
            ).values(
                external_id=external_id,
                phone=message.phone,
                payload={
                    "push_name": message.push_name,
                    "text": message.text,
                    "message_type": message.message_type,
                    "media": message.media,
                },
                correlation_id=str(uuid4()),
            )
            inserted = await session.scalar(
                statement.on_conflict_do_nothing(index_elements=["external_id"]).returning(
                    InboundMessage.id
                )
            )
            queued += inserted is not None
            if inserted is not None:
                message_ids.append(external_id)
        await session.commit()
    except QuotaExceeded as exc:
        raise HTTPException(429, "Rate limit exceeded") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid webhook payload") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Webhook temporarily unavailable") from exc
    # Publish only committed rows, after the response; Beat remains the recovery path.
    if message_ids:
        background_tasks.add_task(dispatch_received, message_ids)
    return {"accepted": True, "queued": queued}
