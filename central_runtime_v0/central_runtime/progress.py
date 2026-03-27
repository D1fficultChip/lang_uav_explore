from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class StageMilestone:
    name: str
    t_wall: float
    details: Dict[str, Any] = field(default_factory=dict)


class ProgressTracker:
    def __init__(self):
        self._milestones: Dict[str, Dict[str, StageMilestone]] = {}

    def mark(
        self,
        stage_id: str,
        name: str,
        *,
        t_wall: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        bucket = self._milestones.setdefault(stage_id, {})
        if name in bucket:
            return False
        bucket[name] = StageMilestone(name=name, t_wall=float(t_wall), details=details or {})
        return True

    def has(self, stage_id: str, name: str) -> bool:
        return name in self._milestones.get(stage_id, {})

    def time_of(self, stage_id: str, name: str) -> Optional[float]:
        ms = self._milestones.get(stage_id, {}).get(name)
        return None if ms is None else ms.t_wall

    def summary(self, stage_id: str) -> Dict[str, float]:
        return {name: ms.t_wall for name, ms in self._milestones.get(stage_id, {}).items()}

