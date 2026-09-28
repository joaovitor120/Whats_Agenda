from urllib.parse import parse_qs, urlsplit

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import decrypt_token, hash_state
from app.db.models import CalendarConnection, OAuthState, OutboundMessage, PendingConnection, User
from app.events.conversation import confirm_connection
from app.main import app


async def start(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(
        settings, "google_redirect_uri", "http://localhost:8000/oauth/google/callback"
    )
    response = await client.get(
        "/oauth/google/start?phone=5511999999999", headers={"X-Admin-Api-Key": "test-admin-secret"}
    )
    assert response.status_code == 200
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)


async def test_pkce_one_use_and_whatsapp_account_confirmation(db, monkeypatch):
    calls = []

    async def request(method, url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/token"):
            return {
                "access_token": "access-value",
                "refresh_token": "refresh-value",
                "expires_in": 3600,
            }
        return {"email": "joao@example.com"}

    monkeypatch.setattr("app.api.oauth.request_json", request)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://test"
    ) as client:
        query = await start(client, monkeypatch)
        assert query["code_challenge_method"] == ["S256"]
        async with db() as session:
            state = await session.get(OAuthState, hash_state(query["state"][0]))
            assert len(decrypt_token(state.encrypted_verifier)) >= 43
        callback = f"/oauth/google/callback?code=test-code&state={query['state'][0]}"
        assert (await client.get(callback)).status_code == 200
        assert (await client.get(callback)).status_code == 400
        assert "code_verifier" in calls[0][1]["data"]
    async with db() as session:
        assert not (await session.scalars(select(CalendarConnection))).all()
        grant = await session.scalar(select(PendingConnection))
        assert "access-value" not in grant.encrypted_tokens
        out = await session.scalar(select(OutboundMessage))
        assert "j***@example.com" in out.text
        code = out.text.split("CONECTAR ")[1].split()[0]
        other = await session.scalar(select(User).where(User.phone == "5511888888888"))
        owner = await session.scalar(select(User).where(User.phone == "5511999999999"))
        assert "inválido" in await confirm_connection(session, other, code)
        assert "conectado" in await confirm_connection(session, owner, code)
        await session.commit()
        assert "inválido" in await confirm_connection(session, owner, code)
        connection = await session.scalar(select(CalendarConnection))
        assert connection.user_id == owner.id and connection.active
        assert decrypt_token(connection.encrypted_refresh_token) == "refresh-value"


async def test_failed_exchange_still_consumes_state(db, monkeypatch):
    async def fail(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr("app.api.oauth.request_json", fail)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://test"
    ) as client:
        query = await start(client, monkeypatch)
        url = f"/oauth/google/callback?code=test-code&state={query['state'][0]}"
        assert (await client.get(url)).status_code == 502
        assert (await client.get(url)).status_code == 400


async def test_admin_rejects_network_and_header_spoofing(db):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.1", 1)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/admin/evolution/setup",
            headers={"X-Admin-Api-Key": "test-admin-secret", "X-Forwarded-For": "127.0.0.1"},
        )
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
