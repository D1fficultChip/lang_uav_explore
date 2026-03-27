from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, List
import json
import os
import time

@dataclass
class ProposalCandidate:
    rank: int
    bbox_xyxy: Optional[List[float]]
    score: float
    class_name: Optional[str] = None
    text_conf: Optional[float] = None
    mask_score: Optional[float] = None
    mask_quality: Optional[float] = None


@dataclass
class Detection:
    found: bool
    score: float
    bbox_xyxy: Optional[List[float]]  # [x1,y1,x2,y2] in pixels
    t_wall: float  # seconds
    req_id: Optional[int] = None
    stage_id: Optional[str] = None
    entity_id: Optional[str] = None
    role: Optional[str] = None
    class_name: Optional[str] = None
    prompt: Optional[str] = None
    img_width: Optional[int] = None
    img_height: Optional[int] = None
    source_path: Optional[str] = None
    proposal_status: Optional[str] = None
    best_candidate: Optional[ProposalCandidate] = None
    topk_candidates: List[ProposalCandidate] = field(default_factory=list)
    mask_quality: Optional[float] = None
    spatial_hint: Dict[str, Any] = field(default_factory=dict)
    proposal_uncertainty: Dict[str, Any] = field(default_factory=dict)

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
        req_id = obj.get("req_id")
        try:
            req_id = int(req_id) if req_id is not None else None
        except Exception:
            req_id = None

        stage_id = obj.get("stage_id")
        if stage_id is not None:
            stage_id = str(stage_id)

        entity_id = obj.get("entity_id")
        if entity_id is not None:
            entity_id = str(entity_id)

        role = obj.get("role")
        if role is not None:
            role = str(role)

        class_name = obj.get("class_name")
        if class_name is not None:
            class_name = str(class_name)

        prompt = obj.get("prompt")
        if prompt is not None:
            prompt = str(prompt)

        img_width = obj.get("img_width")
        try:
            img_width = int(img_width) if img_width is not None else None
        except Exception:
            img_width = None

        img_height = obj.get("img_height")
        try:
            img_height = int(img_height) if img_height is not None else None
        except Exception:
            img_height = None

        def _parse_candidate(raw: Any) -> Optional[ProposalCandidate]:
            if not isinstance(raw, dict):
                return None
            bbox = raw.get("bbox_xyxy", raw.get("bbox"))
            if isinstance(bbox, list) and len(bbox) == 4:
                try:
                    bbox = [float(x) for x in bbox]
                except Exception:
                    bbox = None
            else:
                bbox = None
            try:
                rank = int(raw.get("rank", 0))
            except Exception:
                rank = 0
            try:
                cand_score = float(raw.get("score", 0.0) or 0.0)
            except Exception:
                cand_score = 0.0

            def _f(name: str) -> Optional[float]:
                v = raw.get(name)
                if v is None:
                    return None
                try:
                    return float(v)
                except Exception:
                    return None

            class_name_local = raw.get("class_name")
            if class_name_local is not None:
                class_name_local = str(class_name_local)
            return ProposalCandidate(
                rank=rank,
                bbox_xyxy=bbox,
                score=cand_score,
                class_name=class_name_local,
                text_conf=_f("text_conf"),
                mask_score=_f("mask_score"),
                mask_quality=_f("mask_quality"),
            )

        proposal_status = obj.get("proposal_status")
        if proposal_status is not None:
            proposal_status = str(proposal_status)

        best_candidate = _parse_candidate(obj.get("best_candidate"))
        topk_candidates: List[ProposalCandidate] = []
        raw_topk = obj.get("topk_candidates")
        if isinstance(raw_topk, list):
            for item in raw_topk:
                cand = _parse_candidate(item)
                if cand is not None:
                    topk_candidates.append(cand)

        mask_quality = obj.get("mask_quality")
        try:
            mask_quality = float(mask_quality) if mask_quality is not None else None
        except Exception:
            mask_quality = None

        spatial_hint = obj.get("spatial_hint")
        if not isinstance(spatial_hint, dict):
            spatial_hint = {}

        proposal_uncertainty = obj.get("proposal_uncertainty")
        if not isinstance(proposal_uncertainty, dict):
            proposal_uncertainty = {}

        return Detection(
            found=found,
            score=score,
            bbox_xyxy=bbox,
            t_wall=t_wall,
            req_id=req_id,
            stage_id=stage_id,
            entity_id=entity_id,
            role=role,
            class_name=class_name,
            prompt=prompt,
            img_width=img_width,
            img_height=img_height,
            source_path=obj.get("vis_path"),
            proposal_status=proposal_status,
            best_candidate=best_candidate,
            topk_candidates=topk_candidates,
            mask_quality=mask_quality,
            spatial_hint=spatial_hint,
            proposal_uncertainty=proposal_uncertainty,
        )
