from __future__ import annotations

from typing import Any, Dict, Optional, List, Tuple
import time
import json
import os
import subprocess
import re
import math
import time
from typing import Any, Dict, Optional, Tuple
from .plan_loader import Plan, Transition
from .blackboard import Blackboard
from .conditions import ConditionEngine
from .infer_reader import InferJsonReader
from .prompt_controller import PromptController
from .adapters.base import Adapter
from .fs_utils import open_append_resilient
from .runtime_events import RuntimeEventLogger


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
    # yaw (Z axis)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _quat_from_yaw(yaw: float) -> Tuple[float, float, float, float]:
    # roll=pitch=0
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class PlanExecutor:
    """
    central_runtime v1:
      - reads plan.json
      - polls infer.json and updates Blackboard
      - evaluates transitions
      - stage enter: set prompt + write perception_request (+ optional cues.txt)
      - stage switching: mux->HOLD, publish HOLD pos_cmd for a short time, then switch to target algorithm source
      - NAVIGATE stage: publish goal PoseStamped (V0 uses config-fixed entity_goals)
    """

    def __init__(
        self,
        plan: Plan,
        infer_reader: InferJsonReader,
        prompt_ctl: PromptController,
        adapters: Dict[str, Adapter],
        tick_hz: float = 10.0,
        log_jsonl_path: Optional[str] = None,
        event_jsonl_path: Optional[str] = None,
        verified_cfg: Optional[Dict[str, Any]] = None,
        stage_settle_s: float = 0.0,
        # perception control plane
        perception_request_path: Optional[str] = None,
        emit_cues_txt: bool = False,
        cues_txt_path: Optional[str] = None,
        # NAVIGATE goals
        entity_goals: Optional[Dict[str, Any]] = None,
        ego_goal_topic: str = "/move_base_simple/goal",
        # mux / hover
        mux_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.plan = plan
        self.infer_reader = infer_reader
        self.prompt_ctl = prompt_ctl
        self.adapters = adapters

        self.tick_dt = 1.0 / max(1e-6, float(tick_hz))
        self.bb = Blackboard()
        self.cond = ConditionEngine(self.bb, verified_cfg=verified_cfg or {})

        self.stage_settle_s = float(stage_settle_s)

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

        self.stage_id: str = self._pick_start_stage()
        self.stage_enter_t: float = time.time()
        self.active_entity_id: str = self.plan.stages[self.stage_id].primary_targets[0]

        self._req_id_counter = 0
        self.current_req_id: Optional[int] = None
        self.expected_primary_prompt: str = ""

        self._log_path = log_jsonl_path
        self._log_fp = open_append_resilient(log_jsonl_path, encoding="utf-8") if log_jsonl_path else None
        self._event_logger = RuntimeEventLogger(event_jsonl_path)

    def close(self):
        if self._log_fp:
            self._log_fp.close()
        self._event_logger.close()

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

    def _emit_event(
        self,
        event_type: str,
        *,
        stage_id: Optional[str] = None,
        intent: Optional[str] = None,
        entity_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        t_wall: Optional[float] = None,
    ) -> None:
        self._event_logger.emit(
            event_type=event_type,
            stage_id=stage_id,
            intent=intent,
            entity_id=entity_id,
            details=details,
            t_wall=t_wall,
        )

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
        """
        Return (x,y,z,yaw) from nav_msgs/Odometry by parsing `rostopic echo -n 1`.
        """
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

        # crude parse
        def _find_float(key: str) -> Optional[float]:
            m = re.search(rf"{key}:\s*([-+0-9.eE]+)", out)
            if not m:
                return None
            try:
                return float(m.group(1))
            except Exception:
                return None

        px = _find_float(r"x")
        py = None
        pz = None

        # position appears multiple times; better target "position:" block
        mpos = re.search(r"position:\s*\n\s*x:\s*([-+0-9.eE]+)\s*\n\s*y:\s*([-+0-9.eE]+)\s*\n\s*z:\s*([-+0-9.eE]+)", out)
        if mpos:
            px = float(mpos.group(1))
            py = float(mpos.group(2))
            pz = float(mpos.group(3))
        else:
            return None

        mori = re.search(r"orientation:\s*\n\s*x:\s*([-+0-9.eE]+)\s*\n\s*y:\s*([-+0-9.eE]+)\s*\n\s*z:\s*([-+0-9.eE]+)\s*\n\s*w:\s*([-+0-9.eE]+)", out)
        if not mori:
            yaw = 0.0
        else:
            qx = float(mori.group(1))
            qy = float(mori.group(2))
            qz = float(mori.group(3))
            qw = float(mori.group(4))
            yaw = _yaw_from_quat(qx, qy, qz, qw)

        return (px, py, pz, yaw)

    def _publish_hold_for(self, seconds: float) -> None:
        """
        Publish hold PositionCommand to hold_topic for 'seconds'.
        Requires:
          - mux has hold_topic in its list
          - quadrotor_msgs/PositionCommand exists in this ROS env
        """
        pose = self._read_odom_pose_once()
        if pose is None:
            return
        x, y, z, yaw = pose

        # publish a few times
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
        if not src:
            return
        self._mux_select(src)

    # ---------------- NAVIGATE goal publish ----------------
    def _resolve_goal_for_entity(self, eid: str) -> Optional[Dict[str, Any]]:
        # priority: entity_goals from config
        g = self.entity_goals.get(eid)
        if isinstance(g, dict):
            return g

        # fallback: entity.extra if exists
        ent = self.plan.entities.get(eid)
        extra = getattr(ent, "extra", None)
        if isinstance(ent, dict):
            extra = ent.get("extra")
        if isinstance(extra, dict):
            gg = extra.get("goal")
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
        """
        Publish geometry_msgs/PoseStamped to `topic` using `rostopic pub -1`,
        retrying multiple times to avoid race conditions (subscriber not ready).

        Returns True if any attempt succeeds (rostopic exit code == 0).
        """
        frame = str(goal.get("frame", "world"))
        x = float(goal.get("x", 0.0))
        y = float(goal.get("y", 0.0))
        z = float(goal.get("z", 1.0))
        yaw_deg = float(goal.get("yaw_deg", 0.0))
        yaw = math.radians(yaw_deg)
        qx, qy, qz, qw = _quat_from_yaw(yaw)

        # IMPORTANT:
        # 1) avoid "stamp: now" (rostopic YAML parsing is picky)
        # 2) use double quotes for frame_id
        msg = (
            '{header: {frame_id: "' + frame + '"}, '
            'pose: {position: {x: ' + str(x) + ', y: ' + str(y) + ', z: ' + str(z) + '}, '
            'orientation: {x: ' + str(qx) + ', y: ' + str(qy) + ', z: ' + str(qz) + ', w: ' + str(qw) + '}}}'
        )

        # Ensure ROS env is sourced (especially when central_runtime runs in a non-ROS shell)
        base_prefix = "source /opt/ros/noetic/setup.bash >/dev/null 2>&1 || true; "

        def _run(cmd: str) -> Tuple[int, str, str]:
            p = subprocess.run(["bash", "-lc", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return p.returncode, p.stdout, p.stderr

        # Optional: quick sanity check (helps catch wrong topic/type/remap)
        if check_topic:
            info_cmd = base_prefix + f"rostopic info {topic} >/dev/null 2>&1"
            rc, _, _ = _run(info_cmd)
            if rc != 0:
                print(f"[NAVIGATE][WARN] rostopic info failed for {topic} (topic may not exist yet). Will still retry publish.")

        if settle_s > 0:
            time.sleep(settle_s)

        pub_cmd = base_prefix + f"rostopic pub -1 {topic} geometry_msgs/PoseStamped '{msg}'"

        ok_any = False
        for i in range(max(1, int(retries))):
            try:
                rc, out, err = _run(pub_cmd)
                if rc == 0:
                    ok_any = True
                    print(f"[NAVIGATE] published PoseStamped to {topic} attempt {i+1}/{retries}")
                else:
                    print(f"[NAVIGATE][WARN] publish PoseStamped failed to {topic} attempt {i+1}/{retries}: {err.strip()}")
            except Exception as e:
                print(f"[NAVIGATE][WARN] publish PoseStamped exception to {topic} attempt {i+1}/{retries}: {e}")

            # wait before next attempt (except after last)
            if i != retries - 1:
                time.sleep(interval_s)

        return ok_any

    # ---------------- core loop ----------------
    def run(self):
        self._enter_stage(self.stage_id)
        try:
            while True:
                self._tick_once()
                time.sleep(self.tick_dt)
        except KeyboardInterrupt:
            print("PlanExecutor interrupted.")
        finally:
            self._exit_stage(self.stage_id)
            self.close()

    def _tick_once(self):
        now = time.time()

        # 1) Poll perception
        obj = self.infer_reader.poll()
        if obj:
            det = self.infer_reader.to_detection(obj)
            if det:
                # ignore stale detections right after stage switch
                if (now - self.stage_enter_t) >= self.stage_settle_s and det.t_wall >= (self.stage_enter_t - 0.01):
                    self.bb.update_detection(self.active_entity_id, det)

        # 1.5) allow adapter tick
        stage = self.plan.stages[self.stage_id]
        ad = self.adapters.get(stage.intent)
        if ad:
            ad.tick(self._make_stage_ctx(self.stage_id))

        # 2) Evaluate outgoing transitions
        outs = self.plan.outgoing(self.stage_id)
        fired: Optional[Transition] = None
        evals = []
        for tr in outs:
            ok = self.cond.eval(tr.when, self.stage_enter_t)
            evals.append({"to": tr.to, "when": tr.when, "ok": ok})
            if ok and fired is None:
                fired = tr

        # 3) log
        latest = self.bb.latest.get(self.active_entity_id)
        snap = {
            "t": now,
            "stage": self.stage_id,
            "intent": stage.intent,
            "active_entity": self.active_entity_id,
            "stage_elapsed": now - self.stage_enter_t,
            "latest_det": None if latest is None else {"found": latest.found, "score": latest.score, "t_wall": latest.t_wall},
            "outgoing": [{"to": e["to"], "ok": e["ok"], "type": e["when"].get("type"), "params": e["when"].get("params", {})} for e in evals],
        }
        self._log(snap)

        # 4) transition
        if fired is not None:
            self._transition_to(fired.to, fired)

    def _make_stage_ctx(self, stage_id: str) -> Dict[str, Any]:
        st = self.plan.stages[stage_id]
        eid = st.primary_targets[0]
        ent = self.plan.entities.get(eid)

        ent_prompt = getattr(ent, "prompt", None)
        ent_extra = getattr(ent, "extra", None)
        if isinstance(ent, dict):
            ent_prompt = ent.get("prompt", ent_prompt)
            ent_extra = ent.get("extra", ent_extra)

        if not isinstance(ent_extra, dict):
            ent_extra = {}

        return {
            "stage_id": stage_id,
            "intent": st.intent,
            "entity_id": eid,
            "entity": {"id": eid, "prompt": ent_prompt, "extra": ent_extra},
            "policy": st.policy,
            "budget": st.budget,
        }

    def _transition_to(self, next_stage_id: str, fired: Transition):
        now = time.time()
        cur_stage = self.plan.stages[self.stage_id]
        print(f"[Transition] {self.stage_id} -> {next_stage_id} because {fired.when.get('type')} {fired.when.get('params')}")
        self._emit_event(
            "transition",
            stage_id=self.stage_id,
            intent=cur_stage.intent,
            entity_id=self.active_entity_id,
            details={
                "next_stage_id": next_stage_id,
                "condition_type": fired.when.get("type"),
                "condition_params": fired.when.get("params", {}),
            },
            t_wall=now,
        )

        # A) switch to HOLD and hover briefly (optional but recommended)
        if self.hold_topic:
            self._mux_select(self.hold_topic)
            self._publish_hold_for(self.pre_switch_hover_s)

        # B) stop old stage
        self._exit_stage(self.stage_id)

        # C) enter new stage
        self.stage_id = next_stage_id
        self.stage_enter_t = time.time()
        self.active_entity_id = self.plan.stages[self.stage_id].primary_targets[0]
        self.bb.reset_entity(self.active_entity_id)
        self._enter_stage(self.stage_id)

    def _enter_stage(self, stage_id: str):
        st = self.plan.stages[stage_id]

        # 1) set prompt for primary target
        eid = st.primary_targets[0]
        ent = self.plan.entities[eid]
        prompt = getattr(ent, "prompt", None)
        if isinstance(ent, dict):
            prompt = ent.get("prompt", prompt)
        prompt = prompt or ""
        self.prompt_ctl.set_prompt(prompt)

        # 2) write perception_request.json (+ optional cues.txt)
        self._req_id_counter += 1
        self.current_req_id = self._req_id_counter
        self.expected_primary_prompt = _norm_prompt(prompt)

        cue_eids = getattr(st, "cue_targets", []) or []
        cue_reqs: List[Dict[str, Any]] = []
        cue_prompts: List[str] = []
        for ceid in cue_eids:
            ce = self.plan.entities.get(ceid)
            cp = getattr(ce, "prompt", None)
            if isinstance(ce, dict):
                cp = ce.get("prompt", cp)
            if cp:
                cue_reqs.append({"entity_id": ceid, "prompt": cp})
                cue_prompts.append(cp)

        if self.perception_request_path:
            try:
                _atomic_write_json(
                    self.perception_request_path,
                    {
                        "req_id": self.current_req_id,
                        "t_wall": time.time(),
                        "stage_id": stage_id,
                        "primary": {"entity_id": eid, "prompt": prompt},
                        "cues": cue_reqs,
                    },
                )
            except Exception as e:
                print(f"[PerceptionRequest][WARN] failed to write: {e}")

        if self.emit_cues_txt and self.cues_txt_path:
            try:
                _atomic_write_lines(self.cues_txt_path, cue_prompts)
            except Exception as e:
                print(f"[PerceptionRequest][WARN] failed to write cues.txt: {e}")

        print(f"[StageEnter] {stage_id} intent={st.intent} entity={eid} prompt={prompt!r} req_id={self.current_req_id}")
        self._emit_event(
            "stage_enter",
            stage_id=stage_id,
            intent=st.intent,
            entity_id=eid,
            details={
                "prompt": prompt,
                "req_id": self.current_req_id,
                "cue_entity_ids": cue_eids,
            },
        )

        # 3) start adapter
        ad = self.adapters.get(st.intent)
        if ad:
            ad.enter(self._make_stage_ctx(stage_id))

        # 4) switch mux to the correct algorithm source for this intent
        self._select_intent_source(st.intent)

        # 5) NAVIGATE: publish goal (V0 uses config entity_goals)
        if st.intent == "NAVIGATE":
            g = self._resolve_goal_for_entity(eid)
            if not g:
                print(f"[NAVIGATE][WARN] no goal found for entity {eid}. Add it in config.entity_goals or entity.extra.goal")
            else:
                # 先发 goal，多次重试，降低竞态丢包概率
                ok_goal = self._pub_goal_pose_stamped(
                    g,
                    self.ego_goal_topic,
                    retries=5,
                    interval_s=3.0,
                    settle_s=0.5,     # 给EGO/waypoint_generator一点起节点时间
                    check_topic=False # 你也可以 True，但有些时候topic尚未出现会报警
                )

                # 再发 trigger（很多EGO版本需要这个才能真正开始规划/跟踪）
                ok_trig = self._pub_goal_pose_stamped(
                    g,
                    "/traj_start_trigger",
                    retries=5,
                    interval_s=3.0,
                    settle_s=0.0,
                    check_topic=False
                )

                if ok_goal:
                    print(f"[NAVIGATE] published goal for {eid} to {self.ego_goal_topic}: {g}")
                    self._emit_event(
                        "goal_published",
                        stage_id=stage_id,
                        intent=st.intent,
                        entity_id=eid,
                        details={"topic": self.ego_goal_topic, "goal": g},
                    )
                else:
                    print(f"[NAVIGATE][WARN] failed to publish goal for {eid} to {self.ego_goal_topic}")
                    self._emit_event(
                        "goal_publish_failed",
                        stage_id=stage_id,
                        intent=st.intent,
                        entity_id=eid,
                        details={"topic": self.ego_goal_topic, "goal": g},
                    )

                if ok_trig:
                    print(f"[NAVIGATE] published start trigger to /traj_start_trigger for {eid}")
                    self._emit_event(
                        "traj_start_trigger_published",
                        stage_id=stage_id,
                        intent=st.intent,
                        entity_id=eid,
                        details={"topic": "/traj_start_trigger", "goal": g},
                    )
                else:
                    print(f"[NAVIGATE][WARN] failed to publish start trigger to /traj_start_trigger for {eid}")
                    self._emit_event(
                        "traj_start_trigger_failed",
                        stage_id=stage_id,
                        intent=st.intent,
                        entity_id=eid,
                        details={"topic": "/traj_start_trigger", "goal": g},
                    )


    def _exit_stage(self, stage_id: str):
        st = self.plan.stages[stage_id]
        ad = self.adapters.get(st.intent)
        if ad:
            ad.exit(self._make_stage_ctx(stage_id))
        self._emit_event(
            "stage_exit",
            stage_id=stage_id,
            intent=st.intent,
            entity_id=st.primary_targets[0] if st.primary_targets else None,
            details={},
        )
        print(f"[StageExit] {stage_id}")
