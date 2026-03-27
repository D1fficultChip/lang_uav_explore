from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SemanticVerifyRequest:
    req_id: Optional[int]
    stage_id: str
    entity_id: str
    query_type: str
    primary_prompt: str
    cue_prompts: List[str]
    frame_path: Optional[str] = None
    vis_path: Optional[str] = None
    topk_candidates: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    runtime_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticVerifyResult:
    verify_status: str
    supports_primary_target: Optional[bool] = None
    supports_relation: Dict[str, str] = field(default_factory=dict)
    scene_consistency: Optional[str] = None
    failure_hypothesis: Optional[str] = None
    need_additional_view: bool = False
    verification_confidence: float = 0.0
    recommended_followup: Optional[str] = None
    explanation: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticVerifyResponse:
    result: Optional[SemanticVerifyResult]
    accepted: bool
    reason: str
    raw_text: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class BaseSemanticVerifier(ABC):
    name = "base"

    @abstractmethod
    def verify(self, request: SemanticVerifyRequest) -> SemanticVerifyResponse:
        ...


class NoopSemanticVerifier(BaseSemanticVerifier):
    name = "noop"

    def verify(self, request: SemanticVerifyRequest) -> SemanticVerifyResponse:
        return SemanticVerifyResponse(result=None, accepted=False, reason="semantic verifier disabled")


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


class APISemanticVerifier(BaseSemanticVerifier):
    name = "api"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str,
        timeout_s: float = 20.0,
        temperature: float = 0.1,
        include_image: bool = False,
        system_prompt: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_s = float(timeout_s)
        self.temperature = float(temperature)
        self.include_image = bool(include_image)
        self.system_prompt = system_prompt or (
            "You are a bounded UAV semantic verification module for a real aerial robot. "
            "You DO NOT control the robot. You only verify whether current visual evidence supports "
            "the current task-stage hypothesis. "
            "Return ONLY one JSON object with keys: verify_status, supports_primary_target, "
            "supports_relation, scene_consistency, failure_hypothesis, need_additional_view, "
            "verification_confidence, recommended_followup, explanation, extra. "
            "Allowed verify_status values: supported, weakly_supported, not_supported, inconclusive. "
            "Allowed recommended_followup values: continue, hold_and_reobserve, retry_same_stage, "
            "insert_verify_stage, switch_cue_priority, fallback_to_search, none. "
            "For supports_relation, use weak/medium/strong/none for each relation key. "
            "Be conservative: if evidence is insufficient, return inconclusive rather than supported. "
            "Do not output prose outside JSON."
        )

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def _encode_image_data_url(self, path: Optional[str]) -> Optional[str]:
        if not self.include_image or not path or not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except Exception:
            return None
        lower = path.lower()
        if lower.endswith(".png"):
            mime = "image/png"
        else:
            mime = "image/jpeg"
        return f"data:{mime};base64,{data}"

    def _request_payload(self, request: SemanticVerifyRequest) -> Dict[str, Any]:
        body = {
            "request": {
                "req_id": request.req_id,
                "stage_id": request.stage_id,
                "entity_id": request.entity_id,
                "query_type": request.query_type,
                "primary_prompt": request.primary_prompt,
                "cue_prompts": request.cue_prompts,
                "topk_candidates": request.topk_candidates,
                "relations": request.relations,
                "assumptions": request.assumptions,
                "open_questions": request.open_questions,
                "runtime_context": request.runtime_context,
            }
        }

        user_content: List[Dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps(body, ensure_ascii=False),
            }
        ]
        data_url = self._encode_image_data_url(request.frame_path)
        if data_url:
            user_content.append({"type": "image_url", "image_url": {"url": data_url}})

        return {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

    def verify(self, request: SemanticVerifyRequest) -> SemanticVerifyResponse:
        api_key = self._api_key()
        if not api_key:
            return SemanticVerifyResponse(result=None, accepted=False, reason=f"missing API key env: {self.api_key_env}")

        payload = self._request_payload(request)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            return SemanticVerifyResponse(result=None, accepted=False, reason=f"http_error:{e.code}", raw_text=body)
        except Exception as e:
            return SemanticVerifyResponse(result=None, accepted=False, reason=f"request_error:{e}")

        try:
            payload_resp = json.loads(raw)
        except Exception:
            payload_resp = {"raw_text": raw}

        content = ""
        if isinstance(payload_resp, dict):
            try:
                choices = payload_resp.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
            except Exception:
                content = ""
            if not content and "output_text" in payload_resp:
                content = str(payload_resp.get("output_text") or "")

        parsed = _extract_json_obj(content)
        if parsed is None:
            return SemanticVerifyResponse(
                result=None,
                accepted=False,
                reason="response_not_json",
                raw_text=content or raw,
                raw_payload=payload_resp if isinstance(payload_resp, dict) else {},
            )

        result = SemanticVerifyResult(
            verify_status=str(parsed.get("verify_status", "inconclusive")).strip().lower(),
            supports_primary_target=parsed.get("supports_primary_target"),
            supports_relation=parsed.get("supports_relation") if isinstance(parsed.get("supports_relation"), dict) else {},
            scene_consistency=None if parsed.get("scene_consistency") in (None, "") else str(parsed.get("scene_consistency")),
            failure_hypothesis=None if parsed.get("failure_hypothesis") in (None, "") else str(parsed.get("failure_hypothesis")),
            need_additional_view=bool(parsed.get("need_additional_view", False)),
            verification_confidence=float(parsed.get("verification_confidence", 0.0) or 0.0),
            recommended_followup=None if parsed.get("recommended_followup") in (None, "") else str(parsed.get("recommended_followup")),
            explanation=str(parsed.get("explanation", "")),
            extra=parsed.get("extra") if isinstance(parsed.get("extra"), dict) else {},
        )
        return SemanticVerifyResponse(
            result=result,
            accepted=True,
            reason="ok",
            raw_text=content,
            raw_payload=payload_resp if isinstance(payload_resp, dict) else {},
        )
