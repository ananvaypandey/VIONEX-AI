"""Robot link layer: abstracts the Arduino so the AI brain does not care
whether a real board is connected (mock) or not (serial)."""

from .base import Anim, RobotLink, make_robot

__all__ = ["Anim", "RobotLink", "make_robot"]
