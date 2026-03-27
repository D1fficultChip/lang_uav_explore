from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import os


@dataclass
class TargetLocalization:
    found: bool
    entity_id: Optional[str]
    req_id: Optional[int]
    stage_id: Optional[str]
    t_det: Optional[float]
    t_depth: Optional[float]
    t_odom: Optional[float]
    target_position_body: Optional[List[float]]
    target_position_world: Optional[List[float]]
    localization_confidence: float
    depth_valid_ratio: float
    support_pixels: int
    failure_reason: Optional[str] = None


class TargetLocalizationReader:
    def __init__(self, json_path: str):
        self.path = json_path
        self._last_mtime = -1.0

    def poll(self) -> Optional[Dict[str, Any]]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        if st.st_mtime <= self._last_mtime:
            return None
        self._last_mtime = st.st_mtime
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def to_localization(obj: Dict[str, Any]) -> Optional[TargetLocalization]:
        if not isinstance(obj, dict):
            return None

        def _f(name: str) -> Optional[float]:
            v = obj.get(name)
            if v is None:
                return None
            try:
                return float(v)
            except Exception:
                return None

        def _vec(name: str) -> Optional[List[float]]:
            v = obj.get(name)
            if not isinstance(v, list) or len(v) < 3:
                return None
            try:
                return [float(v[0]), float(v[1]), float(v[2])]
            except Exception:
                return None

        req_id = obj.get("req_id")
        try:
            req_id = int(req_id) if req_id is not None else None
        except Exception:
            req_id = None

        entity_id = obj.get("entity_id")
        if entity_id is not None:
            entity_id = str(entity_id)

        stage_id = obj.get("stage_id")
        if stage_id is not None:
            stage_id = str(stage_id)

        support_pixels = obj.get("support_pixels", 0)
        try:
            support_pixels = int(support_pixels)
        except Exception:
            support_pixels = 0

        return TargetLocalization(
            found=bool(obj.get("found", False)),
            entity_id=entity_id,
            req_id=req_id,
            stage_id=stage_id,
            t_det=_f("t_det"),
            t_depth=_f("t_depth"),
            t_odom=_f("t_odom"),
            target_position_body=_vec("target_position_body"),
            target_position_world=_vec("target_position_world"),
            localization_confidence=float(obj.get("localization_confidence", 0.0) or 0.0),
            depth_valid_ratio=float(obj.get("depth_valid_ratio", 0.0) or 0.0),
            support_pixels=support_pixels,
            failure_reason=None if obj.get("failure_reason") in (None, "") else str(obj.get("failure_reason")),
        )
