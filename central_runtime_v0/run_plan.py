#!/usr/bin/env python3
from __future__ import annotations
import argparse
import os
import yaml

from central_runtime.plan_loader import load_plan
from central_runtime.infer_reader import InferJsonReader
from central_runtime.prompt_controller import PromptController
from central_runtime.executor import PlanExecutor

from central_runtime.adapters.falcon_search import FalconSearchAdapter
from central_runtime.adapters.docker_roslaunch import DockerRoslaunchAdapter
from central_runtime.adapters.track_hold import TrackHoldAdapter
from central_runtime.adapters.navigate_stub import NavigateStubAdapter


def _as_path(shared_dir: str, p: str, default_name: str) -> str:
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
        return cmd.strip().split()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="path to plan.json")
    ap.add_argument("--config", required=True, help="path to config.yaml")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    shared_dir = cfg.get("shared_dir", "/shared")
    infer_json = _as_path(shared_dir, cfg.get("infer_json", "infer.json"), "infer.json")
    prompt_txt = _as_path(shared_dir, cfg.get("prompt_txt", "prompt.txt"), "prompt.txt")
    tick_hz = float(cfg.get("tick_hz", 10.0))
    log_path = _as_path(shared_dir, cfg.get("log_jsonl_path", "plan_runtime.jsonl"), "plan_runtime.jsonl")

    plan = load_plan(args.plan)

    infer_reader = InferJsonReader(infer_json)
    prompt_ctl = PromptController(prompt_txt)

    # Build adapters
    adapters = {}
    a_cfg = cfg.get("adapters", {}) or {}

    # --- SEARCH adapter ---
    # If you run central runtime on HOST while Falcon runs in a running docker container,
    # use DockerRoslaunchAdapter by providing:
    #   adapters.SEARCH.container: falcon_noetic
    #   adapters.SEARCH.cmd: ["roslaunch", ...]
    #
    # If you run central runtime inside the same environment as ROS (e.g., inside Falcon container),
    # you can omit `container` and it will use FalconSearchAdapter(cmd=...).
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
                    # keep this reasonably specific so we don't kill unrelated roslaunch
                    "roslaunch exploration_manager exploration.launch"
                ),
            )
        else:
            adapters["SEARCH"] = FalconSearchAdapter(cmd=search_cmd)

    # --- TRACK adapter ---
    adapters["TRACK"] = TrackHoldAdapter()

    # --- NAVIGATE adapter (stub for now; replace later with EgoPlannerAdapter) ---
    adapters["NAVIGATE"] = NavigateStubAdapter()

    verified_cfg = cfg.get("verified", {}) or {}

    ex = PlanExecutor(
        plan=plan,
        infer_reader=infer_reader,
        prompt_ctl=prompt_ctl,
        adapters=adapters,
        tick_hz=tick_hz,
        log_jsonl_path=log_path,
        verified_cfg=verified_cfg,
    )
    ex.run()


if __name__ == "__main__":
    main()
