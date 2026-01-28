from __future__ import annotations
from typing import Any, Dict

from .base import Adapter

class TrackHoldAdapter(Adapter):
    """v0 Track: do nothing (assume vehicle holds). Replace later with a real tracker/servo."""

    def enter(self, stage: Dict[str, Any]) -> None:
        print("[Adapter][TRACK] enter (hold)")

    def tick(self, stage: Dict[str, Any]) -> None:
        return

    def exit(self, stage: Dict[str, Any]) -> None:
        print("[Adapter][TRACK] exit")
