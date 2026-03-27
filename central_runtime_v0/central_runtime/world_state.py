from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .infer_reader import Detection, ProposalCandidate
from .verification import VerificationStatus


@dataclass
class EvidenceRecord:
    t_wall: float
    source: str
    detection: Detection


@dataclass
class EntityState:
    entity_id: str
    last_detection: Optional[Detection] = None
    last_primary_detection: Optional[Detection] = None
    last_cue_detection: Optional[Detection] = None
    verified: bool = False
    verification_status: str = VerificationStatus.UNKNOWN
    evidence_count: int = 0
    stable_hit_streak: int = 0
    last_seen_t: Optional[float] = None
    last_primary_req_id: Optional[int] = None
    last_stage_id: Optional[str] = None
    cue_support_score: float = 0.0
    relation_support: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    recent_evidence: List[EvidenceRecord] = field(default_factory=list)
    proposal_status: Optional[str] = None
    topk_candidates: List[ProposalCandidate] = field(default_factory=list)
    proposal_uncertainty: Dict[str, Any] = field(default_factory=dict)
    spatial_hint: Dict[str, Any] = field(default_factory=dict)
    last_mask_quality: Optional[float] = None
    semantic_verify_status: str = VerificationStatus.UNKNOWN
    semantic_verify_confidence: float = 0.0
    semantic_explanation: str = ""
    semantic_failure_hypothesis: Optional[str] = None
    semantic_need_additional_view: bool = False
    semantic_recommended_followup: Optional[str] = None
    semantic_supports_relation: Dict[str, str] = field(default_factory=dict)
    last_semantic_verify_t: Optional[float] = None
    target_position_body: Optional[List[float]] = None
    target_position_world: Optional[List[float]] = None
    localization_confidence: float = 0.0
    depth_valid_ratio: float = 0.0
    support_pixels: int = 0
    localization_failure_reason: Optional[str] = None
    last_localization_t: Optional[float] = None


@dataclass
class StageRuntimeState:
    stage_id: str
    intent: str
    enter_t: float
    elapsed_s: float = 0.0
    milestones: Dict[str, float] = field(default_factory=dict)
    verification_status: str = VerificationStatus.UNKNOWN
    current_skill_id: Optional[str] = None
    current_skill_status: str = "idle"
    current_diag: Optional[str] = None
    retry_count: int = 0
    last_reasoner_t: Optional[float] = None
    last_recovery_action: Optional[str] = None
    pending_verify: bool = False
    last_semantic_verify_t: Optional[float] = None
    last_semantic_followup: Optional[str] = None
    verify_window_until: Optional[float] = None
    verify_window_reason: Optional[str] = None


