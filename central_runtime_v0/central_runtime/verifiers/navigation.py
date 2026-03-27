from __future__ import annotations

from typing import Any, Dict

from ..diagnostics import DiagnosticCode
from ..verification import VerificationResult, VerificationStatus
from .base import BaseVerifier


class NavigationVerifier(BaseVerifier):
    name = "navigation"

    def evaluate(self, **kwargs: Dict[str, Any]) -> VerificationResult:
        stage = kwargs["stage"]
        stage_state = kwargs.get("stage_state")
        if stage.intent != "NAVIGATE":
            return VerificationResult(name=self.name, status=VerificationStatus.INCONCLUSIVE, reason="not a navigate stage")

        if stage_state is None:
            return VerificationResult(name=self.name, status=VerificationStatus.INCONCLUSIVE, reason="stage runtime state unavailable")

        milestones = stage_state.milestones
        if "goal_published" not in milestones:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.FAILED,
                reason="navigate stage has no published goal",
                details={"diagnostic_code": DiagnosticCode.NAV_NO_GOAL},
            )

        if "goal_reached" in milestones:
            return VerificationResult(name=self.name, status=VerificationStatus.VERIFIED, reason="goal reached")

        if "motion_confirmed" in milestones:
            return VerificationResult(name=self.name, status=VerificationStatus.INCONCLUSIVE, reason="navigation is progressing")

        return VerificationResult(
            name=self.name,
            status=VerificationStatus.INCONCLUSIVE,
            reason="waiting for navigation progress",
            details={"diagnostic_code": DiagnosticCode.NAV_STALLED},
        )

