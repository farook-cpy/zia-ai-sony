"""
Project Zia - Voice & Audio Processing Pipeline
Provides lightweight TTS using edge-tts, native Termux audio playback,
and cloud-hybrid STT transcription via Gemini Multimodal API.
"""

import os
import shutil
import asyncio
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("project-zia.voice")

# Output directory for audio clips
AUDIO_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audio")
os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

DEFAULT_VOICE = os.getenv("ZIA_VOICE", "en-US-AvaNeural")


class VoicePipeline:
    """Manages audio generation, playback, and transcription."""

    def __init__(self, ai_brain=None):
        self.ai_brain = ai_brain

    # --------------------------------------------------------------------------
    # Text-to-Speech (TTS)
    # --------------------------------------------------------------------------

    @staticmethod
    def list_voices(lang_filter: str = "en") -> List[Dict[str, str]]:
        """List available neural voices from edge-tts."""
        import edge_tts
        try:
            voices = asyncio.run(edge_tts.list_voices())
            filtered = []
            for v in voices:
                if not lang_filter or lang_filter.lower() in v.get("Locale", "").lower():
                    filtered.append({
                        "name": v.get("ShortName"),
                        "locale": v.get("Locale"),
                        "gender": v.get("Gender"),
                        "friendly_name": v.get("FriendlyName"),
                    })
            return filtered
        except Exception as exc:
            logger.error("Failed to list voices: %s", exc)
            return []

    @staticmethod
    def synthesize_to_file(text: str, voice: Optional[str] = None, output_filename: Optional[str] = None) -> str:
        """
        Synthesize text into an MP3 file using edge-tts.
        Returns the absolute file path of the generated audio.
        """
        import edge_tts

        voice = voice or DEFAULT_VOICE
        if not output_filename:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            output_filename = f"tts_{timestamp}.mp3"

        out_path = os.path.join(AUDIO_CACHE_DIR, output_filename)

        async def _run():
            comm = edge_tts.Communicate(text, voice)
            await comm.save(out_path)

        asyncio.run(_run())
        logger.info("Synthesized speech (%d chars) -> %s", len(text), out_path)
        return out_path

    @staticmethod
    def speak_on_device(text: str, voice: Optional[str] = None) -> Dict[str, Any]:
        """
        Speak text out loud on the Sony Xperia 1 III hardware speaker.
        Prefers termux-tts-speak or termux-media-player if running inside Termux.
        """
        # 1. Try native termux-tts-speak
        termux_tts = shutil.which("termux-tts-speak")
        if termux_tts:
            try:
                subprocess.run([termux_tts, text], check=True, timeout=10)
                return {"status": "success", "engine": "termux-tts-speak", "text": text}
            except Exception as exc:
                logger.warning("termux-tts-speak failed: %s", exc)

        # 2. Synthesize via edge-tts and play through termux-media-player
        try:
            audio_path = VoicePipeline.synthesize_to_file(text, voice)
            termux_player = shutil.which("termux-media-player")
            if termux_player:
                subprocess.run([termux_player, "play", audio_path], check=True, timeout=10)
                return {"status": "success", "engine": "edge-tts+termux-media-player", "audio_path": audio_path}

            return {
                "status": "synthesized_only",
                "message": "Audio synthesized but no on-device player found in PATH (termux-media-player).",
                "audio_path": audio_path,
            }
        except Exception as exc:
            logger.error("Failed to speak on device: %s", exc)
            return {"status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # Speech-to-Text (STT) & Audio Understanding
    # --------------------------------------------------------------------------

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/mp3",
                         execute_with_brain: bool = False) -> Dict[str, Any]:
        """
        Transcribe spoken audio using Gemini Multimodal understanding.
        Bypasses heavy local C++ compilation (vosk/whisper) on aarch64.
        """
        if not self.ai_brain or not self.ai_brain.client:
            return {
                "status": "error",
                "message": "Gemini API client not initialized. Configure GEMINI_API_KEY in .env.",
            }

        try:
            from google.genai import types

            part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
            prompt = (
                "Please transcribe the spoken words in this audio recording accurately. "
                "Return only the transcribed text without extra preamble or commentary."
            )

            response = self.ai_brain.client.models.generate_content(
                model=self.ai_brain.model_name,
                contents=[part, prompt],
            )
            transcription = (response.text or "").strip()
            logger.info("Transcribed audio: '%s'", transcription)

            result: Dict[str, Any] = {
                "status": "success",
                "transcription": transcription,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

            if execute_with_brain and transcription:
                logger.info("Executing transcribed voice command with Zia brain...")
                execution = self.ai_brain.process_query(transcription)
                result["execution"] = execution

            return result

        except Exception as exc:
            logger.error("STT transcription error: %s", exc)
            return {"status": "error", "error": str(exc)}

