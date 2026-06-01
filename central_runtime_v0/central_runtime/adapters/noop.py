from __future__ import annotations

from typing import Any, Dict

from .base import Adapter


class NoopAdapter(Adapter):
    """Adapter used when a skill should be visible to the runtime but not touch ROS."""

    def __init__(self, intent: str):
        self.intent = intent

    def enter(self, stage: Dict[str, Any]) -> None:
        print(f"[Adapter][{self.intent}] enter skipped (disabled)")

    def tick(self, stage: Dict[str, Any]) -> None:
        return

    def exit(self, stage: Dict[str, Any]) -> None:
        print(f"[Adapter][{self.intent}] exit skipped (disabled)")
