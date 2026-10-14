"""Speech-to-text with faster-whisper (local, Hindi + English + Hinglish)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Transcript:
    text: str
    language: str
    probability: float


class Transcriber:
    def __init__(self, cfg: dict) -> None:
        # Imported here so the rest of the app can run without the model.
        from faster_whisper import WhisperModel

        self.model_size = cfg.get("model", "small")
        self.device = cfg.get("device", "cpu")
        self.compute_type = cfg.get("compute_type", "int8")
        self.language = cfg.get("language")  # None = auto-detect
        self.beam_size = int(cfg.get("beam_size", 1))
        self.vad_filter = bool(cfg.get("vad_filter", True))
        self.sample_rate = int(cfg.get("sample_rate", 16000))
        # We only ever expect Hindi or English. If auto-detect lands on
        # anything else (Greek, Urdu, Bengali, ...), re-read the audio as the
        # fallback language so we never emit a foreign script.
        self.allowed = tuple(cfg.get("languages") or ("hi", "en"))
        self.fallback_language = cfg.get("fallback_language", "hi")
        self.min_lang_prob = float(cfg.get("min_language_probability", 0.0))

        print(
            f"[stt] loading faster-whisper '{self.model_size}' "
            f"({self.device}/{self.compute_type})..."
        )
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        self.warmup()
        print("[stt] model ready")

    def warmup(self) -> None:
        """Run a tiny transcription to pay one-time init cost up front."""
        try:
            silence = np.zeros(self.sample_rate, dtype=np.int16)  # 0.5 s @ 16k
            self.transcribe(silence)
        except Exception:
            pass

    def transcribe(self, audio_int16: np.ndarray) -> Transcript:
        # faster-whisper expects float32 in [-1, 1] at 16 kHz.
        audio = audio_int16.astype(np.float32) / 32768.0
        # Mild auto-gain: boost quiet/far-from-mic speech so whisper hears it.
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if 0.02 < peak < 0.5:
            audio = np.clip(audio * min(0.9 / peak, 8.0), -1.0, 1.0)

        segments, info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            condition_on_previous_text=False,
        )
        detected = getattr(info, "language", None)
        prob = float(getattr(info, "language_probability", 0.0))

        # Auto-detect wandered outside Hindi/English -> force the fallback
        # language and transcribe again (prevents stray Greek/Urdu/etc. text).
        if self.language is None and (
            detected not in self.allowed or prob < self.min_lang_prob
        ):
            segments, info = self.model.transcribe(
                audio,
                language=self.fallback_language,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
                condition_on_previous_text=False,
            )
            detected = getattr(info, "language", self.fallback_language)
            prob = float(getattr(info, "language_probability", 0.0))

        text = " ".join(seg.text.strip() for seg in segments).strip()
        return Transcript(
            text=text,
            language=detected or "unknown",
            probability=prob,
        )
