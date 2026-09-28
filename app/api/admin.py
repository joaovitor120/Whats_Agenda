from ipaddress import ip_address, ip_network

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.core.limits import QuotaExceeded, consume
from app.core.security import verify_secret
from app.integrations.evolution.client import EvolutionClient

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(request: Request):
    try:
        address = ip_address(request.client.host if request.client else "")
        allowed = any(
            address in ip_network(net.strip()) for net in settings.admin_allowed_networks.split(",")
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(403, "Administrative network required")
    try:
        await consume("admin", str(address), 20, 60)
    except QuotaExceeded as exc:
        raise HTTPException(429, "Rate limit exceeded") from exc
    except Exception as exc:
        raise HTTPException(503, "Authentication temporarily unavailable") from exc
    if not verify_secret(request.headers.get("x-admin-api-key", ""), settings.admin_api_key):
        raise HTTPException(401, "Unauthorized")
    structlog.get_logger().info("admin_access", action=request.url.path)


@router.post("/evolution/setup", dependencies=[Depends(require_admin)])
async def evolution_setup():
    client = EvolutionClient()
    await client.create_instance_if_needed()
    base = settings.evolution_webhook_base_url or settings.public_base_url
    await client.configure_webhook(f"{base.rstrip('/')}/webhooks/evolution")
    return {"connection": await client.get_connection_status(), "qr": await client.get_qr_code()}


@router.get("/operations", dependencies=[Depends(require_admin)])
async def operation_status():
    from sqlalchemy import select

    from app.db.models import InboundMessage, OutboundMessage
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        inbound = (
            (
                await session.execute(
                    select(InboundMessage.id, InboundMessage.status, InboundMessage.attempts)
                    .where(InboundMessage.status.in_(["failed", "needs_review"]))
                    .limit(100)
                )
            )
            .mappings()
            .all()
        )
        outbound = (
            (
                await session.execute(
                    select(OutboundMessage.id, OutboundMessage.status)
                    .where(OutboundMessage.status == "uncertain")
                    .limit(100)
                )
            )
            .mappings()
            .all()
        )
        return {
            "inbound": [dict(row) for row in inbound],
            "outbound": [dict(row) for row in outbound],
        }


@router.post("/connections/{connection_id}/disconnect", dependencies=[Depends(require_admin)])
async def disconnect(connection_id: int, user_id: str):
    from sqlalchemy import select

    from app.core.http import request_json
    from app.core.locks import sender_lock
    from app.core.security import decrypt_token
    from app.db.models import CalendarConnection, Event, User
    from app.db.session import SessionLocal
    from app.events.service import cancel_reminders

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(404)
        async with sender_lock(user.phone):
            connection = await session.scalar(
                select(CalendarConnection).where(
                    CalendarConnection.id == connection_id,
                    CalendarConnection.user_id == user.id,
                    CalendarConnection.active.is_(True),
                )
            )
            if connection is None:
                raise HTTPException(404)
            revoked = False
            if connection.provider == "google":
                try:
                    await request_json(
                        "POST",
                        "https://oauth2.googleapis.com/revoke",
                        data={
                            "token": decrypt_token(
                                connection.encrypted_refresh_token
                                or connection.encrypted_access_token
                            )
                        },
                    )
                    revoked = True
                except Exception:
                    structlog.get_logger().warning(
                        "remote_revocation_required", connection_id=connection.id
                    )
            connection.active = False
            # Deliberately erase the revoked credential.
            connection.encrypted_access_token = ""  # nosec B105
            connection.encrypted_refresh_token = None
            for event in (
                await session.scalars(
                    select(Event).where(
                        Event.connection_id == connection.id, Event.user_id == user.id
                    )
                )
            ).all():
                await cancel_reminders(session, event)
            await session.commit()
            return {
                "disconnected": True,
                "remote_revoked": revoked,
                "remote_revocation_required": not revoked,
            }
