"""Voice services: optional Faster-Whisper STT and SAPI TTS, with honest status."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import ConfigurationError, ToolExecutionError
from app.core.logging import get_logger
from app.services.settings_service import SettingsService

log = get_logger("voice")


class VoiceService:
    def __init__(self, app_settings: Settings, runtime_settings: SettingsService) -> None:
        self.app = app_settings
        self.runtime = runtime_settings
        self._whisper: Any = None
        self._whisper_key: tuple[str, str] | None = None

    def status(self) -> dict[str, Any]:
        config = self.runtime.voice
        return {
            "enabled": bool(config.get("enabled", True)),
            "push_to_talk": bool(config.get("push_to_talk", True)),
            "speak_responses": bool(config.get("speak_responses", True)),
            "wake_word": config.get("wake_word", ""),
            "whisper_installed": importlib.util.find_spec("faster_whisper") is not None,
            "whisper_model": config.get("whisper_model", "base"),
            "whisper_loaded": self._whisper is not None,
            "tts_installed": importlib.util.find_spec("pyttsx3") is not None,
            "browser_tts_available": True,
            "note": (
                "Speech-to-text requires the optional faster-whisper package. "
                "The UI can use Windows/browser speech synthesis for replies."
            ),
        }

    def _load_whisper(self) -> Any:
        config = self.runtime.voice
        model_name = str(config.get("whisper_model") or "base")
        device = str(config.get("whisper_device") or "cpu")
        key = (model_name, device)
        if self._whisper is not None and self._whisper_key == key:
            return self._whisper
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise ConfigurationError(
                "Speech-to-text is not installed.",
                hint="Install optional dependencies: pip install -r backend/requirements-optional.txt",
            ) from exc
        model_dir = self.app.data_dir / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        self._whisper = WhisperModel(model_name, device=device, compute_type="int8", download_root=str(model_dir))
        self._whisper_key = key
        return self._whisper

    async def transcribe(self, audio_path: Path, language: str | None = None) -> dict[str, Any]:
        model = await asyncio.to_thread(self._load_whisper)
        started = time.perf_counter()

        def _run() -> tuple[list[dict[str, Any]], str, float]:
            segments, info = model.transcribe(str(audio_path), language=language, vad_filter=True)
            collected = []
            for segment in segments:
                collected.append({"start": round(segment.start, 2), "end": round(segment.end, 2), "text": segment.text})
            return collected, info.language, info.duration

        try:
            segments, detected, duration = await asyncio.to_thread(_run)
        except Exception as exc:
            raise ToolExecutionError(f"Transcription failed: {exc}") from exc
        text = " ".join(segment["text"].strip() for segment in segments).strip()
        return {
            "text": text,
            "segments": segments,
            "language": detected,
            "duration": round(duration, 2),
            "elapsed": round(time.perf_counter() - started, 2),
        }

    async def synthesize(self, text: str) -> dict[str, Any]:
        if not importlib.util.find_spec("pyttsx3"):
            raise ConfigurationError(
                "Backend text-to-speech is not installed.",
                hint="The UI can speak responses with the Web Speech API. To enable backend TTS install pyttsx3.",
            )
        output_dir = self.app.data_dir / "voice"
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"speech_{int(time.time())}.wav"

        def _speak() -> None:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 180)
            engine.save_to_file(text[:4000], str(target))
            engine.runAndWait()

        try:
            await asyncio.to_thread(_speak)
        except Exception as exc:
            raise ToolExecutionError(f"Speech synthesis failed: {exc}") from exc
        if not target.exists():
            raise ToolExecutionError("Speech synthesis produced no audio file.")
        return {"path": str(target), "url": f"/api/voice/audio/{target.name}", "bytes": target.stat().st_size}
