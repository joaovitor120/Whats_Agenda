import asyncio
import io

from app.core.config import settings
from app.core.http import request_json


class TranscriptionUnavailable(RuntimeError):
    pass


async def transcribe_audio(audio: bytes, mime: str) -> str:
    try:
        async with asyncio.timeout(60):
            if settings.audio_transcription_provider == "local":
                result = await request_json(
                    "POST",
                    f"{settings.local_transcription_url.rstrip('/')}/transcribe",
                    headers={
                        "Authorization": f"Bearer {settings.local_transcription_api_key}",
                        "Content-Type": mime,
                    },
                    content=audio,
                    max_bytes=100000,
                    timeout=55,
                )
                text = result.get("text")
            else:
                from openai import AsyncOpenAI

                extension = {
                    "audio/ogg": "ogg",
                    "audio/mpeg": "mp3",
                    "audio/mp4": "m4a",
                    "audio/wav": "wav",
                    "audio/x-wav": "wav",
                }[mime]
                async with AsyncOpenAI(
                    api_key=settings.openai_api_key, timeout=30, max_retries=0
                ) as client:
                    transcript = await client.audio.transcriptions.create(
                        model=settings.openai_transcription_model,
                        file=(f"audio.{extension}", io.BytesIO(audio)),
                    )
                text = transcript.text
        if not isinstance(text, str) or not 0 < len(text.strip()) <= settings.max_text_chars:
            raise ValueError("Invalid transcript")
        return text.strip()
    except Exception as exc:
        raise TranscriptionUnavailable("Audio transcription unavailable") from exc
