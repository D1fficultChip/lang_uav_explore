from __future__ import annotations

from typing import Any, Dict

from ..diagnostics import DiagnosticCode
from ..verification import VerificationResult, VerificationStatus
from .base import BaseVerifier


class PerceptionVerifier(BaseVerifier):
    name = "perception"

    def __init__(self, cfg: Dict[str, Any] | None = None):
        cfg = cfg or {}
        self.require_req_match = bool(cfg.get("require_req_match", True))
        self.require_entity_match = bool(cfg.get("require_entity_match", True))
        self.require_primary_role = bool(cfg.get("require_primary_role", True))
        self.stable_hits_for_verify = int(max(1, cfg.get("stable_hits_for_verify", 2)))
        self.max_detection_age_s = float(cfg.get("max_detection_age_s", 1.0))
        self.default_min_score = float(cfg.get("min_score", 0.5))
        self.min_mask_quality = float(cfg.get("min_mask_quality", 0.55))
        self.max_ambiguous_top2_gap = float(cfg.get("max_ambiguous_top2_gap", 0.08))

    def evaluate(self, **kwargs: Dict[str, Any]) -> VerificationResult:
        now = float(kwargs["now"])
        world_state = kwargs["world_state"]
        blackboard = kwargs["blackboard"]
        active_entity_id = kwargs["active_entity_id"]
        current_req_id = kwargs.get("current_req_id")
        stage_id = kwargs.get("stage_id")
        min_score = float(kwargs.get("min_score", self.default_min_score))

        ent = world_state.entities.get(active_entity_id)
        if ent is None or ent.last_primary_detection is None:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.INCONCLUSIVE,
                entity_id=active_entity_id,
                reason="no primary detection yet",
            )

        det = ent.last_primary_detection
        age = max(0.0, now - float(det.t_wall))
        if age > self.max_detection_age_s:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.FAILED,
                entity_id=active_entity_id,
                reason="primary detection is stale",
                details={"diagnostic_code": DiagnosticCode.PERCEPTION_STALE, "age_s": age},
            )

        if self.require_req_match and current_req_id is not None and det.req_id is not None and det.req_id != current_req_id:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.CONTRADICTED,
                entity_id=active_entity_id,
                reason="req_id mismatch",
                details={"diagnostic_code": DiagnosticCode.REQ_MISMATCH, "det_req_id": det.req_id, "current_req_id": current_req_id},
            )

        if self.require_entity_match and det.entity_id is not None and det.entity_id != active_entity_id:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.CONTRADICTED,
                entity_id=active_entity_id,
                reason="entity_id mismatch",
                details={"diagnostic_code": DiagnosticCode.ENTITY_MISMATCH, "det_entity_id": det.entity_id},
            )

        if self.require_primary_role and det.role is not None and det.role != "primary":
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.CONTRADICTED,
                entity_id=active_entity_id,
                reason="role mismatch",
                details={"diagnostic_code": DiagnosticCode.ROLE_MISMATCH, "det_role": det.role},
            )

        if det.proposal_status == "multi_candidate_ambiguous":
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.INCONCLUSIVE,
                entity_id=active_entity_id,
                score=float(det.score),
                reason="proposal is ambiguous",
                details={
                    "diagnostic_code": DiagnosticCode.LOW_CONFIDENCE,
                    "proposal_status": det.proposal_status,
                    "proposal_uncertainty": det.proposal_uncertainty,
                    "max_ambiguous_top2_gap": self.max_ambiguous_top2_gap,
                },
            )

        if det.mask_quality is not None and float(det.mask_quality) < self.min_mask_quality:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.INCONCLUSIVE,
                entity_id=active_entity_id,
                score=float(det.score),
                reason="mask quality below verification threshold",
                details={
                    "diagnostic_code": DiagnosticCode.LOW_CONFIDENCE,
                    "mask_quality": float(det.mask_quality),
                    "min_mask_quality": self.min_mask_quality,
                },
            )

        streak = blackboard.update_hit_streak(active_entity_id, min_score)
        ent.stable_hit_streak = streak

        if not det.found or float(det.score) < min_score:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.INCONCLUSIVE,
                entity_id=active_entity_id,
                reason="score below verification threshold",
                score=float(det.score),
                details={"diagnostic_code": DiagnosticCode.LOW_CONFIDENCE, "min_score": min_score},
            )

        if streak < self.stable_hits_for_verify:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.INCONCLUSIVE,
                entity_id=active_entity_id,
                reason="waiting for stable hits",
                score=float(det.score),
                details={"stable_hit_streak": streak, "required_hits": self.stable_hits_for_verify, "stage_id": stage_id},
            )

        return VerificationResult(
            name=self.name,
            status=VerificationStatus.VERIFIED,
            entity_id=active_entity_id,
            score=float(det.score),
            reason="primary evidence verified",
            details={"stable_hit_streak": streak},
        )
