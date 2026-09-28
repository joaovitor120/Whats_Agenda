import asyncio
import base64
import io
import re
from contextlib import asynccontextmanager
from typing import Literal

import structlog

from app.core.config import settings
from app.core.http import request_json

log = structlog.get_logger()


def validate_audio(data: bytes, mime: str) -> float:
    from mutagen import File

    allowed_magic = {
        "audio/ogg": data.startswith(b"OggS"),
        "audio/mpeg": data.startswith(b"ID3")
        or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"},
        "audio/mp4": len(data) > 12 and data[4:8] == b"ftyp",
        "audio/wav": data.startswith(b"RIFF") and data[8:12] == b"WAVE",
        "audio/x-wav": data.startswith(b"RIFF") and data[8:12] == b"WAVE",
    }
    if not data or len(data) > settings.max_audio_bytes or not allowed_magic.get(mime, False):
        raise ValueError("Invalid audio content")
    audio = File(io.BytesIO(data))
    if audio is None or not 0 < audio.info.length <= settings.max_audio_seconds:
        raise ValueError("Invalid audio duration")
    return float(audio.info.length)


class EvolutionClient:
    def __init__(self, base_url=None, api_key=None, instance=None):
        self.base = (base_url or settings.evolution_api_url).rstrip("/")
        self.api_key = (
            api_key
            if api_key is not None
            else settings.evolution_api_key or settings.evolution_instance_token
        )
        self.instance = instance or settings.evolution_instance_name
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.instance):
            raise ValueError("Invalid instance")
        self.fake = settings.fake_evolution

    def _headers(self):
        return {"apikey": self.api_key}

    async def send_presence(
        self, number: str, presence: Literal["composing", "paused"], delay: int = 0
    ) -> dict:
        if not settings.whatsapp_allowed(number):
            raise PermissionError("WhatsApp destination is not authorized")
        if not re.fullmatch(r"[1-9][0-9]{7,14}", number):
            raise ValueError("Invalid presence destination")
        if presence not in {"composing", "paused"} or not 0 <= delay <= 3000:
            raise ValueError("Invalid presence options")
        if self.fake:
            return {"fake": True}
        return await request_json(
            "POST",
            f"{self.base}/chat/sendPresence/{self.instance}",
            headers=self._headers(),
            json={"number": number, "presence": presence, "delay": delay},
            timeout=5,
        )

    @asynccontextmanager
    async def typing(self, number: str):
        """Renew short presence pulses without letting failures prevent a reply."""
        if self.fake or not settings.whatsapp_allowed(number):
            yield
            return
        stopped = asyncio.Event()

        async def keep_typing():
            while not stopped.is_set():
                try:
                    # Evolution waits for this delay, then sends paused itself.
                    async with asyncio.timeout(5):
                        await self.send_presence(number, "composing", delay=3000)
                except Exception as exc:
                    log.warning("typing_presence_failed", error_type=type(exc).__name__)
                    return
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=0.1)
                except TimeoutError:
                    pass

        task = asyncio.create_task(keep_typing())
        try:
            yield
        finally:
            stopped.set()
            # Drain the pulse so it cannot send a late paused into the next reply.
            await task
            try:
                async with asyncio.timeout(2):
                    await self.send_presence(number, "paused")
            except Exception as exc:
                log.warning("typing_cleanup_failed", error_type=type(exc).__name__)

    async def send_text(self, number: str, text: str) -> dict:
        if not settings.whatsapp_allowed(number):
            raise PermissionError("WhatsApp destination is not authorized")
        if not re.fullmatch(r"[1-9][0-9]{7,14}", number) or not 0 < len(text) <= 10000:
            raise ValueError("Invalid outbound message")
        if self.fake:
            return {"fake": True}
        return await request_json(
            "POST",
            f"{self.base}/message/sendText/{self.instance}",
            headers=self._headers(),
            json={"number": number, "text": text, "delay": 3000},
        )

    async def download_media(self, media: dict) -> bytes:
        if "url" in media or set(media) - {"key", "seconds", "mimetype"}:
            raise ValueError("Only stored Evolution media keys are accepted")
        key = media.get("key")
        if not isinstance(key, dict) or not key.get("id") or not key.get("remoteJid"):
            raise ValueError("Missing stored message key")
        if self.fake:
            raise ValueError("Audio retrieval requires a real Evolution instance")
        payload = await request_json(
            "POST",
            f"{self.base}/chat/getBase64FromMediaMessage/{self.instance}",
            max_bytes=(settings.max_audio_bytes * 4 // 3) + 4096,
            headers=self._headers(),
            json={"message": {"key": key}},
        )
        value = payload.get("base64")
        if not isinstance(value, str) or len(value) > (settings.max_audio_bytes * 4 // 3) + 4:
            raise ValueError("Invalid base64 media")
        data = base64.b64decode(value, validate=True)
        validate_audio(data, str(media.get("mimetype")))
        return data

    async def configure_webhook(self, url: str) -> dict:
        return await self._post_instance(
            f"/webhook/set/{self.instance}",
            {
                "webhook": {
                    "enabled": True,
                    "url": url,
                    "byEvents": False,
                    "base64": False,
                    "events": ["MESSAGES_UPSERT"],
                    "headers": {"Authorization": f"Bearer {settings.evolution_webhook_secret}"},
                }
            },
        )

    async def get_connection_status(self) -> dict:
        return await self._get_instance("/instance/connectionState")

    async def create_instance_if_needed(self) -> dict:
        from app.core.http import UpstreamError

        try:
            return await self.get_connection_status()
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
        return await self._post_instance(
            "/instance/create",
            {"instanceName": self.instance, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
        )

    async def get_qr_code(self) -> dict:
        return await self._get_instance("/instance/connect")

    async def _get_instance(self, path: str):
        if self.fake:
            return {"state": "fake-mode", "instance": self.instance}
        return await request_json(
            "GET", f"{self.base}{path}/{self.instance}", headers=self._headers()
        )

    async def _post_instance(self, path: str, body: dict):
        if self.fake:
            return {"fake": True, "instance": self.instance}
        return await request_json("POST", f"{self.base}{path}", headers=self._headers(), json=body)
