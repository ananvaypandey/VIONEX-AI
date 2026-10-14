"""Serial robot: real Arduino over USB, implementing PROTOCOL.md.

Talks to the board on `robot.port` (config.yaml, e.g. "COM9"), or auto-detects
an Arduino/CH340/CP210x port if the configured one is absent. It keeps a
background watcher thread, so if the robot is unplugged and re-plugged the app
reconnects on its own - no restart needed. Until a board is present, commands
are simply dropped.
"""

from __future__ import annotations

import threading
import time

from .base import RobotLink

try:
    import serial
    from serial.tools import list_ports

    _HAVE_SERIAL = True
except Exception:  # pyserial not installed
    _HAVE_SERIAL = False

# Substrings that identify common Arduino/USB-serial bridges.
_PORT_HINTS = (
    "arduino",
    "ch340",
    "ch341",
    "cp210",
    "wch",
    "usb serial",
    "usb-serial",
    "silicon labs",
    "ft232",
    "ftdi",
)


def find_port() -> str | None:
    """Return the most likely Arduino COM port, or None if none found."""
    if not _HAVE_SERIAL:
        return None
    ports = list(list_ports.comports())
    for p in ports:
        blob = " ".join(str(x) for x in (p.device, p.description, p.manufacturer)).lower()
        if any(h in blob for h in _PORT_HINTS):
            return p.device
    # Fall back to the first non-Bluetooth port if any.
    for p in ports:
        if "bluetooth" not in (p.description or "").lower():
            return p.device
    return ports[0].device if ports else None


class SerialRobot(RobotLink):
    """Sends newline-terminated commands to the Arduino and reads events.

    Never raises when the board isn't plugged in: it waits for it and connects
    automatically once it appears.
    """

    def __init__(
        self,
        port: str | None = None,
        baud: int = 115200,
        retry_s: float = 2.0,
    ) -> None:
        if not _HAVE_SERIAL:
            raise RuntimeError(
                "pyserial is not installed. Run: pip install pyserial "
                "or keep robot.mode: mock in config.yaml"
            )

        self.wanted_port = port
        self.baud = int(baud)
        self.retry_s = float(retry_s)
        self.ser = None
        self.port = None
        self._last_event: str | None = None
        self._running = True
        self._lock = threading.Lock()

        if self._connect():
            self.idle()
        else:
            target = self.wanted_port or "any Arduino port"
            print(f"[robot:serial] waiting for robot on {target} (plug it in anytime)")

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
        self._watcher.start()

    # -- connection management ----------------------------------------
    def _candidate_ports(self):
        if self.wanted_port:
            yield self.wanted_port
        auto = find_port()
        if auto and auto != self.wanted_port:
            yield auto

    def _connect(self) -> bool:
        for p in self._candidate_ports():
            try:
                ser = serial.Serial(p, self.baud, timeout=0.1, write_timeout=1.0)
            except Exception:
                continue
            # Opening the port resets the Uno/Nano; give it time to boot.
            time.sleep(2.0)
            with self._lock:
                self.ser = ser
                self.port = p
            print(f"[robot:serial] connected on {p} @ {self.baud} baud")
            self.idle()
            return True
        return False

    def _disconnect(self) -> None:
        with self._lock:
            ser = self.ser
            self.ser = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
            print(f"[robot:serial] disconnected from {self.port}")

    def _watch_loop(self) -> None:
        """Reconnect automatically if the board is unplugged and re-plugged."""
        while self._running:
            if self.ser is None:
                self._connect()
            time.sleep(self.retry_s)

    # -- outgoing ------------------------------------------------------
    def send(self, command: str) -> None:
        ser = self.ser
        if ser is None:
            return  # not connected yet; drop silently
        line = (command.strip() + "\n").encode("ascii", errors="ignore")
        try:
            ser.write(line)
            ser.flush()
        except Exception as exc:  # keep the app alive on unplug
            print(f"[robot:serial] write failed: {exc}")
            self._disconnect()

    # -- incoming (READY / DONE <name>) --------------------------------
    def _read_loop(self) -> None:
        while self._running:
            ser = self.ser
            if ser is None:
                time.sleep(0.2)
                continue
            try:
                raw = ser.readline()
            except Exception:
                self._disconnect()
                continue
            if not raw:
                continue
            msg = raw.decode("ascii", errors="ignore").strip()
            if not msg:
                continue
            self._last_event = msg
            print(f"[robot:serial] <- {msg}")

    def last_event(self) -> str | None:
        return self._last_event

    def close(self) -> None:
        self._running = False
        try:
            self.idle()
            self.stop()
        except Exception:
            pass
        self._disconnect()
        print("[robot:serial] closed")
