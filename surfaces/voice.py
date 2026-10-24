"""Doris's voice: Cartesia text-to-speech, rendered once per line and cached as telephone audio
(8 kHz mu-law WAV, which Twilio's <Play> takes as is)."""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("doris.voice")
CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2025-04-16"


class Voice:
    def __init__(self, api_key: str, voice_id: str, model: str, cache_dir: Path, timeout: float = 20.0):
        self.api_key, self.voice_id, self.model, self.timeout = api_key, voice_id, model, timeout
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def name_for(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}|{self.voice_id}|{text}".encode()).hexdigest()[:32] + ".wav"

    def path(self, name: str) -> Optional[Path]:
        if len(name) != 36 or not name.endswith(".wav") or not all(c in "0123456789abcdef" for c in name[:32]):
            return None
        p = self.cache_dir / name
        return p if p.is_file() else None

    def render(self, text: str) -> Optional[str]:
        """The cached file name for this line, synthesising it if needed; None if Cartesia is unavailable."""
        name = self.name_for(text)
        if (self.cache_dir / name).is_file():
            return name
        if not self.api_key:
            return None
        body = {"model_id": self.model, "transcript": text, "language": "en", "voice": {"mode": "id", "id": self.voice_id},
                "output_format": {"container": "wav", "encoding": "pcm_mulaw", "sample_rate": 8000}}
        req = urllib.request.Request(CARTESIA_URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"X-API-Key": self.api_key, "Cartesia-Version": CARTESIA_VERSION, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                audio = resp.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("Cartesia failed, Twilio's own voice will read the line: %s", exc)
            return None
        if not audio.startswith(b"RIFF"):
            log.warning("Cartesia returned something that isn't WAV; ignoring it")
            return None
        tmp = self.cache_dir / (name + ".part")
        tmp.write_bytes(audio)
        tmp.replace(self.cache_dir / name)
        return name
