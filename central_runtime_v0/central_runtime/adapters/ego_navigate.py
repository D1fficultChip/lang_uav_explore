from __future__ import annotations
import os
import shlex
import subprocess
import time
from typing import Any, Dict, Optional, List

def _run(cmd: List[str], detach: bool = False) -> None:
    """
    Run command on host. If detach=True, start process and return immediately.
    """
    if detach:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    subprocess.run(cmd, check=False)

def _docker_exec(container: str, bash_cmd: str, detach: bool = False) -> None:
    """
    docker exec [-d] <container> bash -lc "<bash_cmd>"
    """
    base = ["docker", "exec"]
    if detach:
        base.append("-d")
    base += [container, "bash", "-lc", bash_cmd]
    _run(base, detach=False)

def _fmt_pose_stamped_yaml(frame_id: str, x: float, y: float, z: float, yaw: Optional[float] = None) -> str:
    """
    Build a minimal PoseStamped yaml for rostopic pub.
    We keep orientation w=1 (no yaw) by default.
    If yaw is provided, you can extend to quaternion later.
    """
    # Minimal, safe formatting
    # NOTE: using w=1 avoids quaternion math; you can upgrade later.
    return (
        "{header: {frame_id: '" + frame_id + "'}, "
        "pose: {position: {x: " + f"{x:.3f}" + ", y: " + f"{y:.3f}" + ", z: " + f"{z:.3f}" + "}, "
        "orientation: {w: 1.0}}}"
    )

class EgoNavigateAdapter:
    """
    NAVIGATE tool adapter:
    - Starts EGO planner in ego_noetic container (roslaunch ...)
    - Publishes a goal once (rostopic pub -1)
    - Stops EGO on exit (pidfile/pkill fallback)
    """

    def __init__(
        self,
        *,
        container: str,
        launch_cmd: List[str],
        pidfile_in_shared: str = "/shared/ego_nav.pid",
        logfile_in_shared: str = "/shared/ego_nav.log",
        stop_fallback_pattern: str = "roslaunch",
        goal_topic: str = "/move_base_simple/goal",
        goal_frame: str = "world",
        goal_key: str = "goal_xyz",
        default_goal_xyz: Optional[List[float]] = None,
        publish_goal_once: bool = True,
        startup_sleep_s: float = 0.5,
    ):
        self.container = container
        self.launch_cmd = launch_cmd
        self.pidfile = pidfile_in_shared
        self.logfile = logfile_in_shared
        self.stop_pattern = stop_fallback_pattern

        self.goal_topic = goal_topic
        self.goal_frame = goal_frame
        self.goal_key = goal_key
        self.default_goal_xyz = default_goal_xyz or [0.0, 0.0, 1.0]

        self.publish_goal_once = publish_goal_once
        self.startup_sleep_s = float(startup_sleep_s)

    def enter(self, ctx: Dict[str, Any]) -> None:
        # 1) start EGO roslaunch inside container
        # We use nohup + PID file in /shared so host can stop it reliably.
        launch_str = " ".join(shlex.quote(x) for x in self.launch_cmd)
        bash = (
            "source /opt/ros/noetic/setup.bash && "
            "source /root/ego_ws/devel/setup.bash && "
            f"nohup {launch_str} > {shlex.quote(self.logfile)} 2>&1 & "
            "echo $! > " + shlex.quote(self.pidfile)
        )
        _docker_exec(self.container, bash, detach=True)
        print(f"[Adapter][NAVIGATE][ego] started in {self.container}: {launch_str}")

        # 2) publish goal once (configurable)
        if self.publish_goal_once:
            time.sleep(self.startup_sleep_s)
            x, y, z = self._resolve_goal_xyz(ctx)
            msg = _fmt_pose_stamped_yaml(self.goal_frame, x, y, z)

            pub_cmd = (
                "source /opt/ros/noetic/setup.bash && "
                "source /root/ego_ws/devel/setup.bash && "
                f"rostopic pub -1 {shlex.quote(self.goal_topic)} geometry_msgs/PoseStamped \"{msg}\""
            )
            _docker_exec(self.container, pub_cmd, detach=False)
            print(f"[Adapter][NAVIGATE][ego] goal published to {self.goal_topic}: [{x:.2f}, {y:.2f}, {z:.2f}] frame={self.goal_frame}")
    
    def exit(self, ctx: Dict[str, Any]) -> None:
        # Try PID stop first
        bash = (
            "if [ -f " + shlex.quote(self.pidfile) + " ]; then "
            "PID=$(cat " + shlex.quote(self.pidfile) + "); "
            "kill -INT $PID 2>/dev/null || true; sleep 0.5; "
            "kill -TERM $PID 2>/dev/null || true; "
            "rm -f " + shlex.quote(self.pidfile) + "; "
            "fi; "
            f"pkill -f {shlex.quote(self.stop_pattern)} 2>/dev/null || true"
        )
        _docker_exec(self.container, bash, detach=False)
        print(f"[Adapter][NAVIGATE][ego] stopped in {self.container}")

    def _resolve_goal_xyz(self, ctx: Dict[str, Any]):
        # Priority:
        # 1) stage policy goal_xyz
        # 2) entity.extra goal_xyz
        # 3) default_goal_xyz
        policy = ctx.get("policy") or {}
        if isinstance(policy, dict) and self.goal_key in policy:
            g = policy.get(self.goal_key)
            if isinstance(g, (list, tuple)) and len(g) >= 3:
                return float(g[0]), float(g[1]), float(g[2])

        ent = ctx.get("entity") or {}
        extra = ent.get("extra") if isinstance(ent, dict) else None
        if isinstance(extra, dict) and self.goal_key in extra:
            g = extra.get(self.goal_key)
            if isinstance(g, (list, tuple)) and len(g) >= 3:
                return float(g[0]), float(g[1]), float(g[2])

        g = self.default_goal_xyz
        return float(g[0]), float(g[1]), float(g[2])
    
    def tick(self, ctx):
    # NAVIGATE 阶段目前不用每tick做事，先占位
        return
