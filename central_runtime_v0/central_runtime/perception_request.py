from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def normalize_prompt(s: str) -> str:
    """Normalize prompt to be: lowercase + trailing '.' (if no terminal punctuation)."""
    if s is None:
        return ""
    t = str(s).strip().lower()
    if not t:
        return ""
    if t.endswith((".", "!", "?")):
        return t
    return t + "."


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


@dataclass
class EntityRequest:
    entity_id: str
    prompt: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "prompt": normalize_prompt(self.prompt),
        }


class PerceptionRequestWriter:
    """
    Write /shared/perception_request.json so the perception (GSAM2) side can
    switch targets in a structured way (entity_id + prompt + role).

    Central Runtime remains the *single source of truth* for "what to perceive now".
    """

    def __init__(self, path: str):
        self.path = path

    def write(
        self,
        *,
        req_id: int,
        stage_id: str,
        primary: EntityRequest,
        cues: Optional[List[EntityRequest]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "req_id": int(req_id),
            "t_wall": float(time.time()),
            "stage_id": stage_id,
            "primary": primary.to_dict(),
            "cues": [c.to_dict() for c in (cues or [])],
        }
        if extra:
            # Allow future extension: thresholds, periods, modes, etc.
            payload["extra"] = extra
        _atomic_write_json(self.path, payload)


class CuesTxtWriter:
    """
    Optional compatibility output: write /shared/cues.txt (one cue prompt per line).
    Current infer_loop_vis_guide.py already reads cues.txt; keeping this makes rollout smoother.
    """

    def __init__(self, path: str):
        self.path = path

    def write(self, cue_prompts: List[str]) -> None:
        lines: List[str] = []
        for p in cue_prompts:
            np = normalize_prompt(p)
            if np:
                lines.append(np)
        text = "\n".join(lines) + ("\n" if lines else "")
        _atomic_write_text(self.path, text)
