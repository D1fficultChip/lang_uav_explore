from __future__ import annotations

from typing import Any, Dict

from ..verification import VerificationResult, VerificationStatus
from .base import BaseVerifier


class ConsistencyVerifier(BaseVerifier):
    name = "consistency"

    def evaluate(self, **kwargs: Dict[str, Any]) -> VerificationResult:
        world_state = kwargs["world_state"]
        active_entity_id = kwargs["active_entity_id"]
        ent = world_state.entities.get(active_entity_id)
        if ent is None:
            return VerificationResult(name=self.name, status=VerificationStatus.INCONCLUSIVE, entity_id=active_entity_id, reason="entity state unavailable")

        if ent.last_primary_detection is None and ent.cue_support_score > 0.0:
            return VerificationResult(
                name=self.name,
                status=VerificationStatus.INCONCLUSIVE,
                entity_id=active_entity_id,
                reason="cue support exists but no primary evidence yet",
                score=float(ent.cue_support_score),
            )

        return VerificationResult(
            name=self.name,
            status=VerificationStatus.INCONCLUSIVE,
            entity_id=active_entity_id,
            reason="no consistency contradiction detected",
        )
