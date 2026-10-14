"""Always-on voice activity detection.

Turns the raw microphone stream into clean "utterances": it waits for you to
start speaking, records until you stop, and yields the int16 samples of that
turn. This is the always-listening trigger the app is built around.

Uses webrtcvad when available, with an energy-based fallback so the app still
works if webrtcvad is missing.
"""

from __future__ import annotations

import collections
from typing import Iterator

import numpy as np

from .audio import MicStream

try:
    import webrtcvad

    _HAVE_WEBRTCVAD = True
except Exception:
    _HAVE_WEBRTCVAD = False


class VoiceListener:
    def __init__(self, mic: MicStream, cfg: dict) -> None:
        self.mic = mic
        self.frame_ms = int(cfg.get("frame_ms", 20))
        self.silence_ms = int(cfg.get("silence_ms", 700))
        self.pad_ms = int(cfg.get("speech_pad_ms", 300))
        self.min_speech_ms = int(cfg.get("min_speech_ms", 250))
        self.energy_threshold = float(cfg.get("energy_threshold", 0.008))

        self._vad = None
        if _HAVE_WEBRTCVAD:
            aggressiveness = int(cfg.get("aggressiveness", 2))
            self._vad = webrtcvad.Vad(max(0, min(3, aggressiveness)))

        # Pre-roll: keep recent frames so the utterance starts before the
        # exact frame where speech was detected.
        self._pad_frames = max(1, self.pad_ms // self.frame_ms)
        # Frames of trailing silence that end a turn.
        self._silence_frames = max(1, self.silence_ms // self.frame_ms)

    # ------------------------------------------------------------------
    def _is_speech(self, frame: np.ndarray) -> bool:
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame.tobytes(), self.mic.sample_rate)
            except Exception:
                pass
        # Energy fallback
        rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
        return rms > self.energy_threshold

    # ------------------------------------------------------------------
    def _finish(self, buffer: list[np.ndarray], trailing_silence: int):
        """Trim trailing silence and drop blips. Returns int16 array or None."""
        audio = np.concatenate(buffer) if buffer else np.zeros(0, np.int16)
        duration_ms = len(audio) * 1000 // self.mic.sample_rate
        if duration_ms < self.min_speech_ms:
            return None
        keep = max(0, len(audio) - trailing_silence * self.mic.frame_samples)
        return audio[: keep or len(audio)]

    def _collect(self, seed: list[np.ndarray]):
        """Keep reading until the speaker goes quiet, then return one utterance."""
        buffer = list(seed)
        trailing = 0
        while True:
            frame = self.mic.read_frame()
            buffer.append(frame)
            if self._is_speech(frame):
                trailing = 0
            else:
                trailing += 1
                if trailing >= self._silence_frames:
                    return self._finish(buffer, trailing)

    def _loud(self, frame: np.ndarray, threshold: float) -> bool:
        """Stricter check used for barge-in: loud *and* webrtcvad-positive."""
        rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
        if rms < threshold:
            return False
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame.tobytes(), self.mic.sample_rate)
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------
    def listen_once(self):
        """Block until one full utterance is captured, then return its samples."""
        preroll: collections.deque = collections.deque(maxlen=self._pad_frames)
        while True:
            frame = self.mic.read_frame()
            if self._is_speech(frame):
                audio = self._collect(list(preroll) + [frame])
                if audio is not None:
                    return audio
                preroll.clear()  # blip; start fresh
            else:
                preroll.append(frame)

    def monitor(self, active, cfg: dict):
        """Watch for the user talking *over* an ongoing reply (barge-in).

        `active` is a callable that returns True while VIONEX is still speaking.
        Returns the interrupting utterance (int16 array) as soon as sustained
        speech is heard, or None if the reply finishes first.
        """
        threshold = float(cfg.get("energy_threshold", 0.02))
        need = max(1, int(cfg.get("min_speech_ms", 300)) // self.frame_ms)
        preroll: collections.deque = collections.deque(maxlen=self._pad_frames)
        streak = 0
        seed: list[np.ndarray] = []

        while active():
            frame = self.mic.read_frame()
            speech = self._loud(frame, threshold)
            if streak == 0:
                if speech:
                    streak = 1
                    seed = list(preroll) + [frame]
                    preroll.clear()
                else:
                    preroll.append(frame)
                continue

            seed.append(frame)
            if speech:
                streak += 1
            elif len(seed) < need:
                streak = 0  # false start, keep watching
                seed = []

            if streak >= need:
                audio = self._collect(seed)
                if audio is not None:
                    return audio
                streak = 0
                seed = []
        return None

    # ------------------------------------------------------------------
    def utterances(self) -> Iterator[np.ndarray]:
        """Yield one int16 numpy array per detected spoken turn."""
        while True:
            audio = self.listen_once()
            if audio is not None:
                yield audio
