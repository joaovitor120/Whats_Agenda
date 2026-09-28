import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.core.config import settings
from app.core.http import request_json
from app.core.security import decrypt_token, encrypt_token, hash_state, new_oauth_state
from app.db.models import OAuthState, OutboundMessage, PendingConnection, User
from app.db.session import get_session

router = APIRouter(prefix="/oauth", tags=["calendar oauth"])


class TokenResponse(BaseModel):
    access_token: str = Field(min_length=1, max_length=16384)
    refresh_token: str | None = Field(default=None, max_length=16384)
    expires_in: int = Field(default=3600, ge=1, le=86400)
    token_type: str = "Bearer"


def oauth_config(provider):
    if provider == "google":
        return (
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            "openid email https://www.googleapis.com/auth/calendar.events",
        )
    if provider == "microsoft":
        base = f"https://login.microsoftonline.com/{settings.microsoft_tenant}/oauth2/v2.0"
        return (
            f"{base}/authorize",
            f"{base}/token",
            "offline_access User.Read Calendars.ReadWrite",
        )
    raise HTTPException(404)


@router.get("/{provider}/start", dependencies=[Depends(require_admin)])
async def oauth_start(
    provider: str,
    phone: str = Query(pattern=r"^[1-9][0-9]{7,14}$"),
    session: AsyncSession = Depends(get_session),
):
    if not settings.whatsapp_allowed(phone):
        raise HTTPException(403, "WhatsApp number is not authorized")
    auth_url, _, scope = oauth_config(provider)
    client_id, redirect = (
        getattr(settings, f"{provider}_client_id"),
        getattr(settings, f"{provider}_redirect_uri"),
    )
    if not client_id or not redirect:
        raise HTTPException(503, "OAuth is not configured")
    user = await session.scalar(select(User).where(User.phone == phone))
    if not user:
        raise HTTPException(404, "The user must first send a WhatsApp message")
    state, verifier = new_oauth_state(), secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    session.add(
        OAuthState(
            state_hash=hash_state(state),
            user_id=user.id,
            provider=provider,
            encrypted_verifier=encrypt_token(verifier),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    await session.commit()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
    }
    if provider == "google":
        params["access_type"] = "offline"
    return {"authorization_url": auth_url + "?" + urlencode(params)}


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str = Query(min_length=1, max_length=4096),
    state: str = Query(min_length=20, max_length=256),
    session: AsyncSession = Depends(get_session),
):
    _, token_url, scope = oauth_config(provider)
    now = datetime.now(UTC)
    # Atomic consume is committed before exchanging a code. Failed exchanges cannot reuse a state.
    row = (
        await session.execute(
            update(OAuthState)
            .where(
                OAuthState.state_hash == hash_state(state),
                OAuthState.provider == provider,
                OAuthState.consumed.is_(False),
                OAuthState.expires_at > now,
                OAuthState.encrypted_verifier.is_not(None),
            )
            .values(consumed=True)
            .returning(OAuthState)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(400, "Invalid or expired OAuth state")
    if row.encrypted_verifier is None:
        raise HTTPException(400, "Missing PKCE verifier")
    verifier, user_id = decrypt_token(row.encrypted_verifier), row.user_id
    row.encrypted_verifier = None
    await session.commit()
    try:
        payload = await request_json(
            "POST",
            token_url,
            data={
                "code": code,
                "client_id": getattr(settings, f"{provider}_client_id"),
                "client_secret": getattr(settings, f"{provider}_client_secret"),
                "redirect_uri": getattr(settings, f"{provider}_redirect_uri"),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
                **({"scope": scope} if provider == "microsoft" else {}),
            },
        )
        tokens = TokenResponse.model_validate(payload)
        if tokens.token_type.lower() != "bearer":
            raise ValueError("Unsupported token type")
        identity_url = (
            "https://openidconnect.googleapis.com/v1/userinfo"
            if provider == "google"
            else "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
        )
        identity = await request_json(
            "GET", identity_url, headers={"Authorization": f"Bearer {tokens.access_token}"}
        )
        email = identity.get("email") or identity.get("mail") or identity.get("userPrincipalName")
        if not isinstance(email, str) or "@" not in email or len(email) > 254:
            raise ValueError("Invalid account identity")
    except Exception as exc:
        raise HTTPException(502, "OAuth exchange failed; start again") from exc
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(400, "User is no longer available")
    if not settings.whatsapp_allowed(user.phone):
        raise HTTPException(403, "WhatsApp number is not authorized")
    confirm = secrets.token_hex(8).upper()
    await session.execute(delete(PendingConnection).where(PendingConnection.user_id == user.id))
    session.add(
        PendingConnection(
            code_hash=hash_state(confirm),
            user_id=user.id,
            provider=provider,
            encrypted_tokens=encrypt_token(json.dumps(tokens.model_dump())),
            expires_at=now + timedelta(minutes=10),
            token_expires_at=now + timedelta(seconds=tokens.expires_in),
        )
    )
    masked = email[0] + "***@" + email.split("@", 1)[1]
    session.add(
        OutboundMessage(
            dedupe_key=f"oauth:{row.state_hash}",
            phone=user.phone,
            text=f"Conectar {provider} ({masked})? Responda CONECTAR {confirm} em até 10 minutos. Se não solicitou, ignore.",
            expires_at=now + timedelta(minutes=10),
        )
    )
    await session.commit()
    return {
        "status": "pending_whatsapp_confirmation",
        "message": "Confirme a conta pelo seu WhatsApp em até 10 minutos.",
    }