class WorldState:
    def __init__(
        self,
        *,
        obs_ttl_s: float = 1.0,
        cue_ttl_s: float = 2.0,
        max_recent_evidence: int = 20,
    ):
        self.obs_ttl_s = float(obs_ttl_s)
        self.cue_ttl_s = float(cue_ttl_s)
        self.max_recent_evidence = int(max(1, max_recent_evidence))
        self.entities: Dict[str, EntityState] = {}
        self.stages: Dict[str, StageRuntimeState] = {}

    def ensure_entity(self, entity_id: str) -> EntityState:
        if entity_id not in self.entities:
            self.entities[entity_id] = EntityState(entity_id=entity_id)
        return self.entities[entity_id]

    def begin_stage(self, stage_id: str, intent: str, enter_t: float) -> StageRuntimeState:
        st = StageRuntimeState(stage_id=stage_id, intent=intent, enter_t=float(enter_t))
        self.stages[stage_id] = st
        return st

    def get_stage(self, stage_id: str) -> Optional[StageRuntimeState]:
        return self.stages.get(stage_id)

    def step(self, now: float) -> None:
        now = float(now)
        for st in self.stages.values():
            st.elapsed_s = max(0.0, now - st.enter_t)
        for ent in self.entities.values():
            last_cue = ent.last_cue_detection
            if last_cue is not None:
                age = max(0.0, now - float(last_cue.t_wall))
                if age > self.cue_ttl_s:
                    ent.cue_support_score = 0.0

    def ingest_detection(
        self,
        detection: Detection,
        *,
        default_entity_id: Optional[str] = None,
        source: str = "primary",
    ) -> Optional[EntityState]:
        entity_id = detection.entity_id or default_entity_id
        if not entity_id:
            return None
        ent = self.ensure_entity(entity_id)
        ent.last_detection = detection
        ent.last_stage_id = detection.stage_id or ent.last_stage_id
        ent.proposal_status = detection.proposal_status or ent.proposal_status
        ent.topk_candidates = list(detection.topk_candidates or [])
        ent.proposal_uncertainty = dict(detection.proposal_uncertainty or {})
        ent.spatial_hint = dict(detection.spatial_hint or {})
        ent.last_mask_quality = detection.mask_quality if detection.mask_quality is not None else ent.last_mask_quality

        if source == "cue":
            ent.last_cue_detection = detection
            if detection.found:
                ent.cue_support_score = max(ent.cue_support_score, float(detection.score))
        else:
            ent.last_primary_detection = detection
            ent.last_primary_req_id = detection.req_id if detection.req_id is not None else ent.last_primary_req_id
            if detection.found:
                ent.evidence_count += 1
                ent.last_seen_t = float(detection.t_wall)

        ent.recent_evidence.append(EvidenceRecord(t_wall=float(detection.t_wall), source=source, detection=detection))
        if len(ent.recent_evidence) > self.max_recent_evidence:
            ent.recent_evidence = ent.recent_evidence[-self.max_recent_evidence :]
        return ent

    def set_entity_verification(
        self,
        entity_id: str,
        status: str,
        *,
        note: Optional[str] = None,
    ) -> EntityState:
        ent = self.ensure_entity(entity_id)
        ent.verification_status = status
        ent.verified = status == VerificationStatus.VERIFIED
        if note:
            ent.notes.append(note)
            ent.notes = ent.notes[-10:]
        return ent

    def set_stage_verification(self, stage_id: str, status: str) -> Optional[StageRuntimeState]:
        st = self.stages.get(stage_id)
        if st is not None:
            st.verification_status = status
        return st

    def set_stage_skill_status(self, stage_id: str, status: str) -> Optional[StageRuntimeState]:
        st = self.stages.get(stage_id)
        if st is not None:
            st.current_skill_status = status
        return st

    def set_stage_skill(self, stage_id: str, skill_id: Optional[str], status: Optional[str] = None) -> Optional[StageRuntimeState]:
        st = self.stages.get(stage_id)
        if st is not None:
            st.current_skill_id = skill_id
            if status is not None:
                st.current_skill_status = status
        return st

    def set_stage_diag(self, stage_id: str, diag_code: Optional[str]) -> Optional[StageRuntimeState]:
        st = self.stages.get(stage_id)
        if st is not None:
            st.current_diag = diag_code
        return st

    def set_entity_semantic_verification(
        self,
        entity_id: str,
        *,
        status: str,
        confidence: float = 0.0,
        explanation: str = "",
        failure_hypothesis: Optional[str] = None,
        need_additional_view: bool = False,
        recommended_followup: Optional[str] = None,
        supports_relation: Optional[Dict[str, str]] = None,
        t_wall: Optional[float] = None,
    ) -> EntityState:
        ent = self.ensure_entity(entity_id)
        ent.semantic_verify_status = status
        ent.semantic_verify_confidence = float(confidence)
        ent.semantic_explanation = explanation or ""
        ent.semantic_failure_hypothesis = failure_hypothesis
        ent.semantic_need_additional_view = bool(need_additional_view)
        ent.semantic_recommended_followup = recommended_followup
        ent.semantic_supports_relation = dict(supports_relation or {})
        ent.last_semantic_verify_t = None if t_wall is None else float(t_wall)
        return ent

    def set_stage_semantic_state(
        self,
        stage_id: str,
        *,
        pending_verify: Optional[bool] = None,
        last_semantic_verify_t: Optional[float] = None,
        last_semantic_followup: Optional[str] = None,
        verify_window_until: Optional[float] = None,
        verify_window_reason: Optional[str] = None,
    ) -> Optional[StageRuntimeState]:
        st = self.stages.get(stage_id)
        if st is None:
            return None
        if pending_verify is not None:
            st.pending_verify = bool(pending_verify)
        if last_semantic_verify_t is not None:
            st.last_semantic_verify_t = float(last_semantic_verify_t)
        if last_semantic_followup is not None:
            st.last_semantic_followup = last_semantic_followup
        if verify_window_until is not None:
            st.verify_window_until = float(verify_window_until)
        if verify_window_reason is not None:
            st.verify_window_reason = verify_window_reason
        return st

    def set_entity_localization(
        self,
        entity_id: str,
        *,
        target_position_body: Optional[List[float]] = None,
        target_position_world: Optional[List[float]] = None,
        localization_confidence: float = 0.0,
        depth_valid_ratio: float = 0.0,
        support_pixels: int = 0,
        failure_reason: Optional[str] = None,
        t_wall: Optional[float] = None,
    ) -> EntityState:
        ent = self.ensure_entity(entity_id)
        ent.target_position_body = list(target_position_body) if target_position_body is not None else None
        ent.target_position_world = list(target_position_world) if target_position_world is not None else None
        ent.localization_confidence = float(localization_confidence)
        ent.depth_valid_ratio = float(depth_valid_ratio)
        ent.support_pixels = int(support_pixels)
        ent.localization_failure_reason = failure_reason
        ent.last_localization_t = None if t_wall is None else float(t_wall)
        return ent
