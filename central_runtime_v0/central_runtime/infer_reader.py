from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
import json
import os
import time

@dataclass
class Detection:
    found: bool
    score: float
    bbox_xyxy: Optional[List[float]]  # [x1,y1,x2,y2] in pixels
    t_wall: float  # seconds

class InferJsonReader:
    """Poll /shared/infer.json and return new detections when the file updates."""

    def __init__(self, infer_json_path: str):
        self.path = infer_json_path
        self._last_mtime: float = -1.0

    def poll(self) -> Optional[Dict[str, Any]]:
        """Return parsed json dict if updated since last poll, else None."""
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
            # writer might be mid-replace; try next tick
            return None

    @staticmethod
    def to_detection(obj: Dict[str, Any]) -> Optional[Detection]:
        # The v0 runtime expects infer.json to contain at least: found(bool), score(float), t_wall(float)
        if not isinstance(obj, dict):
            return None
        found = bool(obj.get("found", False))
        score = obj.get("score", 0.0)
        try:
            score = float(score)
        except Exception:
            score = 0.0
        bbox = obj.get("bbox_xyxy", None) or obj.get("bbox", None)
        if bbox is not None and isinstance(bbox, list) and len(bbox) == 4:
            bbox = [float(x) for x in bbox]
        else:
            bbox = None
        t_wall = obj.get("t_wall", None)
        if t_wall is None:
            # fallback to now if not provided
            t_wall = time.time()
        try:
            t_wall = float(t_wall)
        except Exception:
            t_wall = time.time()
        return Detection(found=found, score=score, bbox_xyxy=bbox, t_wall=t_wall)
