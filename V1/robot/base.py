"""Abstract robot link + the fixed set of animation names.

The AI brain only ever talks to a `RobotLink`. Two implementations exist:
  * `robot.mock.MockRobot`       - logs commands (no hardware needed)
  * `robot.serial_link.SerialRobot` - real Arduino over pyserial

See PROTOCOL.md for the wire format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Anim:
    """Canonical animation names (must match PROTOCOL.md)."""

    IDLE = "idle"
    TALK = "talk"
    BYE = "bye"
    HELLO = "hello"
    HANDSHAKE = "handshake"
    WALK = "walk"

    #: One-shot gestures the LLM is allowed to request.
    ONE_SHOTS = (BYE, HELLO, HANDSHAKE, WALK)

    @classmethod
    def is_valid(cls, name: str) -> bool:
        return name in (cls.IDLE, cls.TALK, *cls.ONE_SHOTS)


class RobotLink(ABC):
    """Interface every robot transport implements."""

    @abstractmethod
    def send(self, command: str) -> None:
        """Send a raw protocol line (see PROTOCOL.md)."""

    # ---- convenient high-level helpers (shared by all transports) ----
    def idle(self) -> None:
        self.send("IDLE")

    def stop(self) -> None:
        self.send("STOP")

    def play(self, name: str) -> None:
        self.send(f"PLAY {name}")

    def start_talk(self) -> None:
        self.play(Anim.TALK)

    def close(self) -> None:
        """Release resources. Override if needed."""

    # Context-manager sugar
    def __enter__(self) -> "RobotLink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def from_config(cfg: dict) -> "RobotLink":
        return make_robot(cfg)


def make_robot(cfg: dict) -> RobotLink:
    """Factory used by main.py. `cfg` is the `robot:` section of config.yaml."""
    mode = str(cfg.get("mode", "mock")).lower()
    if mode == "serial":
        from .serial_link import SerialRobot

        return SerialRobot(
            port=cfg.get("port"),
            baud=int(cfg.get("baud", 115200)),
        )
    from .mock import MockRobot

    return MockRobot()
