"""Offline transcription service; no audio or transcripts are logged or saved."""

import asyncio
import io
import os
import re
import secrets
from collections import Counter
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from faster_whisper import WhisperModel
from mutagen import File

SECRET = os.environ.get("LOCAL_TRANSCRIPTION_API_KEY", "")
MAX_BYTES = min(int(os.environ.get("MAX_AUDIO_BYTES", "20000000")), 20_000_000)
MAX_SECONDS = min(int(os.environ.get("MAX_AUDIO_SECONDS", "600")), 600)
MAX_CHARS = min(int(os.environ.get("MAX_TEXT_CHARS", "4000")), 16000)
slots = asyncio.Semaphore(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(SECRET) < 32:
        raise RuntimeError("Configure a local transcription secret of at least 32 characters")
    app.state.model = WhisperModel(
        "/opt/whisper-small",
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
        local_files_only=True,
    )
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    return {"ready": True, "model": "small", "compute_type": "int8"}


def transcribe_audio(data: bytes) -> str:
    metadata = File(io.BytesIO(data))
    if metadata is None or not 0 < metadata.info.length <= MAX_SECONDS:
        raise ValueError("Invalid duration")
    segments, _ = app.state.model.transcribe(
        io.BytesIO(data),
        language="pt",
        beam_size=5,
        best_of=1,
        temperature=0,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    parts = []
    size = 0
    for segment in segments:
        text = segment.text.strip()
        if not text or segment.no_speech_prob > 0.6:
            continue
        if segment.avg_logprob < -1.0 or segment.compression_ratio > 2.4:
            raise ValueError("Low-confidence transcript")
        size += len(text) + 1
        if size > MAX_CHARS:
            raise ValueError("Transcript too long")
        parts.append(text)
    result = " ".join(parts)
    if not result:
        raise ValueError("No intelligible speech")
    words = re.findall(r"\w+", result.casefold())
    repeated = Counter(tuple(words[i : i + 3]) for i in range(max(0, len(words) - 2)))
    if any(count >= 4 for count in repeated.values()):
        raise ValueError("Repetitive transcript")
    return result


@app.post("/transcribe")
async def transcribe(request: Request):
    if not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {SECRET}"):
        raise HTTPException(401, "Unauthorized")
    if request.headers.get("content-type") not in {
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/x-wav",
    }:
        raise HTTPException(415, "Unsupported audio")
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.2)
    except TimeoutError:
        raise HTTPException(503, "Transcription busy") from None
    try:
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise HTTPException(413, "Audio too large")
        if not raw:
            raise HTTPException(422, "Empty audio")
        # A single inference is allowed at a time, including after client disconnect.
        result = await asyncio.to_thread(transcribe_audio, bytes(raw))
        return {"text": result}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(422, "Audio could not be transcribed") from None
    except Exception:
        raise HTTPException(503, "Transcription unavailable") from None
    finally:
        slots.release()
