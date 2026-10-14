"""Mock robot: logs protocol commands instead of driving servos.

Lets the whole AI pipeline run and be tested with no Arduino attached.
Command strings are identical to what SerialRobot sends, so behaviour matches.
"""

from __future__ import annotations

import time

from .base import RobotLink

try:
    from colorama import Fore, Style

    _C = Fore
    _R = Style.RESET_ALL
except Exception:  # colorama optional
    class _C:  # type: ignore
        CYAN = MAGENTA = YELLOW = GREEN = ""

    _R = ""


class MockRobot(RobotLink):
    """Prints every command to the console."""

    def __init__(self) -> None:
        self._t0 = time.time()
        self._last = None
        print(f"{_C.GREEN}[robot:mock]{_R} ready (no hardware attached)")

    def send(self, command: str) -> None:
        # Collapse repeated identical talk loops so the log stays readable.
        if command == self._last and command.startswith("PLAY talk"):
            return
        self._last = command
        dt = time.time() - self._t0
        print(f"{_C.CYAN}[robot:mock {dt:6.2f}s]{_R} -> {_C.MAGENTA}{command}{_R}")

    def close(self) -> None:
        print(f"{_C.GREEN}[robot:mock]{_R} closed")
