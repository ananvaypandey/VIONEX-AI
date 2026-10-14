"""Microphone capture and speaker playback helpers.

Kept deliberately small: `MicStream` gives fixed-size int16 frames at 16 kHz
(what webrtcvad and faster-whisper both want), and `resolve_device` maps a
config value to a sounddevice device index.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd


def resolve_device(value, kind: str):
    """Turn a config device (None | index | name substring) into a device index."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    needle = str(value).lower()
    for idx, dev in enumerate(sd.query_devices()):
        if needle in dev["name"].lower():
            if kind == "input" and dev["max_input_channels"] > 0:
                return idx
            if kind == "output" and dev["max_output_channels"] > 0:
                return idx
    return None


def list_devices() -> str:
    lines = ["sounddevice devices:"]
    for idx, dev in enumerate(sd.query_devices()):
        lines.append(
            f"  [{idx}] {dev['name']} "
            f"(in={dev['max_input_channels']}, out={dev['max_output_channels']})"
        )
    return "\n".join(lines)


class MicStream:
    """Blocking 16-bit mono microphone stream."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_ms: int = 20,
        device=None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.device = resolve_device(device, "input")
        self._stream: sd.InputStream | None = None

    def __enter__(self) -> "MicStream":
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self.frame_samples,
            device=self.device,
        )
        self._stream.start()
        return self

    def read_frame(self) -> np.ndarray:
        """Return one int16 frame of shape (frame_samples,)."""
        assert self._stream is not None, "MicStream not opened"
        data, _overflowed = self._stream.read(self.frame_samples)
        return np.asarray(data, dtype=np.int16).reshape(-1)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __exit__(self, *exc) -> None:
        self.close()
