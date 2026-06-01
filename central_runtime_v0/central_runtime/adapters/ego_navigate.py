from __future__ import annotations
import shlex
import subprocess
import time
from typing import Any, Dict, Optional, List
from .transport import parse_endpoint, wrap_shell_command

def _fmt_pose_stamped_yaml(frame_id: str, x: float, y: float, z: float, yaw: Optional[float] = None) -> str:
    """
    Build a minimal PoseStamped yaml for rostopic pub.
    """
    return (
        "{header: {frame_id: '" + frame_id + "'}, "
        "pose: {position: {x: " + f"{x:.3f}" + ", y: " + f"{y:.3f}" + ", z: " + f"{z:.3f}" + "}, "
        "orientation: {w: 1.0}}}"
    )

class EgoNavigateAdapter:
    """
    NAVIGATE tool adapter:
    - Starts EGO planner via Docker (local) OR SSH (remote).
    - Publishes a goal once (rostopic pub -1).
    - Stops EGO on exit (pidfile/pkill fallback).
    
    Refactored to match DockerRoslaunchAdapter pattern.
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
        ssh_extra_args: Optional[List[str]] = None,
        shell_prelude: Optional[str] = None,
    ):
        self.container = container
        self.launch_cmd = launch_cmd
        self.pidfile = pidfile_in_shared
        self.logfile = logfile_in_shared
        self.pattern = stop_fallback_pattern  # 统一命名为 pattern 以匹配习惯

        self.goal_topic = goal_topic
        self.goal_frame = goal_frame
        self.goal_key = goal_key
        self.default_goal_xyz = default_goal_xyz or [0.0, 0.0, 1.0]

        self.publish_goal_once = publish_goal_once
        self.startup_sleep_s = float(startup_sleep_s)
        self.ssh_extra_args = ssh_extra_args or []
        self.shell_prelude = shell_prelude

        # Parse target immediately
        endpoint = parse_endpoint(container)
        self.mode = endpoint.mode
        self.target = endpoint.target
        self.port = endpoint.port
        self.identity = endpoint.identity

    # -------------------------------------------------------------------------
    # Backends (完全参考 DockerRoslaunchAdapter)
    # -------------------------------------------------------------------------
    def _docker(self, args: List[str], detach: bool = False) -> None:
        base = ["docker", "exec"]
        if detach:
            base.append("-d")
        base.append(self.target)  # container name
        base.extend(args)
        try:
            subprocess.run(base, check=False, timeout=15.0)
        except subprocess.TimeoutExpired:
            print(f"[Adapter][SSH][WARN] command timed out in {self.target}: {' '.join(args)}")

    def _ssh(self, args: List[str], detach: bool = False) -> None:
        # ssh key 模式：BatchMode=yes（不允许交互）
        # sshpass 模式：不能 BatchMode=yes，否则不会提示密码，sshpass也没机会喂密码
        base = ["ssh", "-n", "-o", "StrictHostKeyChecking=accept-new"]
        if detach:
            base.append("-f")

        if self.mode == "ssh":
            base += ["-o", "BatchMode=yes"]

        if self.identity:
            base += ["-i", self.identity]
        if self.port:
            base += ["-p", str(self.port)]
        if self.ssh_extra_args:
            base += self.ssh_extra_args

        base.append(self.target)

        # 🔥🔥🔥【核心修改】🔥🔥🔥
        # 必须对 args 里的每一个参数进行 quote (加引号)
        base.extend([shlex.quote(a) for a in args])  

        if self.mode == "sshpass":
            # 使用环境变量 SSHPASS
            base = ["sshpass", "-e"] + base

        subprocess.run(base, check=False)

    def _run_shell(self, shell_cmd: str, detach: bool = False) -> None:
        wrapped = wrap_shell_command(shell_cmd, self.shell_prelude)
        self._run(["bash", "-lc", wrapped], detach=detach)

    def _run(self, args: List[str], detach: bool = False) -> None:
        if self.mode == "docker":
            self._docker(args, detach=detach)
        else:
            self._ssh(args, detach=detach)

    # -------------------------------------------------------------------------
    # Adapter Lifecycle
    # -------------------------------------------------------------------------

    def enter(self, ctx: Dict[str, Any]) -> None:
        # 1) start EGO roslaunch
        cmd_str = " ".join(shlex.quote(x) for x in self.launch_cmd)
        shell_cmd = (
            f"nohup {cmd_str} < /dev/null > {shlex.quote(self.logfile)} 2>&1 & "
            f"echo $! > {shlex.quote(self.pidfile)}"
        )
        self._run_shell(shell_cmd, detach=True)
        print(f"[Adapter][NAVIGATE][ego][{self.mode}] started in {self.target}: {cmd_str}")

        # 2) publish goal once (configurable)
        if self.publish_goal_once:
            time.sleep(self.startup_sleep_s)
            x, y, z = self._resolve_goal_xyz(ctx)
            msg = _fmt_pose_stamped_yaml(self.goal_frame, x, y, z)

            pub_cmd = f"rostopic pub -1 {shlex.quote(self.goal_topic)} geometry_msgs/PoseStamped \"{msg}\""
            # 发送 goal 不需要 detach，等它发完就行
            self._run_shell(pub_cmd, detach=False)
            print(f"[Adapter][NAVIGATE][ego] goal published to {self.goal_topic}: [{x:.2f}, {y:.2f}, {z:.2f}]")
    
    def exit(self, ctx: Dict[str, Any]) -> None:
        # Stop by pidfile if present; fallback to pkill -f pattern
        # 这里的 Shell 脚本逻辑和 DockerRoslaunchAdapter 保持完全一致
        shell_cmd = (
            f"if [ -f {shlex.quote(self.pidfile)} ]; then "
            f"  PID=$(cat {shlex.quote(self.pidfile)}); "
            f"  kill -INT $PID 2>/dev/null || true; "
            f"  sleep 1; "
            f"  kill -TERM $PID 2>/dev/null || true; "
            f"  rm -f {shlex.quote(self.pidfile)}; "
            f"fi; "
            f"pkill -f {shlex.quote(self.pattern)} 2>/dev/null || true"
        )
        self._run_shell(shell_cmd, detach=False)
        print(f"[Adapter][NAVIGATE][ego][{self.mode}] stopped in {self.target}")

    def _resolve_goal_xyz(self, ctx: Dict[str, Any]):
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
        return
