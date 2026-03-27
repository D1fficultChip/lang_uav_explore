from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from .adapters.base import Adapter
from .blackboard import Blackboard
from .conditions import ConditionEngine
from .diagnostics import DiagnosticCode, DiagnosticsEngine
from .infer_reader import Detection, InferJsonReader
from .localization_reader import TargetLocalization, TargetLocalizationReader
from .plan_loader import Plan, Transition
from .progress import ProgressTracker
from .prompt_controller import PromptController
from .recovery import RecoveryAction, RecoveryManager
from .reasoner.base import BaseRuntimeReasoner, ReasonerResponse
from .reasoner.guard import GuardDecision, ReasonerGuard
from .reasoner.noop import NoopRuntimeReasoner
from .reasoner.state_summarizer import StateSummarizer
from .runtime_events import RuntimeEventLogger
from .semantic_verifier import BaseSemanticVerifier, NoopSemanticVerifier, SemanticVerifyRequest, SemanticVerifyResponse
from .skills.registry import SkillRegistry
from .verification import VerificationResult, VerificationStatus
from .verifiers.base import BaseVerifier
from .world_state import StageRuntimeState, WorldState


def _norm_prompt(s: str) -> str:
    return (s or "").strip().lower()


def _atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _atomic_write_lines(path: str, lines: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln.rstrip("\n") + "\n")
    os.replace(tmp, path)


