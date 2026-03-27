from __future__ import annotations

from typing import Any, Dict, List

from ..verification import VerificationResult


class StateSummarizer:
    def __init__(self, *, max_recent_events: int = 20):
        self.max_recent_events = int(max(1, max_recent_events))

    def build(self, *, executor, now: float, verifier_results: Dict[str, VerificationResult], criteria: Dict[str, Any]) -> Dict[str, Any]:
        stage = executor.plan.stages[executor.stage_id]
        ent = executor.world_state.entities.get(executor.active_entity_id)
        stage_state = executor.world_state.get_stage(executor.stage_id)
        diagnostics = executor.diagnostics.summary(executor.stage_id)[-self.max_recent_events :]
        milestones = executor.progress.summary(executor.stage_id)
        recent_evidence: List[Dict[str, Any]] = []
        if ent is not None:
            for rec in ent.recent_evidence[-self.max_recent_events :]:
                det = rec.detection
                recent_evidence.append(
                    {
                        "t_wall": rec.t_wall,
                        "source": rec.source,
                        "found": det.found,
                        "score": det.score,
                        "entity_id": det.entity_id,
                        "role": det.role,
                        "req_id": det.req_id,
                        "stage_id": det.stage_id,
                    }
                )

        return {
            "mission": {
                "plan_id": executor.plan.plan_id,
                "instruction_raw": executor.plan.instruction_raw,
                "assumptions": executor.plan.assumptions,
                "open_questions": executor.plan.open_questions,
            },
            "stage": {
                "stage_id": executor.stage_id,
                "name": stage.name,
                "intent": stage.intent,
                "elapsed_s": now - executor.stage_enter_t,
                "active_entity": executor.active_entity_id,
                "current_req_id": executor.current_req_id,
                "current_skill_id": None if stage_state is None else stage_state.current_skill_id,
                "primary_targets": stage.primary_targets,
                "cue_targets": stage.cue_targets,
                "budget": stage.budget,
                "policy": stage.policy,
                "pending_verify": None if stage_state is None else stage_state.pending_verify,
                "last_semantic_verify_t": None if stage_state is None else stage_state.last_semantic_verify_t,
                "last_semantic_followup": None if stage_state is None else stage_state.last_semantic_followup,
                "verify_window_until": None if stage_state is None else stage_state.verify_window_until,
                "verify_window_reason": None if stage_state is None else stage_state.verify_window_reason,
                "skill_contract": None if executor.skill_registry.get_by_intent(stage.intent) is None else executor.skill_registry.get_by_intent(stage.intent).to_dict(),
            },
            "milestones": milestones,
            "entity_state": None if ent is None else {
                "verification_status": ent.verification_status,
                "verified": ent.verified,
                "evidence_count": ent.evidence_count,
                "stable_hit_streak": ent.stable_hit_streak,
                "last_seen_t": ent.last_seen_t,
                "cue_support_score": ent.cue_support_score,
                "proposal_status": ent.proposal_status,
                "last_mask_quality": ent.last_mask_quality,
                "proposal_uncertainty": ent.proposal_uncertainty,
                "spatial_hint": ent.spatial_hint,
                "topk_candidates": [
                    {
                        "rank": cand.rank,
                        "score": cand.score,
                        "class_name": cand.class_name,
                        "text_conf": cand.text_conf,
                        "mask_score": cand.mask_score,
                        "mask_quality": cand.mask_quality,
                    }
                    for cand in ent.topk_candidates
                ],
                "semantic_verify_status": ent.semantic_verify_status,
                "semantic_verify_confidence": ent.semantic_verify_confidence,
                "semantic_explanation": ent.semantic_explanation,
                "semantic_failure_hypothesis": ent.semantic_failure_hypothesis,
                "semantic_need_additional_view": ent.semantic_need_additional_view,
                "semantic_recommended_followup": ent.semantic_recommended_followup,
                "semantic_supports_relation": ent.semantic_supports_relation,
                "target_position_body": ent.target_position_body,
                "target_position_world": ent.target_position_world,
                "localization_confidence": ent.localization_confidence,
                "depth_valid_ratio": ent.depth_valid_ratio,
                "support_pixels": ent.support_pixels,
                "localization_failure_reason": ent.localization_failure_reason,
            },
            "verification": {
                name: {
                    "status": res.status,
                    "score": res.score,
                    "reason": res.reason,
                    "details": res.details,
                }
                for name, res in verifier_results.items()
            },
            "criteria": criteria,
            "diagnostics": diagnostics,
            "recent_evidence": recent_evidence,
            "relations": [
                {
                    "subject_id": rel.subject_id,
                    "predicate": rel.predicate,
                    "object_id": rel.object_id,
                    "description": rel.description,
                }
                for rel in executor.plan.relations
            ],
            "nav_goal_state": executor._nav_goal_state,
            "observe_state": executor._observe_state,
            "track_state": executor._track_state,
            "candidate_actions": [
                "continue",
                "retry_same_stage",
                "hold_and_reobserve",
                "switch_cue_priority",
                "insert_verify_stage",
                "fallback_to_search",
                "safe_terminate",
            ],
        }
