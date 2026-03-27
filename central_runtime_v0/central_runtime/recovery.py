from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .diagnostics import DiagnosticCode


@dataclass
class RecoveryAction:
    kind: str
    reason: str
    stage_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class RecoveryManager:
    def __init__(
        self,
        *,
        max_retries_per_stage: int = 2,
        hold_reobserve_s: float = 2.0,
        allow_fallback_to_search: bool = True,
    ):
        self.max_retries_per_stage = int(max(0, max_retries_per_stage))
        self.hold_reobserve_s = float(max(0.0, hold_reobserve_s))
        self.allow_fallback_to_search = bool(allow_fallback_to_search)

    def suggest(
        self,
        *,
        stage_id: str,
        stage_intent: str,
        diagnostic_code: Optional[str],
        retry_count: int,
    ) -> Optional[RecoveryAction]:
        if diagnostic_code is None:
            return None
        if diagnostic_code == DiagnosticCode.STAGE_FAILURE:
            return RecoveryAction(kind="safe_terminate", reason="stage failure criteria met", stage_id=stage_id)
        if diagnostic_code in (DiagnosticCode.REQ_MISMATCH, DiagnosticCode.ENTITY_MISMATCH, DiagnosticCode.ROLE_MISMATCH):
            if retry_count < self.max_retries_per_stage:
                return RecoveryAction(kind="retry_same_stage", reason=diagnostic_code, stage_id=stage_id)
            return RecoveryAction(kind="hold_and_reobserve", reason=diagnostic_code, stage_id=stage_id)
        if diagnostic_code == DiagnosticCode.NAV_STALLED:
            if retry_count < self.max_retries_per_stage:
                return RecoveryAction(kind="retry_same_stage", reason="navigation stalled", stage_id=stage_id)
            if self.allow_fallback_to_search:
                return RecoveryAction(kind="fallback_to_search", reason="navigation stalled", stage_id=stage_id)
        return None

