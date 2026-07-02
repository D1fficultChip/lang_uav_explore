from __future__ import annotations
from typing import Any, Dict

from .base import Adapter

class NavigateStubAdapter(Adapter):
    """v0 Navigate: placeholder. Later replace with EgoPlannerAdapter.goto(target_point)."""

    def enter(self, stage: Dict[str, Any]) -> None:
        print("[Adapter][NAVIGATE] enter (stub) - TODO: connect to EGO-Planner")

    def tick(self, stage: Dict[str, Any]) -> None:
        return

    def exit(self, stage: Dict[str, Any]) -> None:
        print("[Adapter][NAVIGATE] exit")
