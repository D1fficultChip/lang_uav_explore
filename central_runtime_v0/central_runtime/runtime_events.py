from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class RuntimeEvent:
    event_type: str
    t_wall: float
    stage_id: Optional[str] = None
    intent: Optional[str] = None
    entity_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class RuntimeEventLogger:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._fp = None
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._fp = open(path, "a", encoding="utf-8")

    def emit(
        self,
        *,
        event_type: str,
        stage_id: Optional[str] = None,
        intent: Optional[str] = None,
        entity_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        t_wall: Optional[float] = None,
    ) -> RuntimeEvent:
        ev = RuntimeEvent(
            event_type=event_type,
            t_wall=time.time() if t_wall is None else float(t_wall),
            stage_id=stage_id,
            intent=intent,
            entity_id=entity_id,
            details=details or {},
        )
        if self._fp:
            self._fp.write(json.dumps({
                "rec_type": "runtime_event",
                "event_type": ev.event_type,
                "t_wall": ev.t_wall,
                "stage_id": ev.stage_id,
                "intent": ev.intent,
                "entity_id": ev.entity_id,
                "details": ev.details,
            }, ensure_ascii=False) + "\n")
            self._fp.flush()
        return ev

    def close(self) -> None:
        if self._fp:
            self._fp.close()
            self._fp = None
