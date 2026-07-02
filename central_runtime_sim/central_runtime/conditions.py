from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional
import time

from .blackboard import Blackboard

class ConditionEngine:
    """Evaluate plan event objects of the form {type, params, description?}."""

    def __init__(self, blackboard: Blackboard, verified_cfg: Optional[Dict[str, Any]] = None):
        self.bb = blackboard
        self.verified_cfg = verified_cfg or {}

    def eval(self, event: Dict[str, Any], stage_enter_t: float) -> bool:
        etype = event.get("type")
        params = event.get("params", {})
        if etype == "TIMEOUT":
            return self._timeout(params, stage_enter_t)
        if etype == "PERCEPTION_FOUND":
            return self._perception_found(params)
        if etype == "VISIBLE_FOR":
            return self._visible_for(params)
        if etype == "VERIFIED":
            return self._verified(params, stage_enter_t)
        raise ValueError(f"Unsupported event type: {etype}")

    def _timeout(self, params: Dict[str, Any], stage_enter_t: float) -> bool:
        t = float(params.get("time_s", 0.0))
        return (time.time() - stage_enter_t) >= t

    def _perception_found(self, params: Dict[str, Any]) -> bool:
        eid = params["entity_id"]
        k = int(params.get("k_hits", 1))
        min_score = float(params.get("min_score", 0.0))
        streak = self.bb.update_hit_streak(eid, min_score)
        return streak >= k

    def _visible_for(self, params: Dict[str, Any]) -> bool:
        eid = params["entity_id"]
        seconds = float(params.get("seconds", 0.0))
        min_score = float(params.get("min_score", self.verified_cfg.get("visible_min_score", 0.0)))
        # refresh streak/visible_since based on latest detection
        self.bb.update_hit_streak(eid, min_score)
        vis = self.bb.visible_duration(eid, min_score)
        return vis >= seconds

    def _verified(self, params: Dict[str, Any], stage_enter_t: float) -> bool:
        """v0 semantics: (elapsed >= min_navigate_time_s) AND (entity visible once with min_score)."""
        eid = params.get("entity_id")
        if not eid:
            return False
        min_nav = float(self.verified_cfg.get("min_navigate_time_s", 3.0))
        if (time.time() - stage_enter_t) < min_nav:
            return False
        require_found = bool(self.verified_cfg.get("require_found", True))
        if not require_found:
            return True
        min_score = float(self.verified_cfg.get("min_score", 0.0))
        # One hit is enough
        streak = self.bb.update_hit_streak(eid, min_score)
        return streak >= 1
