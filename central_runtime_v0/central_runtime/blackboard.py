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
        """
        Update and return consecutive-hit streak for (entity_id,min_score).

        Fix: streak only increases when NEW evidence arrives (det.t_wall advances).
        Also: if detection is too old, treat as miss to avoid "stale hit" accumulating.
        """
        st = self._get_state(entity_id, min_score)
        det = self.latest.get(entity_id)
        now = time.time()

        if det is None:
            st.streak = 0
            st.visible_since = None
            st.last_update_t = None
            return 0

        t_det = float(det.t_wall)

        # 1) stale-protection: if det hasn't been updated for a while, do NOT keep counting it
        #    (tune this threshold; start with 0.5~1.0s)
        MAX_DET_AGE_S = 1.0
        if (now - t_det) > MAX_DET_AGE_S:
            st.streak = 0
            st.visible_since = None
            # note: keep last_update_t as-is or set to t_det; either is fine
            st.last_update_t = t_det
            return 0

        # 2) Only count when NEW evidence arrives
        if st.last_update_t is not None and t_det <= float(st.last_update_t) + 1e-6:
            # same evidence as last time → do not change streak
            return st.streak

        hit = self.is_hit(entity_id, min_score)
        if hit:
            st.streak += 1
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
