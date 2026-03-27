from __future__ import annotations
import shlex
import subprocess
import time
import re
from typing import Any, Dict, Optional, List

# -----------------------------------------------------------------------------
# Helper: Parse target string (复用自 DockerRoslaunchAdapter)
# -----------------------------------------------------------------------------
def _parse_target(container: str):
    s = (container or "").strip()

    if s.startswith("sshpass:"):
        s = s[len("sshpass:"):]
        mode = "sshpass"
    elif s.startswith("ssh://"):
        s = s[len("ssh://"):]
        mode = "ssh"
    elif s.startswith("ssh:"):
        s = s[len("ssh:"):]
        mode = "ssh"
    else:
        return ("docker", s, None, None)

    identity = None
    # allow query style ?i=/path
    if "?" in s:
        s, q = s.split("?", 1)
        m = re.search(r"(?:^|&)i=([^&]+)", q)
        if m:
            identity = m.group(1)

    port = None
    # parse host:port if exists (but not IPv6)
    m = re.match(r"^(?P<who>[^:]+):(?P<port>\d+)$", s)
    if m:
        s = m.group("who")
        port = int(m.group("port"))

    target = s
    return (mode, target, port, identity)

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

        # Parse target immediately
        self.mode, self.target, self.port, self.identity = _parse_target(container)

    # -------------------------------------------------------------------------
    # Backends (完全参考 DockerRoslaunchAdapter)
    # -------------------------------------------------------------------------
    def _docker(self, args: List[str], detach: bool = False) -> None:
        base = ["docker", "exec"]
        if detach:
            base.append("-d")
        base.append(self.target)  # container name
        base.extend(args)
        subprocess.run(base, check=False)

    def _ssh(self, args: List[str]) -> None:
        # ssh key 模式：BatchMode=yes（不允许交互）
        # sshpass 模式：不能 BatchMode=yes，否则不会提示密码，sshpass也没机会喂密码
        base = ["ssh", "-o", "StrictHostKeyChecking=accept-new"]

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

    def _run(self, args: List[str], detach: bool = False) -> None:
        if self.mode == "docker":
            self._docker(args, detach=detach)
        else:
            # ssh: detach handled by nohup in bash wrapper; no need ssh -f
            self._ssh(args)

    # -------------------------------------------------------------------------
    # Adapter Lifecycle
    # -------------------------------------------------------------------------

    def enter(self, ctx: Dict[str, Any]) -> None:
        # 1) start EGO roslaunch
        cmd_str = " ".join(shlex.quote(x) for x in self.launch_cmd)
        
        # 使用 zsh 启动，确保 source 正确
        # 注意：这里假设远程也是 zsh 环境，且 setup 文件兼容 zsh (通常 ROS 的 setup.bash 兼容 zsh，或者有 setup.zsh)
        # 为了稳妥，仍然 source setup.bash，zsh 通常能处理它。如果你确定有 setup.zsh 也可以改。
        bash = (
            "source /opt/ros/noetic/setup.zsh && "
            "source /home/nv/uav_demo/src/ego_ws/devel/setup.zsh && "
            f"nohup {cmd_str} > {shlex.quote(self.logfile)} 2>&1 & "
            f"echo $! > {shlex.quote(self.pidfile)}"
        )
        
        # 按照参考代码，使用 zsh -lc 来执行
        self._run(["zsh", "-lc", bash], detach=True)
        print(f"[Adapter][NAVIGATE][ego][{self.mode}] started in {self.target}: {cmd_str}")

        # 2) publish goal once (configurable)
        if self.publish_goal_once:
            time.sleep(self.startup_sleep_s)
            x, y, z = self._resolve_goal_xyz(ctx)
            msg = _fmt_pose_stamped_yaml(self.goal_frame, x, y, z)

            pub_cmd = (
                "source /opt/ros/noetic/setup.zsh && "
                "source /home/nv/uav_demo/src/ego_ws/devel/setup.zsh && "
                f"rostopic pub -1 {shlex.quote(self.goal_topic)} geometry_msgs/PoseStamped \"{msg}\""
            )
            # 发送 goal 不需要 detach，等它发完就行
            self._run(["zsh", "-lc", pub_cmd], detach=False)
            print(f"[Adapter][NAVIGATE][ego] goal published to {self.goal_topic}: [{x:.2f}, {y:.2f}, {z:.2f}]")
    
    def exit(self, ctx: Dict[str, Any]) -> None:
        # Stop by pidfile if present; fallback to pkill -f pattern
        # 这里的 Shell 脚本逻辑和 DockerRoslaunchAdapter 保持完全一致
        bash = (
            f"if [ -f {shlex.quote(self.pidfile)} ]; then "
            f"  PID=$(cat {shlex.quote(self.pidfile)}); "
            f"  kill -INT $PID 2>/dev/null || true; "
            f"  sleep 1; "
            f"  kill -TERM $PID 2>/dev/null || true; "
            f"  rm -f {shlex.quote(self.pidfile)}; "
            f"fi; "
            f"pkill -f {shlex.quote(self.pattern)} 2>/dev/null || true"
        )
        self._run(["zsh", "-lc", bash], detach=False)
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