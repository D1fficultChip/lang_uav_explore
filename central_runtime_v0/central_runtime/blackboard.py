from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
import time

from .infer_reader import Detection

@dataclass
class HitState:
    streak: int = 0
    visible_since: Optional[float] = None
    last_update_t: Optional[float] = None  # wall time

class Blackboard:
    """Holds latest detections and per-(entity,threshold) streak state for condition evaluators."""

    def __init__(self):
        self.latest: Dict[str, Detection] = {}
        # key: (entity_id, min_score)
        self.hit_state: Dict[Tuple[str, float], HitState] = {}

    def update_detection(self, entity_id: str, det: Detection) -> None:
        self.latest[entity_id] = det
        # Do not update hit_state here, because thresholds differ.
        # HitState is updated lazily by evaluators through get_or_update_* methods.

    def _get_state(self, entity_id: str, min_score: float) -> HitState:
        key = (entity_id, float(min_score))
        if key not in self.hit_state:
            self.hit_state[key] = HitState()
        return self.hit_state[key]

    def reset_entity(self, entity_id: str) -> None:
        # Reset all derived states for this entity (use on stage entry to avoid carryover)
        keys = [k for k in self.hit_state.keys() if k[0] == entity_id]
        for k in keys:
            self.hit_state[k] = HitState()

    def is_hit(self, entity_id: str, min_score: float) -> bool:
        det = self.latest.get(entity_id)
        if det is None:
            return False
        return bool(det.found and det.score >= float(min_score))

    def update_hit_streak(self, entity_id: str, min_score: float) -> int:
        """Update and return consecutive-hit streak for (entity_id,min_score) based on latest detection."""
        st = self._get_state(entity_id, min_score)
        det = self.latest.get(entity_id)
        now = time.time()
        t_det = det.t_wall if det is not None else now

        hit = self.is_hit(entity_id, min_score)
        if hit:
            st.streak += 1
            # visible window
            if st.visible_since is None:
                st.visible_since = t_det
        else:
            st.streak = 0
            st.visible_since = None

        st.last_update_t = t_det
        return st.streak

    def visible_duration(self, entity_id: str, min_score: float) -> float:
        st = self._get_state(entity_id, min_score)
        det = self.latest.get(entity_id)
        if det is None:
            return 0.0
        if st.visible_since is None:
            return 0.0
        return max(0.0, float(det.t_wall) - float(st.visible_since))
