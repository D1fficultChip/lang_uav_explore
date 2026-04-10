from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class DiagnosticCode:
    PERCEPTION_STALE = "perception_stale"
    REQ_MISMATCH = "req_mismatch"
    ENTITY_MISMATCH = "entity_mismatch"
    ROLE_MISMATCH = "role_mismatch"
    LOW_CONFIDENCE = "low_confidence"
    SEMANTIC_INCONCLUSIVE = "semantic_inconclusive"
    SEMANTIC_NOT_SUPPORTED = "semantic_not_supported"
    SEMANTIC_NEEDS_VIEW = "semantic_needs_view"
    NAV_NO_GOAL = "nav_no_goal"
    NAV_STALLED = "nav_stalled"
    SEARCH_STUCK = "search_stuck"
    TRACK_TARGET_LOST = "track_target_lost"
    ESCAPE_FAILED = "escape_failed"
    STAGE_FAILURE = "stage_failure"


@dataclass
class DiagnosticEvent:
    code: str
    severity: str
    message: str
    t_wall: float
    stage_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class DiagnosticsEngine:
    def __init__(self, max_events: int = 50):
        self.max_events = int(max(1, max_events))
        self.recent: List[DiagnosticEvent] = []

    def emit(
        self,
        *,
        code: str,
        severity: str,
        message: str,
        t_wall: float,
        stage_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> DiagnosticEvent:
        ev = DiagnosticEvent(
            code=code,
            severity=severity,
            message=message,
            t_wall=float(t_wall),
            stage_id=stage_id,
            details=details or {},
        )
        self.recent.append(ev)
        self.recent = self.recent[-self.max_events :]
        return ev

    def latest(self, stage_id: Optional[str] = None) -> Optional[DiagnosticEvent]:
        if stage_id is None:
            return self.recent[-1] if self.recent else None
        for ev in reversed(self.recent):
            if ev.stage_id == stage_id:
                return ev
        return None

    def summary(self, stage_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for ev in self.recent:
            if stage_id is not None and ev.stage_id != stage_id:
                continue
            out.append(
                {
                    "code": ev.code,
                    "severity": ev.severity,
                    "message": ev.message,
                    "t_wall": ev.t_wall,
                    "details": ev.details,
                }
            )
        return out
