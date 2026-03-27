#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import yaml

from central_runtime.plan_loader import load_plan
from central_runtime.infer_reader import InferJsonReader
from central_runtime.localization_reader import TargetLocalizationReader
from central_runtime.prompt_controller import PromptController
from central_runtime.executor import PlanExecutor
from central_runtime.world_state import WorldState
from central_runtime.progress import ProgressTracker
from central_runtime.diagnostics import DiagnosticsEngine
from central_runtime.recovery import RecoveryManager
from central_runtime.runtime_events import RuntimeEventLogger
from central_runtime.skills.builtin import (
    fast_proposal_contract,
    hold_observe_contract,
    navigate_contract,
    observe_contract,
    search_contract,
    semantic_verify_contract,
    track_dynamic_contract,
)
from central_runtime.skills.registry import SkillRegistry
from central_runtime.verifiers import ConsistencyVerifier, NavigationVerifier, PerceptionVerifier
from central_runtime.reasoner import (
    LLMRuntimeReasoner,
    NoopRuntimeReasoner,
    ReasonerGuard,
    StateSummarizer,
)
from central_runtime.semantic_verifier import APISemanticVerifier, NoopSemanticVerifier

from central_runtime.adapters.ego_navigate import EgoNavigateAdapter
from central_runtime.adapters.falcon_search import FalconSearchAdapter
from central_runtime.adapters.docker_roslaunch import DockerRoslaunchAdapter


def _as_path(shared_dir: str, p: str | None, default_name: str) -> str:
    """
    Allow config to provide either:
      - relative path: "infer.json" -> join(shared_dir, ...)
      - absolute path: "/home/.../infer.json" -> use as-is
    """
    if not p:
        p = default_name
    if os.path.isabs(p):
        return p
    return os.path.join(shared_dir, p)


