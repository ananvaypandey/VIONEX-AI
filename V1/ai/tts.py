"""Text-to-speech.

Primary engine: Piper (offline neural TTS) - synthesises a sentence in
~50-150 ms on CPU, so the voice starts with the reply instead of seconds
later. Voices are picked from the script of the text (Devanagari -> Hindi,
Arabic -> Urdu, else English).

Fallback engine: edge-tts (online, higher-latency) streamed into ffplay, used
automatically if Piper voices are missing or a Piper voice fails.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

_WARMUP = {"hi": "हाँ", "ur": "ہاں", "en": "ok"}


class Speaker:
    def __init__(self, cfg: dict) -> None:
        self.base_dir = cfg.get("base_dir") or os.getcwd()
        self.engine = str(cfg.get("engine", "piper") or "piper").lower()

        # edge-tts fallback voices (by language key).
        edge = cfg.get("voices", {})
        self.edge_voices = {
            "hi": edge.get("hi", "hi-IN-SwaraNeural"),
            "ur": edge.get("ur", "ur-IN-GulNeural"),
            "en": edge.get("en", "en-IN-NeerjaNeural"),
            "default": edge.get("default", "en-IN-NeerjaNeural"),
        }
        self.rate = cfg.get("rate", "+0%")
        self.volume = cfg.get("volume", "+0%")
        self.pitch = cfg.get("pitch", "+0Hz")

        self._ffplay = shutil.which("ffplay")
        self._sd = None
        self._piper: dict = {}

        # Barge-in support: stop() interrupts whatever is currently playing.
        self._interrupt = threading.Event()
        self._piper_stream = None
        self._edge_proc = None

        self._init_piper(cfg.get("piper", {}))

        # pygame (only needed for the non-streaming edge fallback path).
        self._pygame = None
        try:
            import pygame

            pygame.mixer.init()
            self._pygame = pygame
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _resolve(self, path: str | None) -> str | None:
        if not path:
            return None
        if os.path.isabs(path):
            return path
        return os.path.join(self.base_dir, path)

    def _init_piper(self, pcfg: dict) -> None:
        if self.engine != "piper":
            return
        try:
            from piper import PiperVoice

            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            print(f"[tts] piper unavailable ({exc}); using edge-tts")
            return

        self._sd = sd
        paths = pcfg.get("voices", {})
        for lang in ("hi", "ur", "en"):
            path = self._resolve(paths.get(lang))
            if not path or not os.path.exists(path):
                continue
            try:
                voice = PiperVoice.load(path)
                for _ in voice.synthesize(_WARMUP.get(lang, "ok")):
                    break
                self._piper[lang] = voice
            except Exception as exc:  # noqa: BLE001
                print(f"[tts] piper voice '{lang}' failed to load: {exc}")

        if self._piper:
            print(f"[tts] piper engine active (offline): {', '.join(self._piper)}")
        else:
            print("[tts] piper: no voice models found; using edge-tts")

    # ------------------------------------------------------------------
    def _pick_lang(self, text: str, lang: str | None) -> str:
        if _DEVANAGARI.search(text):
            return "hi"
        if _ARABIC.search(text):
            return "ur"
        if lang and lang.startswith("hi"):
            return "hi"
        if lang and lang.startswith("ur"):
            return "ur"
        if lang and lang.startswith("en"):
            return "en"
        return "en"

    def _play_piper(self, text: str, key: str) -> bool:
        voice = (
            self._piper.get(key)
            or self._piper.get("en")
            or next(iter(self._piper.values()))
        )
        try:
            stream = self._sd.RawOutputStream(
                samplerate=voice.config.sample_rate,
                channels=1,
                dtype="int16",
                latency="low",
            )
            self._piper_stream = stream
            stream.start()
            got = False
            for chunk in voice.synthesize(text):
                if self._interrupt.is_set():
                    break
                data = chunk.audio_int16_bytes
                if data:
                    got = True
                    stream.write(data)
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            return got
        except Exception as exc:  # noqa: BLE001
            print(f"[tts] piper playback failed: {exc}")
            return False
        finally:
            self._piper_stream = None

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Clear the interrupt flag before a new utterance starts."""
        self._interrupt.clear()

    def stop(self) -> None:
        """Barge-in: cut off the current playback as fast as possible."""
        self._interrupt.set()
        stream = self._piper_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        proc = self._edge_proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self._pygame is not None:
            try:
                self._pygame.mixer.music.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    async def _edge_stream(self, text: str, voice: str, sink) -> bool:
        import edge_tts

        communicate = edge_tts.Communicate(
            text, voice, rate=self.rate, volume=self.volume, pitch=self.pitch
        )
        got = False
        async for chunk in communicate.stream():
            data = chunk.get("data")
            if chunk.get("type") == "audio" and data:
                got = True
                sink(data)
        return got

    def _edge_stream_ffplay(self, text: str, voice: str) -> bool:
        proc = subprocess.Popen(
            [self._ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-hide_banner", "-i", "pipe:0"],
            stdin=subprocess.PIPE,
        )
        self._edge_proc = proc

        def sink(data: bytes) -> None:
            try:
                proc.stdin.write(data)
            except Exception:
                pass

        try:
            got = asyncio.run(self._edge_stream(text, voice, sink))
        finally:
            self._edge_proc = None
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
        return got

    def _edge_speak(self, text: str, key: str) -> None:
        voice = self.edge_voices.get(key, self.edge_voices["default"])
        for attempt in range(3):
            try:
                if self._ffplay:
                    if self._edge_stream_ffplay(text, voice):
                        return
                else:
                    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="robi_tts_")
                    os.close(fd)
                    try:
                        import edge_tts

                        asyncio.run(
                            edge_tts.Communicate(
                                text, voice, rate=self.rate,
                                volume=self.volume, pitch=self.pitch,
                            ).save(path)
                        )
                        if os.path.getsize(path) > 0 and self._pygame is not None:
                            self._pygame.mixer.music.load(path)
                            self._pygame.mixer.music.play()
                            while self._pygame.mixer.music.get_busy():
                                self._pygame.time.wait(20)
                            return
                    finally:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            except Exception:  # noqa: BLE001, S110
                pass
            time.sleep(0.4 * (attempt + 1))
        print(f"[tts] speech failed ({voice})")

    # ------------------------------------------------------------------
    def speak(self, text: str, lang: str | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.reset()
        key = self._pick_lang(text, lang)

        if self._piper and self._play_piper(text, key):
            return
        self._edge_speak(text, key)
