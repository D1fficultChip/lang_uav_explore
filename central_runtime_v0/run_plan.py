#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import yaml

from central_runtime.plan_loader import load_plan
from central_runtime.infer_reader import InferJsonReader
from central_runtime.prompt_controller import PromptController
from central_runtime.executor import PlanExecutor

from central_runtime.adapters.ego_navigate import EgoNavigateAdapter
from central_runtime.adapters.falcon_search import FalconSearchAdapter
from central_runtime.adapters.docker_roslaunch import DockerRoslaunchAdapter
from central_runtime.adapters.track_hold import TrackHoldAdapter


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
    prompt_txt = _as_path(shared_dir, cfg.get("prompt_txt", "prompt.txt"), "prompt.txt")
    log_path = _as_path(shared_dir, cfg.get("log_jsonl_path", "plan_runtime.jsonl"), "plan_runtime.jsonl")

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
    plan = load_plan(args.plan)
    infer_reader = InferJsonReader(infer_json)
    prompt_ctl = PromptController(prompt_txt)

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

    # TRACK (placeholder HOLD)
    adapters["TRACK"] = TrackHoldAdapter()

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

    # --- Executor (NEW interface; no request_writer/cues_writer) ---
    ex = PlanExecutor(
        plan=plan,
        infer_reader=infer_reader,
        prompt_ctl=prompt_ctl,
        adapters=adapters,
        tick_hz=tick_hz,
        log_jsonl_path=log_path,
        verified_cfg=verified_cfg,
        stage_settle_s=stage_settle_s,

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