def _as_cmd_list(cmd):
    """
    Allow cmd to be either:
      - list: ["roslaunch", ...]
      - string: "roslaunch ..."  (will split by whitespace)
    """
    if cmd is None:
        return None
    if isinstance(cmd, list):
        return cmd
    if isinstance(cmd, str):
        s = cmd.strip()
        return s.split() if s else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="path to plan.json")
    ap.add_argument("--config", required=True, help="path to config.yaml")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    shared_dir = cfg.get("shared_dir", "/shared")

    # --- inputs/outputs in /shared ---
    infer_json = _as_path(shared_dir, cfg.get("infer_json", "infer.json"), "infer.json")
    infer_cue_json = _as_path(shared_dir, cfg.get("infer_cue_json", "infer_cue.json"), "infer_cue.json")
    target_localization_json = _as_path(
        shared_dir,
        cfg.get("target_localization_json", "target_localization.json"),
        "target_localization.json",
    )
    prompt_txt = _as_path(shared_dir, cfg.get("prompt_txt", "prompt.txt"), "prompt.txt")
    log_path = _as_path(shared_dir, cfg.get("log_jsonl_path", "plan_runtime.jsonl"), "plan_runtime.jsonl")
    event_log_path = _as_path(shared_dir, cfg.get("event_jsonl_path", "runtime_events.jsonl"), "runtime_events.jsonl")
    frame_path = _as_path(shared_dir, cfg.get("frame_path", "frame.jpg"), "frame.jpg")

    # --- perception control plane outputs ---
    perception_request_json = _as_path(
        shared_dir,
        cfg.get("perception_request_json", "perception_request.json"),
        "perception_request.json",
    )
    emit_cues_txt = bool(cfg.get("emit_cues_txt", True))
    cues_txt_path = _as_path(shared_dir, cfg.get("cues_txt", "cues.txt"), "cues.txt")

    # --- runtime params ---
    tick_hz = float(cfg.get("tick_hz", 10.0))
    stage_settle_s = float(cfg.get("stage_settle_s", 0.5))
    verified_cfg = cfg.get("verified", {}) or {}
    mux_cfg = cfg.get("mux", {}) or {}

    # --- load plan and build IO helpers ---
    plan = load_plan(args.plan, intent_alias=cfg.get("intent_alias"))
    infer_reader = InferJsonReader(infer_json)
    cue_reader = InferJsonReader(infer_cue_json)
    localization_reader = TargetLocalizationReader(target_localization_json)
    prompt_ctl = PromptController(prompt_txt)
    world_cfg = cfg.get("world_state", {}) or {}
    world_state = WorldState(
        obs_ttl_s=float(world_cfg.get("obs_ttl_s", 1.0)),
        cue_ttl_s=float(world_cfg.get("cue_ttl_s", 2.0)),
        max_recent_evidence=int(world_cfg.get("max_recent_evidence", 20)),
    )
    progress = ProgressTracker()
    diagnostics = DiagnosticsEngine(max_events=int((cfg.get("diagnostics", {}) or {}).get("max_events", 50)))
    recovery = RecoveryManager(**(cfg.get("recovery", {}) or {}))
    event_logger = RuntimeEventLogger(event_log_path)
    verification_cfg = cfg.get("verification", {}) or {}
    verifiers = [
        PerceptionVerifier(verification_cfg),
        NavigationVerifier(),
        ConsistencyVerifier(),
    ]
    reasoner_cfg = cfg.get("reasoner", {}) or {}
    summarizer = StateSummarizer(max_recent_events=int(reasoner_cfg.get("max_recent_events", 20)))
    backend = str(reasoner_cfg.get("backend", "noop")).strip().lower()
    if bool(reasoner_cfg.get("enabled", False)) and backend == "llm_api":
        reasoner = LLMRuntimeReasoner(
            base_url=str(reasoner_cfg.get("base_url", "https://api.openai.com/v1/chat/completions")),
            model=str(reasoner_cfg.get("model", "")),
            api_key_env=str(reasoner_cfg.get("api_key_env", "OPENAI_API_KEY")),
            timeout_s=float(reasoner_cfg.get("timeout_s", 20.0)),
            temperature=float(reasoner_cfg.get("temperature", 0.1)),
            system_prompt=reasoner_cfg.get("system_prompt"),
        )
    else:
        reasoner = NoopRuntimeReasoner()
    reasoner_guard = ReasonerGuard(
        allow_actions=list(reasoner_cfg.get("allow_actions", [])),
        auto_apply_actions=list(reasoner_cfg.get("auto_apply_actions", [])),
        allow_insert_verify_stage=bool(reasoner_cfg.get("allow_insert_verify_stage", False)),
        allow_unknown_target_stage=bool(reasoner_cfg.get("allow_unknown_target_stage", False)),
    )
    semantic_cfg = cfg.get("semantic_verifier", {}) or {}
    semantic_backend = str(semantic_cfg.get("backend", "noop")).strip().lower()
    if bool(semantic_cfg.get("enabled", False)) and semantic_backend == "api":
        semantic_verifier = APISemanticVerifier(
            base_url=str(semantic_cfg.get("base_url", "https://api.openai.com/v1/chat/completions")),
            model=str(semantic_cfg.get("model", "")),
            api_key_env=str(semantic_cfg.get("api_key_env", "OPENAI_API_KEY")),
            timeout_s=float(semantic_cfg.get("timeout_s", 20.0)),
            temperature=float(semantic_cfg.get("temperature", 0.1)),
            include_image=bool(semantic_cfg.get("include_image", False)),
            system_prompt=semantic_cfg.get("system_prompt"),
        )
    else:
        semantic_verifier = NoopSemanticVerifier()
    skill_registry = SkillRegistry()
    skill_registry.register(search_contract())
    skill_registry.register(navigate_contract())
    skill_registry.register(observe_contract())
    skill_registry.register(track_dynamic_contract())
    skill_registry.register(hold_observe_contract())
    skill_registry.register(fast_proposal_contract())
    skill_registry.register(semantic_verify_contract())

    # --- Build adapters ---
    adapters: dict[str, object] = {}
    a_cfg = cfg.get("adapters", {}) or {}

    # SEARCH (FALCON)
    search_cfg = a_cfg.get("SEARCH", {}) or {}
    search_cmd = _as_cmd_list(search_cfg.get("cmd"))
    search_container = search_cfg.get("container")  # e.g., "falcon_noetic"

    if search_cmd:
        if search_container:
            adapters["SEARCH"] = DockerRoslaunchAdapter(
                container=search_container,
                cmd=search_cmd,
                pidfile_in_shared=search_cfg.get("pidfile", "/shared/falcon_search.pid"),
                logfile_in_shared=search_cfg.get("logfile", "/shared/falcon_search.log"),
                stop_fallback_pattern=search_cfg.get(
                    "stop_pattern",
                    # keep reasonably specific so we don't kill unrelated roslaunch
                    "roslaunch exploration_manager exploration.launch",
                ),
            )
        else:
            adapters["SEARCH"] = FalconSearchAdapter(cmd=search_cmd)

    # NAVIGATE (EGO)
    nav_cfg = a_cfg.get("NAVIGATE", {}) or {}
    nav_container = nav_cfg.get("container", "ego_noetic")
    nav_launch_cmd = _as_cmd_list(nav_cfg.get("cmd"))
    if not nav_launch_cmd:
        # fallback (you will set it in config)
        nav_launch_cmd = ["roslaunch", "ego_planner", "simple_run.launch"]

    # IMPORTANT: make executor goal topic consistent with NAVIGATE adapter goal_topic
    goal_topic = nav_cfg.get("goal_topic", cfg.get("ego_goal_topic", "/move_base_simple/goal"))

    adapters["NAVIGATE"] = EgoNavigateAdapter(
        container=nav_container,
        launch_cmd=nav_launch_cmd,
        pidfile_in_shared=nav_cfg.get("pidfile", "/shared/ego_nav.pid"),
        logfile_in_shared=nav_cfg.get("logfile", "/shared/ego_nav.log"),
        stop_fallback_pattern=nav_cfg.get("stop_pattern", "roslaunch ego_planner"),
        goal_topic=goal_topic,
        goal_frame=nav_cfg.get("goal_frame", "map"),
        goal_key=nav_cfg.get("goal_key", "goal_xyz"),
        default_goal_xyz=nav_cfg.get("default_goal_xyz", [0.0, 0.0, 1.0]),
        publish_goal_once=bool(nav_cfg.get("publish_goal_once", False)),
        startup_sleep_s=float(nav_cfg.get("startup_sleep_s", 0.5)),
    )

    observe_cfg = a_cfg.get("OBSERVE", {}) or {}
    observe_container = observe_cfg.get("container", nav_container)
    observe_launch_cmd = _as_cmd_list(observe_cfg.get("cmd")) or list(nav_launch_cmd)
    observe_goal_topic = observe_cfg.get("goal_topic", goal_topic)
    adapters["OBSERVE"] = EgoNavigateAdapter(
        container=observe_container,
        launch_cmd=observe_launch_cmd,
        pidfile_in_shared=observe_cfg.get("pidfile", nav_cfg.get("pidfile", "/shared/ego_nav.pid")),
        logfile_in_shared=observe_cfg.get("logfile", nav_cfg.get("logfile", "/shared/ego_nav.log")),
        stop_fallback_pattern=observe_cfg.get("stop_pattern", nav_cfg.get("stop_pattern", "roslaunch ego_planner")),
        goal_topic=observe_goal_topic,
        goal_frame=observe_cfg.get("goal_frame", nav_cfg.get("goal_frame", "map")),
        goal_key=observe_cfg.get("goal_key", nav_cfg.get("goal_key", "goal_xyz")),
        default_goal_xyz=observe_cfg.get("default_goal_xyz", nav_cfg.get("default_goal_xyz", [0.0, 0.0, 1.0])),
        publish_goal_once=bool(observe_cfg.get("publish_goal_once", False)),
        startup_sleep_s=float(observe_cfg.get("startup_sleep_s", nav_cfg.get("startup_sleep_s", 0.5))),
    )

    track_cfg = a_cfg.get("TRACK", {}) or {}
    track_container = track_cfg.get("container", nav_container)
    track_launch_cmd = _as_cmd_list(track_cfg.get("cmd")) or list(nav_launch_cmd)
    track_goal_topic = track_cfg.get("goal_topic", goal_topic)
    adapters["TRACK"] = EgoNavigateAdapter(
        container=track_container,
        launch_cmd=track_launch_cmd,
        pidfile_in_shared=track_cfg.get("pidfile", nav_cfg.get("pidfile", "/shared/ego_nav.pid")),
        logfile_in_shared=track_cfg.get("logfile", nav_cfg.get("logfile", "/shared/ego_nav.log")),
        stop_fallback_pattern=track_cfg.get("stop_pattern", nav_cfg.get("stop_pattern", "roslaunch ego_planner")),
        goal_topic=track_goal_topic,
        goal_frame=track_cfg.get("goal_frame", nav_cfg.get("goal_frame", "map")),
        goal_key=track_cfg.get("goal_key", nav_cfg.get("goal_key", "goal_xyz")),
        default_goal_xyz=track_cfg.get("default_goal_xyz", nav_cfg.get("default_goal_xyz", [0.0, 0.0, 1.0])),
        publish_goal_once=bool(track_cfg.get("publish_goal_once", False)),
        startup_sleep_s=float(track_cfg.get("startup_sleep_s", nav_cfg.get("startup_sleep_s", 0.5))),
    )

    # --- Executor (NEW interface; no request_writer/cues_writer) ---
    ex = PlanExecutor(
        plan=plan,
        infer_reader=infer_reader,
        cue_reader=cue_reader,
        localization_reader=localization_reader,
        prompt_ctl=prompt_ctl,
        adapters=adapters,
        world_state=world_state,
        progress=progress,
        diagnostics=diagnostics,
        recovery=recovery,
        event_logger=event_logger,
        skill_registry=skill_registry,
        verifiers=verifiers,
        reasoner=reasoner,
        reasoner_guard=reasoner_guard,
        semantic_verifier=semantic_verifier,
        summarizer=summarizer,
        tick_hz=tick_hz,
        log_jsonl_path=log_path,
        verified_cfg=verified_cfg,
        verification_cfg=verification_cfg,
        reasoner_cfg=reasoner_cfg,
        semantic_cfg=semantic_cfg,
        stage_settle_s=stage_settle_s,
        frame_path=frame_path,

        # perception control plane
        perception_request_path=perception_request_json,
        emit_cues_txt=emit_cues_txt,
        cues_txt_path=cues_txt_path,

        # NAVIGATE goals
        entity_goals=cfg.get("entity_goals", {}) or {},
        ego_goal_topic=goal_topic,

        # mux / hover
        mux_cfg=mux_cfg,
    )
    ex.run()


if __name__ == "__main__":
    main()
