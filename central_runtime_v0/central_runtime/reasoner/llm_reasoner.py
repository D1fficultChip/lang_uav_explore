from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional

from .base import BaseRuntimeReasoner, ReasonerProposal, ReasonerResponse


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


class LLMRuntimeReasoner(BaseRuntimeReasoner):
    name = "llm_api"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str,
        timeout_s: float = 20.0,
        temperature: float = 0.1,
        system_prompt: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_s = float(timeout_s)
        self.temperature = float(temperature)
        self.system_prompt = system_prompt or (
            "You are a bounded UAV runtime reasoner. "
            "Return ONLY one JSON object with keys: assessment, evidence_summary, "
            "failure_hypothesis, suggested_action, target_stage_id, confidence, extra. "
            "Do not output prose outside JSON. "
            "Never suggest low-level control. Only use the allowed candidate actions."
        )

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def _request_payload(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
            ],
        }

    def propose(self, summary: Dict[str, Any]) -> ReasonerResponse:
        api_key = self._api_key()
        if not api_key:
            return ReasonerResponse(proposal=None, accepted=False, reason=f"missing API key env: {self.api_key_env}")

        payload = self._request_payload(summary)
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
            return ReasonerResponse(proposal=None, accepted=False, reason=f"http_error:{e.code}", raw_text=body)
        except Exception as e:
            return ReasonerResponse(proposal=None, accepted=False, reason=f"request_error:{e}")

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
            return ReasonerResponse(
                proposal=None,
                accepted=False,
                reason="response_not_json",
                raw_text=content or raw,
                raw_payload=payload_resp if isinstance(payload_resp, dict) else {},
            )

        proposal = ReasonerProposal(
            proposal_id=f"rp_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            stage_id=str(summary.get("stage", {}).get("stage_id", "")),
            assessment=str(parsed.get("assessment", "")),
            evidence_summary=str(parsed.get("evidence_summary", "")),
            failure_hypothesis=None if parsed.get("failure_hypothesis") in (None, "") else str(parsed.get("failure_hypothesis")),
            suggested_action=str(parsed.get("suggested_action", "")).strip(),
            target_stage_id=None if parsed.get("target_stage_id") in (None, "") else str(parsed.get("target_stage_id")),
            confidence=float(parsed.get("confidence", 0.0) or 0.0),
            extra=parsed.get("extra") if isinstance(parsed.get("extra"), dict) else {},
        )
        return ReasonerResponse(
            proposal=proposal,
            accepted=True,
            reason="ok",
            raw_text=content,
            raw_payload=payload_resp if isinstance(payload_resp, dict) else {},
        )
