"""Voice endpoints: status, transcription and optional backend TTS."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import services_from
from app.core.errors import ConfigurationError
from app.core.logging import audit

router = APIRouter(tags=["voice"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class SpeakRequest(BaseModel):
    text: str


@router.get("/api/voice/status")
async def voice_status(request: Request) -> dict[str, Any]:
    services = services_from(request)
    return services.voice.status()


@router.post("/api/voice/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...), language: str | None = None) -> dict[str, Any]:
    services = services_from(request)
    voice_dir = services.app_settings.data_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    target = voice_dir / f"input_{uuid.uuid4().hex[:10]}{suffix}"
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise HTTPException(status_code=413, detail="Audio exceeds 25 MB")
                handle.write(chunk)
        try:
            result = await services.voice.transcribe(target, language=language)
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail=exc.to_dict()) from exc
        audit("voice.transcribed", chars=len(result.get("text", "")), language=result.get("language"))
        return result
    finally:
        await file.close()
        target.unlink(missing_ok=True)


@router.post("/api/voice/speak")
async def speak(request: Request, body: SpeakRequest) -> dict[str, Any]:
    services = services_from(request)
    try:
        return await services.voice.synthesize(body.text)
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc


@router.get("/api/voice/audio/{filename}")
async def audio(filename: str, request: Request) -> FileResponse:
    services = services_from(request)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = services.app_settings.data_dir / "voice" / filename
    if not target.exists() or target.suffix.lower() not in (".wav", ".mp3"):
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(str(target), media_type="audio/wav", filename=filename)