def _yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _quat_from_yaw(yaw: float) -> Tuple[float, float, float, float]:
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def _dist3(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class PlanExecutor:
    """
    V1 verified runtime:
      - reads primary + cue observations
      - maintains a world state in parallel with the lightweight blackboard
      - runs verifier modules before transition checks
      - evaluates stage success/failure criteria explicitly
      - keeps milestone / diagnostics / recovery summaries in runtime logs
    """

    def __init__(
        self,
        plan: Plan,
        infer_reader: InferJsonReader,
        prompt_ctl: PromptController,
        adapters: Dict[str, Adapter],
        *,
        cue_reader: Optional[InferJsonReader] = None,
        localization_reader: Optional[TargetLocalizationReader] = None,
        world_state: Optional[WorldState] = None,
        progress: Optional[ProgressTracker] = None,
        diagnostics: Optional[DiagnosticsEngine] = None,
        recovery: Optional[RecoveryManager] = None,
        event_logger: Optional[RuntimeEventLogger] = None,
        skill_registry: Optional[SkillRegistry] = None,
        verifiers: Optional[List[BaseVerifier]] = None,
        reasoner: Optional[BaseRuntimeReasoner] = None,
        reasoner_guard: Optional[ReasonerGuard] = None,
        semantic_verifier: Optional[BaseSemanticVerifier] = None,
        summarizer: Optional[StateSummarizer] = None,
        tick_hz: float = 10.0,
        log_jsonl_path: Optional[str] = None,
        verified_cfg: Optional[Dict[str, Any]] = None,
        verification_cfg: Optional[Dict[str, Any]] = None,
        reasoner_cfg: Optional[Dict[str, Any]] = None,
        semantic_cfg: Optional[Dict[str, Any]] = None,
        stage_settle_s: float = 0.0,
        frame_path: Optional[str] = None,
        perception_request_path: Optional[str] = None,
        emit_cues_txt: bool = False,
        cues_txt_path: Optional[str] = None,
        entity_goals: Optional[Dict[str, Any]] = None,
        ego_goal_topic: str = "/move_base_simple/goal",
        mux_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.plan = plan
        self.infer_reader = infer_reader
        self.cue_reader = cue_reader
        self.localization_reader = localization_reader
        self.prompt_ctl = prompt_ctl
        self.adapters = adapters

        self.tick_dt = 1.0 / max(1e-6, float(tick_hz))
        self.bb = Blackboard()
        self.world_state = world_state or WorldState()
        self.progress = progress or ProgressTracker()
        self.diagnostics = diagnostics or DiagnosticsEngine()
        self.recovery = recovery or RecoveryManager()
        self.event_logger = event_logger or RuntimeEventLogger(None)
        self.skill_registry = skill_registry or SkillRegistry()
        self.verifiers = verifiers or []
        self.reasoner = reasoner or NoopRuntimeReasoner()
        self.reasoner_guard = reasoner_guard or ReasonerGuard(allow_actions=["continue"])
        self.semantic_verifier = semantic_verifier or NoopSemanticVerifier()
        self.summarizer = summarizer or StateSummarizer()

        self.verified_cfg = verified_cfg or {}
        self.verification_cfg = verification_cfg or {}
        self.reasoner_cfg = reasoner_cfg or {}
        self.semantic_cfg = semantic_cfg or {}
        self.cond = ConditionEngine(
            self.bb,
            verified_cfg=self.verified_cfg,
            verification_lookup=self._is_entity_verified,
            cue_strength_lookup=self._cue_strength,
            mission_elapsed_lookup=self._mission_elapsed,
        )

        self.stage_settle_s = float(stage_settle_s)
        self.frame_path = frame_path
        self.perception_request_path = perception_request_path
        self.emit_cues_txt = bool(emit_cues_txt)
        self.cues_txt_path = cues_txt_path

        self.entity_goals = entity_goals or {}
        self.ego_goal_topic = ego_goal_topic

        mux_cfg = mux_cfg or {}
        self.mux_select_service = mux_cfg.get("select_service", "/mux/select")
        self.mux_selected_topic = mux_cfg.get("selected_topic", "/mux/selected")
        self.hold_topic = mux_cfg.get("hold_topic", "/hold/pos_cmd")
        self.pre_switch_hover_s = float(mux_cfg.get("pre_switch_hover_s", 0.3))
        self.odom_topic = mux_cfg.get("odom_topic", "/uav_simulator/odometry")
        self.odom_wait_timeout_s = float(mux_cfg.get("odom_wait_timeout_s", 2.0))
        self.intent_source = mux_cfg.get("intent_source", {}) or {}

        self.nav_progress_check_period_s = float(self.verification_cfg.get("nav_progress_check_period_s", 1.0))
        self.nav_goal_reached_tol_m = float(self.verification_cfg.get("nav_goal_reached_tol_m", 0.8))
        self.min_motion_progress_m = float(self.verification_cfg.get("min_motion_progress_m", 0.3))
        self.nav_stall_timeout_s = float(self.verification_cfg.get("nav_stall_timeout_s", 5.0))
        self.reasoner_enabled = bool(self.reasoner_cfg.get("enabled", False))
        self.reasoner_min_stage_dwell_s = float(self.reasoner_cfg.get("min_stage_dwell_s", 2.0))
        self.reasoner_cooldown_s = float(self.reasoner_cfg.get("cooldown_s", 3.0))
        self.reasoner_trigger_on_inconclusive = bool(self.reasoner_cfg.get("trigger_on_inconclusive", True))
        self.reasoner_auto_rewrite_request = bool(self.reasoner_cfg.get("auto_rewrite_request", True))
        self.verify_window_s = float(self.reasoner_cfg.get("verify_window_s", 2.0))
        self.semantic_enabled = bool(self.semantic_cfg.get("enabled", False))
        self.semantic_min_stage_dwell_s = float(self.semantic_cfg.get("min_stage_dwell_s", 1.5))
        self.semantic_cooldown_s = float(self.semantic_cfg.get("cooldown_s", 4.0))
        self.semantic_trigger_on_ambiguous = bool(self.semantic_cfg.get("trigger_on_ambiguous", True))
        self.semantic_trigger_on_inconclusive = bool(self.semantic_cfg.get("trigger_on_inconclusive", True))
        self.semantic_trigger_on_relations = bool(self.semantic_cfg.get("trigger_on_relations", True))
        self.semantic_trigger_on_failure = bool(self.semantic_cfg.get("trigger_on_failure", False))
        self.semantic_auto_apply_followup = bool(self.semantic_cfg.get("auto_apply_followup", True))
        self.semantic_allow_followups = set(self.semantic_cfg.get("allow_followups", []))

        self.stage_id = self._pick_start_stage()
        self.mission_start_t = time.time()
        self.stage_enter_t = time.time()
        self.active_entity_id = self.plan.stages[self.stage_id].primary_targets[0]

        self._req_id_counter = 0
        self.current_req_id: Optional[int] = None
        self.expected_primary_prompt = ""

        self._log_fp = open(log_jsonl_path, "a", encoding="utf-8") if log_jsonl_path else None
        self._stop_requested = False
        self._terminal_reason: Optional[str] = None
        self._verification_results: Dict[str, VerificationResult] = {}
        self._current_recovery: Optional[RecoveryAction] = None
        self._stage_retry_counts: Dict[str, int] = {}
        self._nav_goal_state: Optional[Dict[str, Any]] = None
        self._last_nav_progress_check_t = 0.0
        self._cue_priority_override: Dict[str, List[str]] = {}
        self._last_reasoner_response: Optional[ReasonerResponse] = None
        self._last_guard_decision: Optional[GuardDecision] = None
        self._last_semantic_response: Optional[SemanticVerifyResponse] = None
        self._last_localization: Optional[TargetLocalization] = None
        self._observe_state: Optional[Dict[str, Any]] = None
        self._track_state: Optional[Dict[str, Any]] = None

    def close(self):
        if self._log_fp:
            self._log_fp.close()
        self.event_logger.close()

    def _pick_start_stage(self) -> str:
        incoming = {t.to for t in self.plan.transitions}
        for sid in self.plan.stages.keys():
            if sid not in incoming:
                return sid
        return list(self.plan.stages.keys())[0]

    def _log(self, rec: Dict[str, Any]) -> None:
        if not self._log_fp:
            return
        self._log_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._log_fp.flush()

    def _request_stop(self, reason: str) -> None:
        if self._stop_requested:
            return
        self._stop_requested = True
        self._terminal_reason = reason
        self._emit_runtime_event("mission_stop_requested", now=time.time(), details={"reason": reason})

    def _get_stage_state(self) -> Optional[StageRuntimeState]:
        return self.world_state.get_stage(self.stage_id)

    def _is_entity_verified(self, entity_id: str) -> bool:
        ent = self.world_state.entities.get(entity_id)
        return ent is not None and ent.verification_status == VerificationStatus.VERIFIED

    def _cue_strength(self, entity_id: str) -> float:
        ent = self.world_state.entities.get(entity_id)
        if ent is None:
            return 0.0
        return float(ent.cue_support_score)

    def _mission_elapsed(self) -> float:
        return max(0.0, time.time() - self.mission_start_t)

    def _entity_semantic_target_confidence(self, entity_id: str) -> float:
        ent = self.world_state.entities.get(entity_id)
        if ent is None:
            return 0.0

        confs: List[float] = []
        if ent.last_primary_detection is not None and ent.last_primary_detection.found:
            confs.append(max(0.0, min(1.0, float(ent.last_primary_detection.score))))

        semantic_status = str(ent.semantic_verify_status or "").strip().lower()
        semantic_conf = max(0.0, min(1.0, float(ent.semantic_verify_confidence or 0.0)))
        semantic_factor = {
            "supported": 1.0,
            "weakly_supported": 0.75,
            "inconclusive": 0.45,
            "not_supported": 0.1,
        }.get(semantic_status, 0.0)
        if semantic_conf > 1e-6 and semantic_factor > 0.0:
            confs.append(semantic_conf * semantic_factor)

        if ent.proposal_status == "stable_primary_candidate":
            confs.append(0.75)
        elif ent.proposal_status == "single_candidate":
            confs.append(0.6)
        elif ent.proposal_status == "multi_candidate_ambiguous":
            confs.append(0.35)

        if ent.cue_support_score > 1e-6:
            confs.append(max(0.0, min(1.0, 0.7 * float(ent.cue_support_score))))

        if not confs:
            return 0.0
        return max(0.0, min(1.0, max(confs)))

    def _entity_bearing_prior(self, entity_id: str, *, confidence_hint: Optional[float] = None) -> Optional[Dict[str, Any]]:
        ent = self.world_state.entities.get(entity_id)
        if ent is None:
            return None
        spatial_hint = ent.spatial_hint or {}
        center_xy = spatial_hint.get("center_xy_norm")
        if not (isinstance(center_xy, list) and len(center_xy) >= 2):
            return None
        try:
            center_x = float(center_xy[0])
        except Exception:
            return None
        area_ratio = spatial_hint.get("box_area_ratio")
        try:
            area_ratio = float(area_ratio) if area_ratio is not None else 0.0
        except Exception:
            area_ratio = 0.0
        area_ratio = max(0.0, min(1.0, area_ratio))

        confidence = confidence_hint
        if confidence is None:
            confidence = self._entity_semantic_target_confidence(entity_id)
        confidence = max(0.0, min(1.0, float(confidence or 0.0)))

        width_norm = max(0.12, min(0.55, 0.38 - 0.22 * area_ratio))
        return {
            "entity_id": entity_id,
            "source": "primary" if ent.last_primary_detection is not None else "cue",
            "center_x_norm": max(0.0, min(1.0, center_x)),
            "width_x_norm": width_norm,
            "confidence": confidence,
            "image_region": spatial_hint.get("image_region"),
        }

    def _semantic_bearing_prior(self, stage_id: str, primary_entity_id: str, target_confidence: float) -> Optional[Dict[str, Any]]:
        primary_prior = self._entity_bearing_prior(primary_entity_id, confidence_hint=target_confidence)
        best_prior = primary_prior
        best_conf = 0.0 if primary_prior is None else float(primary_prior.get("confidence", 0.0) or 0.0)

        st = self.plan.stages[stage_id]
        for cue_id in st.cue_targets:
            cue_ent = self.world_state.entities.get(cue_id)
            if cue_ent is None:
                continue
            cue_conf = max(
                0.0,
                min(
                    1.0,
                    max(
                        float(cue_ent.cue_support_score or 0.0),
                        self._entity_semantic_target_confidence(cue_id),
                    ),
                ),
            )
            cue_prior = self._entity_bearing_prior(cue_id, confidence_hint=cue_conf)
            if cue_prior is None:
                continue
            if cue_conf > best_conf + 1e-6:
                best_prior = cue_prior
                best_conf = cue_conf
        return best_prior

    def _semantic_urgency(self, stage_id: str, primary_entity_id: str, mode: str) -> float:
        st = self.plan.stages[stage_id]
        policy = st.policy or {}
        raw_override = policy.get("semantic_urgency")
        if raw_override is not None:
            try:
                return max(0.0, min(1.0, float(raw_override)))
            except Exception:
                pass

        if mode in ("normal", "off"):
            return 0.0

        ent = self.world_state.entities.get(primary_entity_id)
        urgency = 0.45 if mode == "bias" else 0.82
        if ent is not None:
            if ent.proposal_status == "multi_candidate_ambiguous":
                urgency += 0.12
            if ent.semantic_need_additional_view:
                urgency += 0.18
            if (ent.semantic_recommended_followup or "") in {"insert_verify_stage", "switch_cue_priority", "hold_and_reobserve"}:
                urgency += 0.08
            urgency += 0.12 * max(0.0, min(1.0, float(ent.cue_support_score or 0.0)))

        latest_diag = self.diagnostics.latest(stage_id)
        if latest_diag is not None:
            urgency += 0.08
        if st.intent == "SEARCH" and self.plan.stages[stage_id].cue_targets:
            urgency += 0.05
        return max(0.0, min(1.0, urgency))

    def _semantic_exploration_extra(self, stage_id: str) -> Dict[str, Any]:
        st = self.plan.stages[stage_id]
        policy = st.policy or {}
        primary_entity_id = st.primary_targets[0]
        mode = str(policy.get("semantic_mode", "") or "").strip().lower()
        if not mode:
            if st.intent == "SEARCH" and st.cue_targets:
                mode = "bias"
            else:
                mode = "normal"
        if mode not in ("normal", "bias", "focus", "off"):
            mode = "bias" if (st.intent == "SEARCH" and st.cue_targets) else "normal"
        strength = policy.get("semantic_strength", None)
        try:
            strength_val = float(strength) if strength is not None else None
        except Exception:
            strength_val = None
        if strength_val is None:
            if mode == "focus":
                strength_val = 1.25
            elif mode == "bias":
                strength_val = 0.85
            else:
                strength_val = 0.0
        target_confidence = self._entity_semantic_target_confidence(primary_entity_id)
        raw_target_conf = policy.get("target_confidence")
        if raw_target_conf is not None:
            try:
                target_confidence = max(0.0, min(1.0, float(raw_target_conf)))
            except Exception:
                pass

        semantic_urgency = self._semantic_urgency(stage_id, primary_entity_id, mode)
        bearing_prior = self._semantic_bearing_prior(stage_id, primary_entity_id, target_confidence)
        if isinstance(policy.get("bearing_prior"), dict):
            bearing_prior = dict(policy.get("bearing_prior") or {})
        return {
            "semantic_mode": mode,
            "semantic_strength": strength_val,
            "semantic_active": bool(mode not in ("normal", "off")),
            "target_confidence": target_confidence,
            "semantic_urgency": semantic_urgency,
            "bearing_prior": bearing_prior,
        }

    def _emit_runtime_event(self, event_type: str, *, now: Optional[float] = None, details: Optional[Dict[str, Any]] = None) -> None:
        stage = self.plan.stages.get(self.stage_id)
        self.event_logger.emit(
            event_type=event_type,
            t_wall=time.time() if now is None else now,
            stage_id=self.stage_id,
            intent=None if stage is None else stage.intent,
            entity_id=self.active_entity_id,
            details=details,
        )

    def _stage_skill_contract(self, stage_id: str):
        st = self.plan.stages[stage_id]
        return self.skill_registry.get_by_intent(st.intent)

    def _stage_skill_id(self, stage_id: str) -> Optional[str]:
        contract = self._stage_skill_contract(stage_id)
        return None if contract is None else contract.skill_id

    def _start_verify_window(self, now: float, *, reason: str, source: str) -> bool:
        stage_state = self._get_stage_state()
        if stage_state is None:
            return False
        until = now + max(0.1, self.verify_window_s)
        already_active = stage_state.verify_window_until is not None and stage_state.verify_window_until > now
        if already_active and float(stage_state.verify_window_until) >= until:
            return False
        self.world_state.set_stage_semantic_state(
            self.stage_id,
            pending_verify=True,
            verify_window_until=until,
            verify_window_reason=reason,
        )
        self._record_milestone("verify_window_started", now, {"reason": reason, "source": source, "until": until})
        self._emit_runtime_event(
            "verify_window_started",
            now=now,
            details={"reason": reason, "source": source, "until": until},
        )
        if self.hold_topic:
            self._mux_select(self.hold_topic)
            self._publish_hold_for(min(self.recovery.hold_reobserve_s, self.verify_window_s))
        return True

    def _update_verify_window(self, now: float) -> bool:
        stage_state = self._get_stage_state()
        if stage_state is None or stage_state.verify_window_until is None:
            return False
        if now < float(stage_state.verify_window_until):
            return True
        reason = stage_state.verify_window_reason
        self.world_state.set_stage_semantic_state(
            self.stage_id,
            pending_verify=False,
            verify_window_until=0.0,
            verify_window_reason="",
        )
        stage_state.verify_window_until = None
        stage_state.verify_window_reason = None
        self._record_milestone("verify_window_finished", now, {"reason": reason})
        self._emit_runtime_event("verify_window_finished", now=now, details={"reason": reason})
        return False

    def _record_milestone(self, name: str, now: float, details: Optional[Dict[str, Any]] = None) -> bool:
        created = self.progress.mark(self.stage_id, name, t_wall=now, details=details)
        st = self._get_stage_state()
        if created and st is not None:
            st.milestones[name] = float(now)
        return created

    def _emit_diag(self, *, code: str, message: str, now: float, severity: str = "warn", details: Optional[Dict[str, Any]] = None) -> None:
        st = self._get_stage_state()
        current_code = None if st is None else st.current_diag
        if current_code == code:
            return
        self.diagnostics.emit(
            code=code,
            severity=severity,
            message=message,
            t_wall=now,
            stage_id=self.stage_id,
            details=details,
        )
        self.world_state.set_stage_diag(self.stage_id, code)

    def _clear_stage_diag(self) -> None:
        self.world_state.set_stage_diag(self.stage_id, None)

    def _ordered_cue_targets(self, stage_id: str) -> List[str]:
        st = self.plan.stages[stage_id]
        override = self._cue_priority_override.get(stage_id)
        if not override:
            return list(st.cue_targets)
        ordered = [eid for eid in override if eid in st.cue_targets]
        tail = [eid for eid in st.cue_targets if eid not in ordered]
        return ordered + tail

    # ---------------- mux / hover helpers ----------------
    def _mux_select(self, topic: str) -> bool:
        topic = str(topic)
        try:
            p = subprocess.run(
                ["rosservice", "call", self.mux_select_service, topic],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if p.returncode != 0:
                print(f"[MUX][WARN] select failed: {p.stderr.strip()}")
                return False
            return True
        except FileNotFoundError:
            print("[MUX][WARN] rosservice not found. Did you source ROS on host?")
            return False
        except Exception as e:
            print(f"[MUX][WARN] select exception: {e}")
            return False

    def _read_odom_pose_once(self) -> Optional[Tuple[float, float, float, float]]:
        try:
            out = subprocess.check_output(
                ["rostopic", "echo", "-n", "1", self.odom_topic],
                stderr=subprocess.STDOUT,
                text=True,
                timeout=2.0,
            )
        except Exception as e:
            print(f"[HOLD][WARN] cannot read odom: {e}")
            return None

        mpos = re.search(
            r"position:\s*\n\s*x:\s*([-+0-9.eE]+)\s*\n\s*y:\s*([-+0-9.eE]+)\s*\n\s*z:\s*([-+0-9.eE]+)",
            out,
        )
        if not mpos:
            return None
        px = float(mpos.group(1))
        py = float(mpos.group(2))
        pz = float(mpos.group(3))

        mori = re.search(
            r"orientation:\s*\n\s*x:\s*([-+0-9.eE]+)\s*\n\s*y:\s*([-+0-9.eE]+)\s*\n\s*z:\s*([-+0-9.eE]+)\s*\n\s*w:\s*([-+0-9.eE]+)",
            out,
        )
        if not mori:
            yaw = 0.0
        else:
            yaw = _yaw_from_quat(float(mori.group(1)), float(mori.group(2)), float(mori.group(3)), float(mori.group(4)))
        return (px, py, pz, yaw)

    def _publish_hold_for(self, seconds: float) -> None:
        pose = self._read_odom_pose_once()
        if pose is None:
            return
        x, y, z, yaw = pose
        t_end = time.time() + max(0.0, seconds)
        while time.time() < t_end:
            msg = (
                "{header: {stamp: now, frame_id: 'world'}, "
                f"position: {{x: {x}, y: {y}, z: {z}}}, "
                "velocity: {x: 0.0, y: 0.0, z: 0.0}, "
                "acceleration: {x: 0.0, y: 0.0, z: 0.0}, "
                "jerk: {x: 0.0, y: 0.0, z: 0.0}, "
                f"yaw: {yaw}, yaw_dot: 0.0, "
                "kx: [0.0, 0.0, 0.0], kv: [0.0, 0.0, 0.0], "
                "trajectory_id: 0, trajectory_flag: 0}"
            )
            try:
                subprocess.run(
                    ["rostopic", "pub", "-1", self.hold_topic, "quadrotor_msgs/PositionCommand", msg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except Exception:
                pass
            time.sleep(0.05)

    def _select_intent_source(self, intent: str) -> None:
        src = self.intent_source.get(intent)
        if not src and intent == "OBSERVE":
            src = self.intent_source.get("NAVIGATE")
        if src:
            self._mux_select(src)

    # ---------------- NAVIGATE helpers ----------------
    def _resolve_goal_for_entity(self, eid: str) -> Optional[Dict[str, Any]]:
        g = self.entity_goals.get(eid)
        if isinstance(g, dict):
            return g
        ent = self.plan.entities.get(eid)
        if ent and isinstance(ent.extra, dict):
            gg = ent.extra.get("goal")
            if isinstance(gg, dict):
                return gg
        return None

    def _pub_goal_pose_stamped(
        self,
        goal: Dict[str, Any],
        topic: str,
        retries: int = 5,
        interval_s: float = 3.0,
        settle_s: float = 0.0,
        check_topic: bool = True,
    ) -> bool:
        frame = str(goal.get("frame", "world"))
        x = float(goal.get("x", 0.0))
        y = float(goal.get("y", 0.0))
        z = float(goal.get("z", 1.0))
        yaw_deg = float(goal.get("yaw_deg", 0.0))
        yaw = math.radians(yaw_deg)
        qx, qy, qz, qw = _quat_from_yaw(yaw)

        msg = (
            '{header: {frame_id: "' + frame + '"}, '
            'pose: {position: {x: ' + str(x) + ', y: ' + str(y) + ', z: ' + str(z) + '}, '
            'orientation: {x: ' + str(qx) + ', y: ' + str(qy) + ', z: ' + str(qz) + ', w: ' + str(qw) + '}}}'
        )
        base_prefix = "source /opt/ros/noetic/setup.bash >/dev/null 2>&1 || true; "

        def _run(cmd: str) -> Tuple[int, str, str]:
            p = subprocess.run(["bash", "-lc", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return p.returncode, p.stdout, p.stderr

        if check_topic:
            rc, _, _ = _run(base_prefix + f"rostopic info {topic} >/dev/null 2>&1")
            if rc != 0:
                print(f"[NAVIGATE][WARN] rostopic info failed for {topic}, will still retry publish")

        if settle_s > 0:
            time.sleep(settle_s)

        ok_any = False
        pub_cmd = base_prefix + f"rostopic pub -1 {topic} geometry_msgs/PoseStamped '{msg}'"
        for i in range(max(1, int(retries))):
            try:
                rc, _, err = _run(pub_cmd)
                if rc == 0:
                    ok_any = True
                    print(f"[NAVIGATE] published PoseStamped to {topic} attempt {i + 1}/{retries}")
                else:
                    print(f"[NAVIGATE][WARN] publish PoseStamped failed to {topic} attempt {i + 1}/{retries}: {err.strip()}")
            except Exception as e:
                print(f"[NAVIGATE][WARN] publish PoseStamped exception to {topic} attempt {i + 1}/{retries}: {e}")
            if i != retries - 1:
                time.sleep(interval_s)
        return ok_any

    def _set_navigation_goal_state(self, eid: str, goal: Dict[str, Any], now: float) -> None:
        self._nav_goal_state = {
            "entity_id": eid,
            "goal_xyz": (float(goal.get("x", 0.0)), float(goal.get("y", 0.0)), float(goal.get("z", 1.0))),
            "initial_distance": None,
            "last_distance": None,
            "goal_published_t": now,
        }
        self._last_nav_progress_check_t = 0.0

    def _update_navigation_progress(self, now: float) -> None:
        stage = self.plan.stages[self.stage_id]
        if stage.intent not in ("NAVIGATE", "OBSERVE", "TRACK") or not self._nav_goal_state:
            return
        if (now - self._last_nav_progress_check_t) < self.nav_progress_check_period_s:
            return
        self._last_nav_progress_check_t = now

        pose = self._read_odom_pose_once()
        if pose is None:
            return
        pos = (pose[0], pose[1], pose[2])
        goal_xyz = self._nav_goal_state["goal_xyz"]
        dist = _dist3(pos, goal_xyz)
        if self._nav_goal_state["initial_distance"] is None:
            self._nav_goal_state["initial_distance"] = dist
            self._nav_goal_state["last_distance"] = dist
            return

        initial_distance = float(self._nav_goal_state["initial_distance"])
        last_distance = float(self._nav_goal_state["last_distance"])
        self._nav_goal_state["last_distance"] = dist

        if dist <= self.nav_goal_reached_tol_m:
            self._record_milestone("goal_reached", now, {"distance_m": dist})
            return

        if (initial_distance - dist) >= self.min_motion_progress_m or (last_distance - dist) >= self.min_motion_progress_m:
            self._record_milestone("motion_confirmed", now, {"distance_m": dist})
        elif (now - float(self._nav_goal_state["goal_published_t"])) >= self.nav_stall_timeout_s:
            self._emit_diag(
                code=DiagnosticCode.NAV_STALLED,
                message="navigate goal published but no measurable progress yet",
                now=now,
                details={"distance_m": dist},
            )

    def _current_goal_reached(self) -> bool:
        if not self._nav_goal_state:
            return False
        pose = self._read_odom_pose_once()
        if pose is None:
            return False
        pos = (pose[0], pose[1], pose[2])
        goal_xyz = self._nav_goal_state["goal_xyz"]
        return _dist3(pos, goal_xyz) <= self.nav_goal_reached_tol_m

    def _publish_navigation_goal(self, eid: str, goal: Dict[str, Any], now: float, *, trigger: bool = True) -> bool:
        ok_goal = self._pub_goal_pose_stamped(
            goal,
            self.ego_goal_topic,
            retries=5,
            interval_s=3.0,
            settle_s=0.5,
            check_topic=False,
        )
        ok_trig = True
        if trigger:
            ok_trig = self._pub_goal_pose_stamped(
                goal,
                "/traj_start_trigger",
                retries=5,
                interval_s=3.0,
                settle_s=0.0,
                check_topic=False,
            )
        if ok_goal:
            self._record_milestone("goal_published", now, {"entity_id": eid, "goal": goal})
            self._set_navigation_goal_state(eid, goal, now)
        return bool(ok_goal and ok_trig)

    def _observe_policy(self, stage_id: str) -> Dict[str, Any]:
        st = self.plan.stages[stage_id]
        policy = dict(st.policy or {})
        return {
            "observe_radius_m": float(policy.get("observe_radius_m", 0.5)),
            "hover_duration_s": float(policy.get("hover_duration_s", 2.0)),
            "rollback_on_fail": bool(policy.get("rollback_on_fail", True)),
        }

    def _track_policy(self, stage_id: str) -> Dict[str, Any]:
        st = self.plan.stages[stage_id]
        policy = dict(st.policy or {})
        return {
            "track_standoff_m": float(policy.get("track_standoff_m", 0.5)),
            "goal_update_dist_th": float(policy.get("goal_update_dist_th", 0.3)),
            "goal_update_yaw_th_deg": float(policy.get("goal_update_yaw_th_deg", 20.0)),
            "target_lost_timeout_s": float(policy.get("target_lost_timeout_s", 2.0)),
            "min_localization_confidence": float(policy.get("min_localization_confidence", 0.35)),
            "localization_max_age_s": float(policy.get("localization_max_age_s", 0.7)),
            "rollback_on_fail": bool(policy.get("rollback_on_fail", False)),
        }

    def _goal_dict_from_pose(self, pose: Tuple[float, float, float, float], *, frame: str = "world") -> Dict[str, Any]:
        x, y, z, yaw = pose
        return {"frame": frame, "x": float(x), "y": float(y), "z": float(z), "yaw_deg": math.degrees(float(yaw))}

    def _build_observe_goal(self, eid: str, stage_id: str) -> Optional[Dict[str, Any]]:
        ent_state = self.world_state.entities.get(eid)
        if ent_state is None or ent_state.target_position_world is None or len(ent_state.target_position_world) < 3:
            return None
        pose = self._read_odom_pose_once()
        if pose is None:
            return None
        ux, uy, uz, uyaw = pose
        tx, ty, tz = [float(v) for v in ent_state.target_position_world[:3]]
        radius = self._observe_policy(stage_id)["observe_radius_m"]
        vx = ux - tx
        vy = uy - ty
        norm = math.hypot(vx, vy)
        if norm < 1e-3:
            vx = math.cos(uyaw)
            vy = math.sin(uyaw)
            norm = max(math.hypot(vx, vy), 1e-6)
        uxdir = vx / norm
        uydir = vy / norm
        gx = tx + radius * uxdir
        gy = ty + radius * uydir
        gz = uz
        yaw = math.atan2(ty - gy, tx - gx)
        return {"frame": "world", "x": gx, "y": gy, "z": gz, "yaw_deg": math.degrees(yaw)}

    def _build_track_goal(self, eid: str, stage_id: str) -> Optional[Dict[str, Any]]:
        ent_state = self.world_state.entities.get(eid)
        if ent_state is None or ent_state.target_position_world is None or len(ent_state.target_position_world) < 3:
            return None
        pose = self._read_odom_pose_once()
        if pose is None:
            return None
        ux, uy, uz, uyaw = pose
        tx, ty, tz = [float(v) for v in ent_state.target_position_world[:3]]
        radius = self._track_policy(stage_id)["track_standoff_m"]
        vx = ux - tx
        vy = uy - ty
        norm = math.hypot(vx, vy)
        if norm < 1e-3:
            vx = math.cos(uyaw)
            vy = math.sin(uyaw)
            norm = max(math.hypot(vx, vy), 1e-6)
        gx = tx + radius * (vx / norm)
        gy = ty + radius * (vy / norm)
        gz = uz
        yaw = math.atan2(ty - gy, tx - gx)
        return {"frame": "world", "x": gx, "y": gy, "z": gz, "yaw_deg": math.degrees(yaw)}

    def _goal_distance(self, a: Dict[str, Any], b: Dict[str, Any]) -> float:
        return _dist3(
            (float(a.get("x", 0.0)), float(a.get("y", 0.0)), float(a.get("z", 0.0))),
            (float(b.get("x", 0.0)), float(b.get("y", 0.0)), float(b.get("z", 0.0))),
        )

    def _goal_yaw_delta_deg(self, a: Dict[str, Any], b: Dict[str, Any]) -> float:
        yaw_a = float(a.get("yaw_deg", 0.0))
        yaw_b = float(b.get("yaw_deg", 0.0))
        d = (yaw_a - yaw_b + 180.0) % 360.0 - 180.0
        return abs(d)

    def _start_observe_stage(self, stage_id: str, eid: str, now: float) -> None:
        start_pose = self._read_odom_pose_once()
        if start_pose is None:
            self._observe_state = {"phase": "unavailable", "reason": "odom_unavailable", "started_t": now}
            self._emit_diag(code=DiagnosticCode.NAV_NO_GOAL, message="observe start odom unavailable", now=now, severity="error")
            return
        goal = self._build_observe_goal(eid, stage_id)
        if goal is None:
            self._observe_state = {"phase": "unavailable", "reason": "target_localization_unavailable", "started_t": now}
            self._emit_diag(code=DiagnosticCode.NAV_NO_GOAL, message=f"observe goal unavailable for {eid}", now=now, severity="error")
            return
        self._observe_state = {
            "phase": "approach",
            "started_t": now,
            "start_odom_goal": self._goal_dict_from_pose(start_pose),
            "observe_goal": goal,
            "hover_duration_s": self._observe_policy(stage_id)["hover_duration_s"],
            "rollback_on_fail": self._observe_policy(stage_id)["rollback_on_fail"],
            "fallback_stage_id": next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None),
            "hover_finished_t": None,
            "rollback_started_t": None,
        }
        ok = self._publish_navigation_goal(eid, goal, now, trigger=True)
        self._record_milestone("observe_started", now, {"goal": goal, "ok": ok})
        self._emit_runtime_event("observe_started", now=now, details={"goal": goal, "ok": ok})

    def _target_localization_available(self, eid: str, now: float, stage_id: str) -> bool:
        ent_state = self.world_state.entities.get(eid)
        if ent_state is None or ent_state.target_position_world is None:
            return False
        policy = self._track_policy(stage_id)
        if ent_state.localization_confidence < float(policy["min_localization_confidence"]):
            return False
        if ent_state.last_localization_t is None:
            return False
        return (now - float(ent_state.last_localization_t)) <= float(policy["localization_max_age_s"])

    def _start_track_stage(self, stage_id: str, eid: str, now: float) -> None:
        if not self._target_localization_available(eid, now, stage_id):
            self._track_state = {
                "phase": "unavailable",
                "reason": "target_localization_unavailable",
                "started_t": now,
                "fallback_stage_id": next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None),
            }
            self._emit_diag(code=DiagnosticCode.TRACK_TARGET_LOST, message=f"track localization unavailable for {eid}", now=now, severity="error")
            return
        goal = self._build_track_goal(eid, stage_id)
        if goal is None:
            self._track_state = {
                "phase": "unavailable",
                "reason": "track_goal_unavailable",
                "started_t": now,
                "fallback_stage_id": next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None),
            }
            self._emit_diag(code=DiagnosticCode.NAV_NO_GOAL, message=f"track goal unavailable for {eid}", now=now, severity="error")
            return
        self._track_state = {
            "phase": "tracking",
            "started_t": now,
            "last_goal": goal,
            "last_target_position_world": None if self.world_state.entities.get(eid) is None else list(self.world_state.entities[eid].target_position_world or []),
            "last_goal_update_t": now,
            "lost_since_t": None,
            "fallback_stage_id": next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None),
            "policy": self._track_policy(stage_id),
        }
        ok = self._publish_navigation_goal(eid, goal, now, trigger=True)
        self._record_milestone("track_started", now, {"goal": goal, "ok": ok})
        self._emit_runtime_event("track_started", now=now, details={"goal": goal, "ok": ok})

    def _drive_track_stage(self, now: float, criteria: Dict[str, Any]) -> bool:
        stage = self.plan.stages[self.stage_id]
        if stage.intent != "TRACK" or self._track_state is None:
            return False
        phase = self._track_state.get("phase")
        if criteria.get("success_met"):
            self._track_state["phase"] = "complete"
            self._record_milestone("track_completed", now, {})
            self._emit_runtime_event("track_completed", now=now, details={})
            return False
        if phase == "unavailable":
            return self._start_track_fallback(now, reason=str(self._track_state.get("reason", "unavailable")))

        policy = self._track_state.get("policy") or self._track_policy(self.stage_id)
        ent_state = self.world_state.entities.get(self.active_entity_id)
        loc_ok = self._target_localization_available(self.active_entity_id, now, self.stage_id)

        if loc_ok and ent_state is not None and ent_state.target_position_world is not None:
            if phase in ("temporarily_lost", "reacquiring"):
                self._track_state["phase"] = "tracking"
                self._track_state["lost_since_t"] = None
                self._record_milestone("track_recovered", now, {})
                self._emit_runtime_event("track_recovered", now=now, details={})

            goal = self._build_track_goal(self.active_entity_id, self.stage_id)
            last_goal = self._track_state.get("last_goal")
            should_update = goal is not None and (
                last_goal is None
                or self._goal_distance(goal, last_goal) >= float(policy["goal_update_dist_th"])
                or self._goal_yaw_delta_deg(goal, last_goal) >= float(policy["goal_update_yaw_th_deg"])
            )
            if should_update and goal is not None:
                ok = self._publish_navigation_goal(self.active_entity_id, goal, now, trigger=True)
                self._track_state["last_goal"] = goal
                self._track_state["last_goal_update_t"] = now
                self._track_state["last_target_position_world"] = list(ent_state.target_position_world)
                self._record_milestone("track_goal_updated", now, {"goal": goal, "ok": ok})
                self._emit_runtime_event("track_goal_updated", now=now, details={"goal": goal, "ok": ok})
            return True

        if self._track_state.get("lost_since_t") is None:
            self._track_state["lost_since_t"] = now
            self._track_state["phase"] = "temporarily_lost"
            self._record_milestone("track_temporarily_lost", now, {})
            self._emit_runtime_event("track_temporarily_lost", now=now, details={})
            self._emit_diag(code=DiagnosticCode.TRACK_TARGET_LOST, message="tracking target temporarily lost", now=now, severity="warn")
            return True

        lost_since = float(self._track_state["lost_since_t"])
        if (now - lost_since) <= float(policy["target_lost_timeout_s"]):
            return True

        self._track_state["phase"] = "reacquiring"
        self._record_milestone("track_reacquire_started", now, {"lost_duration_s": now - lost_since})
        self._emit_runtime_event("track_reacquire_started", now=now, details={"lost_duration_s": now - lost_since})
        return self._start_track_fallback(now, reason="track_target_lost_timeout")

    def _start_track_fallback(self, now: float, *, reason: str) -> bool:
        if self._track_state is None:
            return False
        target = self._track_state.get("fallback_stage_id")
        if target and target != self.stage_id:
            self._record_milestone("track_fallback_started", now, {"reason": reason, "target_stage": target})
            self._emit_runtime_event("track_fallback_started", now=now, details={"reason": reason, "target_stage": target})
            self._transition_to(
                target,
                Transition(fr=self.stage_id, to=target, when={"type": "TRACK_FALLBACK", "params": {"reason": reason}}),
            )
            return True
        self._request_stop(f"track failed: {reason}")
        return True

    def _drive_observe_stage(self, now: float, criteria: Dict[str, Any]) -> bool:
        stage = self.plan.stages[self.stage_id]
        if stage.intent != "OBSERVE" or self._observe_state is None:
            return False
        phase = self._observe_state.get("phase")
        if phase == "unavailable":
            if self._observe_state.get("rollback_on_fail", True):
                return self._start_observe_rollback(now, reason=str(self._observe_state.get("reason", "unavailable")))
            return False
        if phase == "approach":
            if self._current_goal_reached():
                self._record_milestone("observe_goal_reached", now, {"goal": self._observe_state.get("observe_goal")})
                self._emit_runtime_event("observe_goal_reached", now=now, details={"goal": self._observe_state.get("observe_goal")})
                if self.hold_topic:
                    self._mux_select(self.hold_topic)
                    self._publish_hold_for(float(self._observe_state.get("hover_duration_s", 2.0)))
                self._observe_state["phase"] = "hover_done"
                self._observe_state["hover_finished_t"] = time.time()
                self._record_milestone("observe_hover_finished", self._observe_state["hover_finished_t"], {"duration_s": self._observe_state.get("hover_duration_s", 2.0)})
                self._emit_runtime_event("observe_hover_finished", now=self._observe_state["hover_finished_t"], details={"duration_s": self._observe_state.get("hover_duration_s", 2.0)})
            return True
        if phase == "hover_done":
            if criteria.get("success_met"):
                self._observe_state["phase"] = "complete"
                self._record_milestone("observe_supported", now, {})
                self._emit_runtime_event("observe_supported", now=now, details={})
                return False
            if self._observe_state.get("rollback_on_fail", True):
                return self._start_observe_rollback(now, reason="observe_inconclusive")
            return False
        if phase == "rollback":
            if self._current_goal_reached():
                self._record_milestone("observe_rollback_reached", now, {"goal": self._observe_state.get("start_odom_goal")})
                self._emit_runtime_event("observe_rollback_reached", now=now, details={"goal": self._observe_state.get("start_odom_goal")})
                target = self._observe_state.get("fallback_stage_id")
                if target and target != self.stage_id:
                    self._transition_to(
                        target,
                        Transition(fr=self.stage_id, to=target, when={"type": "OBSERVE_ROLLBACK", "params": {"reason": "observe_inconclusive"}}),
                    )
                    return True
                self._request_stop("observe rollback completed")
                return True
            return True
        return False

    def _start_observe_rollback(self, now: float, *, reason: str) -> bool:
        if self._observe_state is None:
            return False
        if self._observe_state.get("phase") == "rollback":
            return True
        rollback_goal = self._observe_state.get("start_odom_goal")
        if not isinstance(rollback_goal, dict):
            return False
        self._observe_state["phase"] = "rollback"
        self._observe_state["rollback_started_t"] = now
        ok = self._publish_navigation_goal(self.active_entity_id, rollback_goal, now, trigger=True)
        self._record_milestone("observe_rollback_started", now, {"goal": rollback_goal, "reason": reason, "ok": ok})
        self._emit_runtime_event("observe_rollback_started", now=now, details={"goal": rollback_goal, "reason": reason, "ok": ok})
        return True

    # ---------------- observation ingestion ----------------
    def _ingest_primary_detection(self, now: float) -> Optional[Detection]:
        obj = self.infer_reader.poll()
        if not obj:
            return None
        det = self.infer_reader.to_detection(obj)
        if det is None:
            return None
        if (now - self.stage_enter_t) < self.stage_settle_s or det.t_wall < (self.stage_enter_t - 0.01):
            return det
        self.bb.update_detection(self.active_entity_id, det)
        self.world_state.ingest_detection(det, default_entity_id=self.active_entity_id, source="primary")
        if det.found:
            self._record_milestone("primary_detected", now, {"score": det.score})
        return det

    def _ingest_cue_detection(self, now: float) -> Optional[Detection]:
        if self.cue_reader is None:
            return None
        obj = self.cue_reader.poll()
        if not obj:
            return None
        det = self.cue_reader.to_detection(obj)
        if det is None:
            return None
        self.world_state.ingest_detection(det, source="cue")
        return det

    def _ingest_localization(self, now: float) -> Optional[TargetLocalization]:
        if self.localization_reader is None:
            return None
        obj = self.localization_reader.poll()
        if not obj:
            return None
        loc = self.localization_reader.to_localization(obj)
        if loc is None or not loc.entity_id:
            return None
        self._last_localization = loc
        self.world_state.set_entity_localization(
            loc.entity_id,
            target_position_body=loc.target_position_body,
            target_position_world=loc.target_position_world,
            localization_confidence=loc.localization_confidence,
            depth_valid_ratio=loc.depth_valid_ratio,
            support_pixels=loc.support_pixels,
            failure_reason=loc.failure_reason,
            t_wall=loc.t_det,
        )
        if loc.found and loc.entity_id == self.active_entity_id:
            self._record_milestone(
                "target_localized",
                now,
                {
                    "confidence": loc.localization_confidence,
                    "support_pixels": loc.support_pixels,
                },
            )
            self._emit_runtime_event(
                "target_localized",
                now=now,
                details={
                    "entity_id": loc.entity_id,
                    "confidence": loc.localization_confidence,
                    "support_pixels": loc.support_pixels,
                },
            )
        return loc

    # ---------------- verification / criteria ----------------
    def _evaluate_verifiers(self, now: float) -> Dict[str, VerificationResult]:
        stage = self.plan.stages[self.stage_id]
        stage_state = self._get_stage_state()
        results: Dict[str, VerificationResult] = {}
        min_score = float(self.verified_cfg.get("min_score", 0.0))
        diag_to_emit: Optional[Tuple[str, str, Dict[str, Any]]] = None

        for verifier in self.verifiers:
            res = verifier.evaluate(
                now=now,
                plan=self.plan,
                stage=stage,
                stage_state=stage_state,
                world_state=self.world_state,
                blackboard=self.bb,
                active_entity_id=self.active_entity_id,
                current_req_id=self.current_req_id,
                stage_id=self.stage_id,
                min_score=min_score,
            )
            results[verifier.name] = res

        perception_res = results.get("perception")
        if perception_res is not None:
            self.world_state.set_entity_verification(
                self.active_entity_id,
                perception_res.status,
                note=perception_res.reason or None,
            )
            self.world_state.set_stage_verification(self.stage_id, perception_res.status)
            if perception_res.status == VerificationStatus.VERIFIED:
                self._record_milestone("primary_verified", now, {"score": perception_res.score})
                self._clear_stage_diag()
            else:
                diag_code = perception_res.details.get("diagnostic_code")
                if diag_code:
                    diag_to_emit = (diag_code, perception_res.reason, perception_res.details)

        if diag_to_emit is not None:
            self._emit_diag(code=diag_to_emit[0], message=diag_to_emit[1], now=now, details=diag_to_emit[2])

        self._verification_results = results
        return results

    def _eval_event_safe(self, event: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        try:
            return self.cond.eval(event, self.stage_enter_t), None
        except Exception as e:
            return False, str(e)

    def _evaluate_stage_criteria(self, now: float) -> Dict[str, Any]:
        stage = self.plan.stages[self.stage_id]
        success_evals: List[Dict[str, Any]] = []
        failure_evals: List[Dict[str, Any]] = []

        for ev in stage.success_criteria:
            ok, err = self._eval_event_safe(ev)
            success_evals.append({"type": ev.get("type"), "params": ev.get("params", {}), "ok": ok, "error": err})

        for ev in stage.failure_criteria:
            ok, err = self._eval_event_safe(ev)
            failure_evals.append({"type": ev.get("type"), "params": ev.get("params", {}), "ok": ok, "error": err})

        success_met = bool(stage.success_criteria) and all(e["ok"] for e in success_evals)
        failure_met = any(e["ok"] for e in failure_evals)

        if success_met:
            if self._record_milestone("stage_success", now):
                self._emit_runtime_event("stage_success", now=now)
        if failure_met:
            if self._record_milestone("stage_failed", now):
                self._emit_runtime_event("stage_failure", now=now)
            self._emit_diag(
                code=DiagnosticCode.STAGE_FAILURE,
                message="stage failure criteria met",
                now=now,
                severity="error",
            )

        return {
            "success": success_evals,
            "failure": failure_evals,
            "success_met": success_met,
            "failure_met": failure_met,
        }

    def _maybe_recover(self, now: float, criteria: Dict[str, Any]) -> None:
        if not criteria.get("failure_met"):
            self._current_recovery = None
            return
        retry_count = self._stage_retry_counts.get(self.stage_id, 0)
        diag = self.diagnostics.latest(self.stage_id)
        action = self.recovery.suggest(
            stage_id=self.stage_id,
            stage_intent=self.plan.stages[self.stage_id].intent,
            diagnostic_code=None if diag is None else diag.code,
            retry_count=retry_count,
        )
        self._current_recovery = action
        self._emit_runtime_event(
            "recovery_suggested",
            now=now,
            details=None if action is None else {"kind": action.kind, "reason": action.reason},
        )
        st = self._get_stage_state()
        if st is not None:
            st.last_recovery_action = None if action is None else action.kind

        if action is None or action.kind == "safe_terminate":
            self._request_stop(f"stage failure at {self.stage_id}")
            return

        if action.kind == "retry_same_stage" and retry_count < self.recovery.max_retries_per_stage:
            self._stage_retry_counts[self.stage_id] = retry_count + 1
            self._record_milestone("recovery_started", now, {"action": action.kind})
            self._emit_runtime_event("recovery_started", now=now, details={"kind": action.kind})
            self._retry_current_stage()
            self._record_milestone("recovery_finished", time.time(), {"action": action.kind})
            self._emit_runtime_event("recovery_finished", now=time.time(), details={"kind": action.kind})
            return

        if action.kind == "hold_and_reobserve":
            self._record_milestone("recovery_started", now, {"action": action.kind})
            self._emit_runtime_event("recovery_started", now=now, details={"kind": action.kind})
            if self.hold_topic:
                self._mux_select(self.hold_topic)
                self._publish_hold_for(self.recovery.hold_reobserve_s)
            self._record_milestone("recovery_finished", time.time(), {"action": action.kind})
            self._emit_runtime_event("recovery_finished", now=time.time(), details={"kind": action.kind})
            return

        if action.kind == "fallback_to_search":
            search_stage = next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None)
            if search_stage is not None:
                self._record_milestone("recovery_started", now, {"action": action.kind, "target_stage": search_stage})
                self._emit_runtime_event("recovery_started", now=now, details={"kind": action.kind, "target_stage": search_stage})
                self._transition_to(search_stage, Transition(fr=self.stage_id, to=search_stage, when={"type": "RECOVERY", "params": {"reason": action.reason}}))
                self._record_milestone("recovery_finished", time.time(), {"action": action.kind})
                self._emit_runtime_event("recovery_finished", now=time.time(), details={"kind": action.kind, "target_stage": search_stage})
                return

        self._request_stop(f"unhandled recovery at {self.stage_id}")

    # ---------------- semantic verification ----------------
    def _should_run_semantic_verifier(self, now: float, verifier_results: Dict[str, VerificationResult], criteria: Dict[str, Any]) -> bool:
        if not self.semantic_enabled:
            return False
        stage_state = self._get_stage_state()
        if stage_state is None:
            return False
        if (now - self.stage_enter_t) < self.semantic_min_stage_dwell_s:
            return False
        if stage_state.last_semantic_verify_t is not None and (now - stage_state.last_semantic_verify_t) < self.semantic_cooldown_s:
            return False

        ent = self.world_state.entities.get(self.active_entity_id)
        if ent is None or ent.last_primary_detection is None:
            return False

        if criteria.get("failure_met") and self.semantic_trigger_on_failure:
            return True

        if self.semantic_trigger_on_ambiguous and ent.proposal_status == "multi_candidate_ambiguous":
            return True

        if self.semantic_trigger_on_inconclusive:
            res = verifier_results.get("perception")
            if res is not None and res.status in (VerificationStatus.INCONCLUSIVE, VerificationStatus.CONTRADICTED, VerificationStatus.FAILED):
                return True

        if self.semantic_trigger_on_relations and self.plan.relations:
            if ent.last_primary_detection is not None and ent.last_primary_detection.found:
                return True

        return False

    def _build_semantic_verify_request(self, now: float) -> Optional[SemanticVerifyRequest]:
        stage = self.plan.stages[self.stage_id]
        ent = self.world_state.entities.get(self.active_entity_id)
        if ent is None:
            return None
        plan_ent = self.plan.entities.get(self.active_entity_id)
        topk = []
        for cand in ent.topk_candidates:
            topk.append(
                {
                    "rank": cand.rank,
                    "bbox": cand.bbox_xyxy,
                    "score": cand.score,
                    "class_name": cand.class_name,
                    "text_conf": cand.text_conf,
                    "mask_score": cand.mask_score,
                    "mask_quality": cand.mask_quality,
                }
            )

        if ent.proposal_status == "multi_candidate_ambiguous":
            query_type = "ambiguity_resolve"
        elif stage.relations:
            query_type = "relation_verify"
        else:
            query_type = "candidate_verify"

        return SemanticVerifyRequest(
            req_id=self.current_req_id,
            stage_id=self.stage_id,
            entity_id=self.active_entity_id,
            query_type=query_type,
            primary_prompt="" if plan_ent is None else (plan_ent.prompt or ""),
            cue_prompts=[self.plan.entities[cid].prompt or "" for cid in self._ordered_cue_targets(self.stage_id) if cid in self.plan.entities],
            frame_path=self.frame_path,
            vis_path=None if ent.last_primary_detection is None else ent.last_primary_detection.source_path,
            topk_candidates=topk,
            relations=[
                {
                    "subject_id": rel.subject_id,
                    "predicate": rel.predicate,
                    "object_id": rel.object_id,
                    "description": rel.description,
                }
                for rel in self.plan.relations
            ],
            assumptions=list(self.plan.assumptions),
            open_questions=list(self.plan.open_questions),
            runtime_context={
                "intent": stage.intent,
                "elapsed_s": now - self.stage_enter_t,
                "proposal_status": ent.proposal_status,
                "proposal_uncertainty": ent.proposal_uncertainty,
                "spatial_hint": ent.spatial_hint,
                "cue_support_score": ent.cue_support_score,
            },
        )

    def _maybe_run_semantic_verifier(self, now: float, verifier_results: Dict[str, VerificationResult], criteria: Dict[str, Any]) -> bool:
        if not self._should_run_semantic_verifier(now, verifier_results, criteria):
            return False
        request = self._build_semantic_verify_request(now)
        if request is None:
            return False
        stage_state = self._get_stage_state()
        if stage_state is not None:
            stage_state.pending_verify = True

        self._emit_runtime_event(
            "skill_invoked",
            now=now,
            details={"skill_id": "semantic_verify", "intent": "VERIFY", "stage_id": self.stage_id},
        )
        response = self.semantic_verifier.verify(request)
        self._last_semantic_response = response
        self._emit_runtime_event(
            "skill_stopped",
            now=time.time(),
            details={"skill_id": "semantic_verify", "intent": "VERIFY", "stage_id": self.stage_id},
        )
        self.world_state.set_stage_semantic_state(
            self.stage_id,
            pending_verify=False,
            last_semantic_verify_t=now,
            last_semantic_followup=None if response.result is None else response.result.recommended_followup,
        )
        self._record_milestone("semantic_verify_called", now, {"query_type": request.query_type})
        self._emit_runtime_event("semantic_verify_called", now=now, details={"query_type": request.query_type})

        if not response.accepted or response.result is None:
            self._emit_diag(
                code=DiagnosticCode.SEMANTIC_INCONCLUSIVE,
                message=f"semantic verifier unavailable: {response.reason}",
                now=now,
                severity="info",
            )
            self._emit_runtime_event("semantic_verify_failed", now=now, details={"reason": response.reason})
            return False

        result = response.result
        self.world_state.set_entity_semantic_verification(
            self.active_entity_id,
            status=result.verify_status,
            confidence=result.verification_confidence,
            explanation=result.explanation,
            failure_hypothesis=result.failure_hypothesis,
            need_additional_view=result.need_additional_view,
            recommended_followup=result.recommended_followup,
            supports_relation=result.supports_relation,
            t_wall=now,
        )

        if stage_state is not None:
            stage_state.last_semantic_followup = result.recommended_followup

        if result.verify_status in ("supported", "verified"):
            self._record_milestone("semantic_verify_supported", now, {"confidence": result.verification_confidence})
            self._emit_runtime_event(
                "semantic_verify_supported",
                now=now,
                details={"confidence": result.verification_confidence, "followup": result.recommended_followup},
            )
            return True

        if result.need_additional_view:
            self._emit_diag(
                code=DiagnosticCode.SEMANTIC_NEEDS_VIEW,
                message=result.explanation or "semantic verification requests additional view",
                now=now,
                severity="info",
                details={"followup": result.recommended_followup, "confidence": result.verification_confidence},
            )
        else:
            self._emit_diag(
                code=DiagnosticCode.SEMANTIC_NOT_SUPPORTED if result.verify_status == "not_supported" else DiagnosticCode.SEMANTIC_INCONCLUSIVE,
                message=result.explanation or "semantic verification inconclusive",
                now=now,
                severity="info",
                details={"followup": result.recommended_followup, "confidence": result.verification_confidence},
            )
        if result.need_additional_view and (result.recommended_followup or "").strip().lower() in ("", "none"):
            self._start_verify_window(
                now,
                reason=result.explanation or "semantic verification requested additional observation",
                source="semantic_result",
            )
        if self._apply_semantic_followup(result, now):
            self._record_milestone(
                "semantic_followup_applied",
                now,
                {"followup": result.recommended_followup, "confidence": result.verification_confidence},
            )
            self._emit_runtime_event(
                "semantic_followup_applied",
                now=now,
                details={"followup": result.recommended_followup, "confidence": result.verification_confidence},
            )
            return True
        return True

    def _apply_semantic_followup(self, result, now: float) -> bool:
        followup = (result.recommended_followup or "").strip().lower()
        if not followup or followup == "none":
            return False
        if not self.semantic_auto_apply_followup:
            return False
        if self.semantic_allow_followups and followup not in self.semantic_allow_followups:
            return False

        if followup == "continue":
            return False

        if followup == "hold_and_reobserve":
            if self.hold_topic:
                self._mux_select(self.hold_topic)
                self._publish_hold_for(self.recovery.hold_reobserve_s)
                return True
            return False

        if followup == "retry_same_stage":
            retry_count = self._stage_retry_counts.get(self.stage_id, 0)
            if retry_count < self.recovery.max_retries_per_stage:
                self._stage_retry_counts[self.stage_id] = retry_count + 1
                self._retry_current_stage()
                return True
            return False

        if followup == "fallback_to_search":
            target = next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None)
            if target:
                self._transition_to(target, Transition(fr=self.stage_id, to=target, when={"type": "SEMANTIC_FOLLOWUP", "params": {"followup": followup}}))
                return True
            return False

        if followup == "switch_cue_priority":
            cue_order = result.extra.get("cue_target_order")
            if isinstance(cue_order, list):
                cue_order = [str(x) for x in cue_order if str(x) in self.plan.stages[self.stage_id].cue_targets]
            else:
                cue_entity_id = result.extra.get("cue_entity_id")
                cue_order = []
                if cue_entity_id:
                    cue_order.append(str(cue_entity_id))
                cue_order.extend([eid for eid in self.plan.stages[self.stage_id].cue_targets if eid not in cue_order])
            if cue_order:
                self._cue_priority_override[self.stage_id] = cue_order
                if self.reasoner_auto_rewrite_request:
                    ent = self.plan.entities[self.active_entity_id]
                    self._rewrite_perception_request(
                        stage_id=self.stage_id,
                        primary_entity_id=self.active_entity_id,
                        prompt=ent.prompt or "",
                        cue_order=cue_order,
                        now=now,
                    )
                return True
            return False

        if followup == "insert_verify_stage":
            return self._start_verify_window(
                now,
                reason=result.explanation or "semantic verifier requested additional verification",
                source="semantic_followup",
            )

        return False

    # ---------------- reasoner ----------------
    def _should_run_reasoner(self, now: float, verifier_results: Dict[str, VerificationResult], criteria: Dict[str, Any]) -> bool:
        if not self.reasoner_enabled:
            return False
        stage_state = self._get_stage_state()
        if stage_state is None:
            return False
        if (now - self.stage_enter_t) < self.reasoner_min_stage_dwell_s:
            return False
        if stage_state.last_reasoner_t is not None and (now - stage_state.last_reasoner_t) < self.reasoner_cooldown_s:
            return False
        if criteria.get("failure_met"):
            return True
        latest_diag = self.diagnostics.latest(self.stage_id)
        if latest_diag is not None:
            return True
        if self.reasoner_trigger_on_inconclusive:
            for res in verifier_results.values():
                if res.status in (VerificationStatus.INCONCLUSIVE, VerificationStatus.CONTRADICTED, VerificationStatus.FAILED):
                    return True
        return False

    def _rewrite_perception_request(self, *, stage_id: str, primary_entity_id: str, prompt: str, cue_order: List[str], now: float) -> None:
        cue_reqs: List[Dict[str, Any]] = []
        cue_prompts: List[str] = []
        for ceid in cue_order:
            ce = self.plan.entities.get(ceid)
            if ce and ce.prompt:
                cue_reqs.append({"entity_id": ceid, "prompt": ce.prompt})
                cue_prompts.append(ce.prompt)
        if self.perception_request_path:
            extra = {
                "intent": self.plan.stages[stage_id].intent,
                "budget": self.plan.stages[stage_id].budget or {},
                "policy": self.plan.stages[stage_id].policy or {},
            }
            extra.update(self._semantic_exploration_extra(stage_id))
            _atomic_write_json(
                self.perception_request_path,
                {
                    "req_id": self.current_req_id,
                    "t_wall": now,
                    "stage_id": stage_id,
                    "primary": {"entity_id": primary_entity_id, "prompt": prompt},
                    "cues": cue_reqs,
                    "extra": extra,
                },
            )
        if self.emit_cues_txt and self.cues_txt_path:
            _atomic_write_lines(self.cues_txt_path, cue_prompts)

    def _apply_reasoner_action(self, decision: GuardDecision, response: ReasonerResponse, now: float) -> bool:
        if not decision.approved or not decision.details.get("auto_apply", False):
            return False
        if response.proposal is None:
            return False
        action = decision.action

        if action == "continue":
            return False

        if action == "hold_and_reobserve":
            self._record_milestone("reasoner_hold", now, {"proposal_id": response.proposal.proposal_id})
            self._emit_runtime_event("reasoner_action", now=now, details={"action": action})
            if self.hold_topic:
                self._mux_select(self.hold_topic)
                self._publish_hold_for(self.recovery.hold_reobserve_s)
            return True

        if action == "retry_same_stage":
            self._record_milestone("reasoner_retry", now, {"proposal_id": response.proposal.proposal_id})
            self._emit_runtime_event("reasoner_action", now=now, details={"action": action})
            retry_count = self._stage_retry_counts.get(self.stage_id, 0)
            if retry_count < self.recovery.max_retries_per_stage:
                self._stage_retry_counts[self.stage_id] = retry_count + 1
                self._retry_current_stage()
                return True
            return False

        if action == "safe_terminate":
            self._record_milestone("reasoner_terminate", now, {"proposal_id": response.proposal.proposal_id})
            self._emit_runtime_event("reasoner_action", now=now, details={"action": action})
            self._request_stop(f"reasoner terminate at {self.stage_id}")
            return True

        if action == "fallback_to_search":
            target = response.proposal.target_stage_id
            if not target:
                target = next((sid for sid, st in self.plan.stages.items() if st.intent == "SEARCH"), None)
            if target:
                self._record_milestone("reasoner_fallback", now, {"proposal_id": response.proposal.proposal_id, "target_stage": target})
                self._emit_runtime_event("reasoner_action", now=now, details={"action": action, "target_stage": target})
                self._transition_to(target, Transition(fr=self.stage_id, to=target, when={"type": "REASONER", "params": {"proposal_id": response.proposal.proposal_id, "action": action}}))
                return True
            return False

        if action == "switch_cue_priority":
            cue_order = response.proposal.extra.get("cue_target_order")
            if isinstance(cue_order, list):
                cue_order = [str(x) for x in cue_order if str(x) in self.plan.stages[self.stage_id].cue_targets]
            else:
                cue_entity_id = response.proposal.extra.get("cue_entity_id")
                cue_order = []
                if cue_entity_id:
                    cue_order.append(str(cue_entity_id))
                cue_order.extend([eid for eid in self.plan.stages[self.stage_id].cue_targets if eid not in cue_order])
            if cue_order:
                self._cue_priority_override[self.stage_id] = cue_order
                if self.reasoner_auto_rewrite_request:
                    ent = self.plan.entities[self.active_entity_id]
                    self._rewrite_perception_request(
                        stage_id=self.stage_id,
                        primary_entity_id=self.active_entity_id,
                        prompt=ent.prompt or "",
                        cue_order=cue_order,
                        now=now,
                    )
                self._record_milestone("reasoner_switch_cue", now, {"proposal_id": response.proposal.proposal_id, "cue_order": cue_order})
                self._emit_runtime_event("reasoner_action", now=now, details={"action": action, "cue_order": cue_order})
                return True
            return False

        if action == "insert_verify_stage":
            self._record_milestone("reasoner_verify_window", now, {"proposal_id": response.proposal.proposal_id})
            self._emit_runtime_event("reasoner_action", now=now, details={"action": action})
            return self._start_verify_window(
                now,
                reason=response.proposal.assessment or "reasoner requested verification",
                source="reasoner",
            )

        return False

    def _maybe_run_reasoner(self, now: float, verifier_results: Dict[str, VerificationResult], criteria: Dict[str, Any]) -> bool:
        if not self._should_run_reasoner(now, verifier_results, criteria):
            return False
        summary = self.summarizer.build(executor=self, now=now, verifier_results=verifier_results, criteria=criteria)
        response = self.reasoner.propose(summary)
        self._last_reasoner_response = response
        self._emit_runtime_event("reasoner_called", now=now, details={"accepted": response.accepted, "reason": response.reason})
        stage_state = self._get_stage_state()
        if stage_state is not None:
            stage_state.last_reasoner_t = now

        if not response.accepted or response.proposal is None:
            self._last_guard_decision = GuardDecision(approved=False, reason=response.reason)
            return False

        decision = self.reasoner_guard.review(response.proposal, plan_stage_ids=list(self.plan.stages.keys()))
        self._last_guard_decision = decision
        if not decision.approved:
            self._emit_diag(
                code="reasoner_rejected",
                message=f"reasoner proposal rejected: {decision.reason}",
                now=now,
                severity="info",
                details={"proposal_id": response.proposal.proposal_id, "action": response.proposal.suggested_action},
            )
            self._emit_runtime_event("reasoner_rejected", now=now, details={"reason": decision.reason})
            return False

        self._record_milestone("reasoner_called", now, {"proposal_id": response.proposal.proposal_id, "action": response.proposal.suggested_action})
        self._emit_runtime_event(
            "reasoner_approved",
            now=now,
            details={"proposal_id": response.proposal.proposal_id, "action": response.proposal.suggested_action},
        )
        return self._apply_reasoner_action(decision, response, now)

    # ---------------- core loop ----------------
    def run(self):
        self._enter_stage(self.stage_id)
        try:
            while not self._stop_requested:
                self._tick_once()
                time.sleep(self.tick_dt)
        except KeyboardInterrupt:
            print("PlanExecutor interrupted.")
        finally:
            self._exit_stage(self.stage_id)
            self._emit_runtime_event(
                "mission_finished",
                now=time.time(),
                details={"terminal_reason": self._terminal_reason, "mission_elapsed": self._mission_elapsed()},
            )
            if self._terminal_reason:
                print(f"[PlanExecutor] terminal reason: {self._terminal_reason}")
            self.close()

    def _tick_once(self):
        now = time.time()
        self.world_state.step(now)
        verify_window_active = self._update_verify_window(now)

        primary_det = self._ingest_primary_detection(now)
        cue_det = self._ingest_cue_detection(now)
        localization = self._ingest_localization(now)

        stage = self.plan.stages[self.stage_id]
        stage_state = self._get_stage_state()
        if stage_state is not None:
            stage_state.elapsed_s = now - self.stage_enter_t

        ad = self.adapters.get(stage.intent)
        if ad:
            ad.tick(self._make_stage_ctx(self.stage_id))
            self.world_state.set_stage_skill_status(self.stage_id, "running")

        self._update_navigation_progress(now)
        verifier_results = self._evaluate_verifiers(now)
        criteria = self._evaluate_stage_criteria(now)
        self._maybe_run_semantic_verifier(now, verifier_results, criteria)
        observe_active = self._drive_observe_stage(now, criteria)
        track_active = self._drive_track_stage(now, criteria)

        outs = self.plan.outgoing(self.stage_id)
        fired: Optional[Transition] = None
        transition_evals = []
        for tr in outs:
            ok, err = self._eval_event_safe(tr.when)
            transition_evals.append({"to": tr.to, "when": tr.when, "ok": ok, "error": err})
            if ok and fired is None:
                fired = tr

        if verify_window_active or observe_active or track_active:
            for tr_eval in transition_evals:
                if tr_eval["ok"]:
                    if verify_window_active:
                        tr_eval["suppressed_by_verify_window"] = True
                    if observe_active:
                        tr_eval["suppressed_by_observe_skill"] = True
                    if track_active:
                        tr_eval["suppressed_by_track_skill"] = True
            fired = None

        snap = self._build_runtime_snapshot(
            now=now,
            primary_det=primary_det,
            cue_det=cue_det,
            localization=localization,
            transition_evals=transition_evals,
            verifier_results=verifier_results,
            criteria=criteria,
        )
        self._log(snap)

        if self._maybe_run_reasoner(now, verifier_results, criteria):
            return

        if fired is not None:
            self._transition_to(fired.to, fired)
            return

        if criteria["failure_met"]:
            self._maybe_recover(now, criteria)
            return

        if criteria["success_met"] and not outs and not verify_window_active:
            self._request_stop(f"stage success at {self.stage_id}")

    def _build_runtime_snapshot(
        self,
        *,
        now: float,
        primary_det: Optional[Detection],
        cue_det: Optional[Detection],
        localization: Optional[TargetLocalization],
        transition_evals: List[Dict[str, Any]],
        verifier_results: Dict[str, VerificationResult],
        criteria: Dict[str, Any],
    ) -> Dict[str, Any]:
        ent = self.world_state.entities.get(self.active_entity_id)
        cue_summary = []
        stage = self.plan.stages[self.stage_id]
        for cue_id in stage.cue_targets:
            cue_ent = self.world_state.entities.get(cue_id)
            if cue_ent is None:
                continue
            cue_summary.append(
                {
                    "entity_id": cue_id,
                    "cue_support_score": cue_ent.cue_support_score,
                    "last_cue_t": None if cue_ent.last_cue_detection is None else cue_ent.last_cue_detection.t_wall,
                }
            )

        return {
            "rec_type": "runtime_snapshot",
            "t": now,
            "stage": self.stage_id,
            "intent": stage.intent,
            "active_entity": self.active_entity_id,
            "current_req_id": self.current_req_id,
            "mission_elapsed": self._mission_elapsed(),
            "stage_elapsed": now - self.stage_enter_t,
            "current_skill_id": self._stage_skill_id(self.stage_id),
            "skill_contract": None if self.skill_registry.get_by_intent(stage.intent) is None else self.skill_registry.get_by_intent(stage.intent).to_dict(),
            "milestones": self.progress.summary(self.stage_id),
            "primary_det": None if primary_det is None else {
                "found": primary_det.found,
                "score": primary_det.score,
                "req_id": primary_det.req_id,
                "entity_id": primary_det.entity_id,
                "role": primary_det.role,
                "t_wall": primary_det.t_wall,
                "proposal_status": primary_det.proposal_status,
                "mask_quality": primary_det.mask_quality,
                "topk_candidates": [
                    {
                        "rank": cand.rank,
                        "score": cand.score,
                        "class_name": cand.class_name,
                    }
                    for cand in primary_det.topk_candidates
                ],
                "proposal_uncertainty": primary_det.proposal_uncertainty,
            },
            "cue_det": None if cue_det is None else {
                "found": cue_det.found,
                "score": cue_det.score,
                "entity_id": cue_det.entity_id,
                "role": cue_det.role,
                "t_wall": cue_det.t_wall,
                "proposal_status": cue_det.proposal_status,
                "topk_candidates": [
                    {
                        "rank": cand.rank,
                        "score": cand.score,
                        "class_name": cand.class_name,
                    }
                    for cand in cue_det.topk_candidates
                ],
                "proposal_uncertainty": cue_det.proposal_uncertainty,
            },
            "localization": None if localization is None else {
                "found": localization.found,
                "entity_id": localization.entity_id,
                "req_id": localization.req_id,
                "stage_id": localization.stage_id,
                "t_det": localization.t_det,
                "t_depth": localization.t_depth,
                "t_odom": localization.t_odom,
                "target_position_body": localization.target_position_body,
                "target_position_world": localization.target_position_world,
                "localization_confidence": localization.localization_confidence,
                "depth_valid_ratio": localization.depth_valid_ratio,
                "support_pixels": localization.support_pixels,
                "failure_reason": localization.failure_reason,
            },
            "entity_state": None if ent is None else {
                "verified": ent.verified,
                "verification_status": ent.verification_status,
                "stable_hit_streak": ent.stable_hit_streak,
                "evidence_count": ent.evidence_count,
                "cue_support_score": ent.cue_support_score,
                "last_seen_t": ent.last_seen_t,
                "proposal_status": ent.proposal_status,
                "last_mask_quality": ent.last_mask_quality,
                "proposal_uncertainty": ent.proposal_uncertainty,
                "spatial_hint": ent.spatial_hint,
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
            "verify_window": None if self._get_stage_state() is None else {
                "active": bool(self._get_stage_state().verify_window_until and now < float(self._get_stage_state().verify_window_until)),
                "until": self._get_stage_state().verify_window_until,
                "reason": self._get_stage_state().verify_window_reason,
            },
            "observe_state": None if self._observe_state is None else dict(self._observe_state),
            "track_state": None if self._track_state is None else dict(self._track_state),
            "cue_summary": cue_summary,
            "criteria": criteria,
            "verifiers": {
                name: {
                    "status": res.status,
                    "score": res.score,
                    "reason": res.reason,
                    "details": res.details,
                }
                for name, res in verifier_results.items()
            },
            "diagnostics": self.diagnostics.summary(self.stage_id)[-5:],
            "recovery": None if self._current_recovery is None else {
                "kind": self._current_recovery.kind,
                "reason": self._current_recovery.reason,
                "details": self._current_recovery.details,
            },
            "reasoner": None if self._last_reasoner_response is None else {
                "accepted": self._last_reasoner_response.accepted,
                "reason": self._last_reasoner_response.reason,
                "proposal": None if self._last_reasoner_response.proposal is None else {
                    "proposal_id": self._last_reasoner_response.proposal.proposal_id,
                    "assessment": self._last_reasoner_response.proposal.assessment,
                    "failure_hypothesis": self._last_reasoner_response.proposal.failure_hypothesis,
                    "suggested_action": self._last_reasoner_response.proposal.suggested_action,
                    "target_stage_id": self._last_reasoner_response.proposal.target_stage_id,
                    "confidence": self._last_reasoner_response.proposal.confidence,
                    "extra": self._last_reasoner_response.proposal.extra,
                },
            },
            "semantic_verifier": None if self._last_semantic_response is None else {
                "accepted": self._last_semantic_response.accepted,
                "reason": self._last_semantic_response.reason,
                "result": None if self._last_semantic_response.result is None else {
                    "verify_status": self._last_semantic_response.result.verify_status,
                    "supports_primary_target": self._last_semantic_response.result.supports_primary_target,
                    "supports_relation": self._last_semantic_response.result.supports_relation,
                    "scene_consistency": self._last_semantic_response.result.scene_consistency,
                    "failure_hypothesis": self._last_semantic_response.result.failure_hypothesis,
                    "need_additional_view": self._last_semantic_response.result.need_additional_view,
                    "verification_confidence": self._last_semantic_response.result.verification_confidence,
                    "recommended_followup": self._last_semantic_response.result.recommended_followup,
                    "explanation": self._last_semantic_response.result.explanation,
                },
            },
            "reasoner_guard": None if self._last_guard_decision is None else {
                "approved": self._last_guard_decision.approved,
                "reason": self._last_guard_decision.reason,
                "action": self._last_guard_decision.action,
                "details": self._last_guard_decision.details,
            },
            "nav_goal_state": self._nav_goal_state,
            "outgoing": transition_evals,
        }

    def _make_stage_ctx(self, stage_id: str) -> Dict[str, Any]:
        st = self.plan.stages[stage_id]
        eid = st.primary_targets[0]
        ent = self.plan.entities.get(eid)
        entity = {
            "id": eid,
            "prompt": None if ent is None else ent.prompt,
            "extra": {} if ent is None else ent.extra,
        }
        return {
            "stage_id": stage_id,
            "intent": st.intent,
            "skill_id": self._stage_skill_id(stage_id),
            "entity_id": eid,
            "entity": entity,
            "policy": st.policy,
            "budget": st.budget,
            "skill_contract": None if self._stage_skill_contract(stage_id) is None else self._stage_skill_contract(stage_id).to_dict(),
            "relations": [
                {
                    "subject_id": rel.subject_id,
                    "predicate": rel.predicate,
                    "object_id": rel.object_id,
                    "description": rel.description,
                }
                for rel in self.plan.relations
            ],
            "assumptions": self.plan.assumptions,
            "open_questions": self.plan.open_questions,
        }

    def _retry_current_stage(self) -> None:
        print(f"[Recovery] retrying stage {self.stage_id}")
        self._exit_stage(self.stage_id)
        self.stage_enter_t = time.time()
        self.bb.reset_entity(self.active_entity_id)
        self._enter_stage(self.stage_id)

    def _transition_to(self, next_stage_id: str, fired: Transition):
        print(f"[Transition] {self.stage_id} -> {next_stage_id} because {fired.when.get('type')} {fired.when.get('params')}")
        prev_skill_id = self._stage_skill_id(self.stage_id)
        next_skill_id = self._stage_skill_id(next_stage_id)
        self._emit_runtime_event(
            "transition",
            now=time.time(),
            details={"from": self.stage_id, "to": next_stage_id, "when": fired.when},
        )
        if prev_skill_id != next_skill_id:
            self._emit_runtime_event(
                "skill_switched",
                now=time.time(),
                details={"from_skill": prev_skill_id, "to_skill": next_skill_id, "from_stage": self.stage_id, "to_stage": next_stage_id},
            )
        if self.hold_topic:
            self._mux_select(self.hold_topic)
            self._publish_hold_for(self.pre_switch_hover_s)

        self._exit_stage(self.stage_id)
        self.stage_id = next_stage_id
        self.stage_enter_t = time.time()
        self.active_entity_id = self.plan.stages[self.stage_id].primary_targets[0]
        self.bb.reset_entity(self.active_entity_id)
        self._current_recovery = None
        self._nav_goal_state = None
        self._enter_stage(self.stage_id)

    def _enter_stage(self, stage_id: str):
        now = time.time()
        st = self.plan.stages[stage_id]
        eid = st.primary_targets[0]
        ent = self.plan.entities[eid]
        prompt = ent.prompt or ""

        self.prompt_ctl.set_prompt(prompt)
        self._record_milestone("prompt_written", now, {"entity_id": eid})

        self._req_id_counter += 1
        self.current_req_id = self._req_id_counter
        self.expected_primary_prompt = _norm_prompt(prompt)

        cue_reqs: List[Dict[str, Any]] = []
        cue_prompts: List[str] = []
        for ceid in self._ordered_cue_targets(stage_id):
            ce = self.plan.entities.get(ceid)
            if ce and ce.prompt:
                cue_reqs.append({"entity_id": ceid, "prompt": ce.prompt})
                cue_prompts.append(ce.prompt)

        if self.perception_request_path:
            try:
                extra = {"intent": st.intent, "budget": st.budget or {}, "policy": st.policy or {}}
                extra.update(self._semantic_exploration_extra(stage_id))
                _atomic_write_json(
                    self.perception_request_path,
                    {
                        "req_id": self.current_req_id,
                        "t_wall": now,
                        "stage_id": stage_id,
                        "primary": {"entity_id": eid, "prompt": prompt},
                        "cues": cue_reqs,
                        "extra": extra,
                    },
                )
                self._record_milestone("request_written", now, {"req_id": self.current_req_id})
            except Exception as e:
                print(f"[PerceptionRequest][WARN] failed to write: {e}")

        if self.emit_cues_txt and self.cues_txt_path:
            try:
                _atomic_write_lines(self.cues_txt_path, cue_prompts)
            except Exception as e:
                print(f"[PerceptionRequest][WARN] failed to write cues.txt: {e}")

        stage_state = self.world_state.begin_stage(stage_id, st.intent, now)
        stage_state.retry_count = self._stage_retry_counts.get(stage_id, 0)
        stage_state.current_skill_id = self._stage_skill_id(stage_id)
        self._record_milestone("stage_entered", now, {"intent": st.intent, "entity_id": eid})
        self._emit_runtime_event("stage_enter", now=now, details={"intent": st.intent, "entity_id": eid, "req_id": self.current_req_id})

        print(f"[StageEnter] {stage_id} intent={st.intent} entity={eid} prompt={prompt!r} req_id={self.current_req_id}")

        ad = self.adapters.get(st.intent)
        if ad:
            ad.enter(self._make_stage_ctx(stage_id))
            self.world_state.set_stage_skill(stage_id, self._stage_skill_id(stage_id), "running")
            self._record_milestone("skill_entered", now, {"intent": st.intent})
            self._emit_runtime_event(
                "skill_invoked",
                now=now,
                details={"skill_id": self._stage_skill_id(stage_id), "intent": st.intent, "stage_id": stage_id},
            )

        self._select_intent_source(st.intent)

        if st.intent == "NAVIGATE":
            goal = self._resolve_goal_for_entity(eid)
            if not goal:
                print(f"[NAVIGATE][WARN] no goal found for entity {eid}. Add it in config.entity_goals or entity.extra.goal")
                self._emit_diag(code=DiagnosticCode.NAV_NO_GOAL, message=f"no goal found for entity {eid}", now=now, severity="error")
            else:
                ok_goal = self._pub_goal_pose_stamped(
                    goal,
                    self.ego_goal_topic,
                    retries=5,
                    interval_s=3.0,
                    settle_s=0.5,
                    check_topic=False,
                )
                ok_trig = self._pub_goal_pose_stamped(
                    goal,
                    "/traj_start_trigger",
                    retries=5,
                    interval_s=3.0,
                    settle_s=0.0,
                    check_topic=False,
                )
                if ok_goal:
                    self._record_milestone("goal_published", time.time(), {"entity_id": eid, "goal": goal})
                    self._set_navigation_goal_state(eid, goal, time.time())
                    print(f"[NAVIGATE] published goal for {eid} to {self.ego_goal_topic}: {goal}")
                else:
                    self._emit_diag(code=DiagnosticCode.NAV_NO_GOAL, message=f"failed to publish goal for {eid}", now=time.time(), severity="error")
                    print(f"[NAVIGATE][WARN] failed to publish goal for {eid} to {self.ego_goal_topic}")
                if ok_trig:
                    print(f"[NAVIGATE] published start trigger to /traj_start_trigger for {eid}")
                else:
                    print(f"[NAVIGATE][WARN] failed to publish start trigger to /traj_start_trigger for {eid}")
        elif st.intent == "OBSERVE":
            self._start_observe_stage(stage_id, eid, now)
        elif st.intent == "TRACK":
            self._start_track_stage(stage_id, eid, now)

    def _exit_stage(self, stage_id: str):
        st = self.plan.stages[stage_id]
        skill_id = self._stage_skill_id(stage_id)
        self._emit_runtime_event("stage_exit", now=time.time(), details={"intent": st.intent})
        ad = self.adapters.get(st.intent)
        if ad:
            ad.exit(self._make_stage_ctx(stage_id))
        self.world_state.set_stage_skill(stage_id, skill_id, "stopped")
        if skill_id:
            self._emit_runtime_event("skill_stopped", now=time.time(), details={"skill_id": skill_id, "intent": st.intent, "stage_id": stage_id})
        if st.intent == "OBSERVE":
            self._observe_state = None
        if st.intent == "TRACK":
            self._track_state = None
        print(f"[StageExit] {stage_id}")
