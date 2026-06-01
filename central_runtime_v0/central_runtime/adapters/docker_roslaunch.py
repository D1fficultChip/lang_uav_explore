from __future__ import annotations
from typing import Any, Dict, List, Optional
import subprocess
import shlex

from .base import Adapter
from .transport import parse_endpoint, wrap_shell_command


class DockerRoslaunchAdapter(Adapter):
    """Start/stop a roslaunch either:
      - inside a running Docker container using `docker exec` (legacy sim), OR
      - on a remote onboard machine via SSH (true-robot mode),

    WITHOUT changing higher-level workflow code.

    Switch mode by setting config.adapters.<INTENT>.container to one of:
      - "falcon_noetic"                 -> docker mode
      - "ssh:nv@192.168.1.122"         -> ssh mode
      - "ssh://nv@192.168.1.122:22"    -> ssh mode with port
      - "ssh:nv@192.168.1.122:22?i=/home/you/.ssh/id_rsa" -> ssh + identity

    Notes:
      - cmd is executed as-is. In robot mode, include proper `source .../setup.bash` in cmd.
      - pidfile/logfile paths live on the *target* machine (container or onboard).
    """

    def __init__(
        self,
        container: str,
        cmd: List[str],
        pidfile_in_shared: str = "/shared/falcon_search.pid",
        logfile_in_shared: str = "/shared/falcon_search.log",
        stop_fallback_pattern: str = "roslaunch exploration_manager exploration.launch",
        # ssh options (optional; can be embedded in container string)
        ssh_extra_args: Optional[List[str]] = None,
        shell_prelude: Optional[str] = None,
    ):
        self.container = container
        self.cmd = cmd
        self.pidfile = pidfile_in_shared
        self.logfile = logfile_in_shared
        self.pattern = stop_fallback_pattern
        self.ssh_extra_args = ssh_extra_args or []
        self.shell_prelude = shell_prelude

        endpoint = parse_endpoint(container)
        self.mode = endpoint.mode
        self.target = endpoint.target
        self.port = endpoint.port
        self.identity = endpoint.identity

    def _stage_cmd(self, stage: Dict[str, Any]) -> List[str]:
        cmd = list(self.cmd)
        overrides = stage.get("launch_overrides") or {}
        if not overrides:
            return cmd

        roslaunch_args = [
            f"{str(k)}:={v}"
            for k, v in overrides.items()
            if v is not None
        ]
        if not roslaunch_args:
            return cmd

        if len(cmd) >= 3 and cmd[0] == "bash" and cmd[1] == "-lc":
            suffix = " ".join(shlex.quote(arg) for arg in roslaunch_args)
            cmd[2] = f"{cmd[2]} {suffix}"
            return cmd

        return cmd + roslaunch_args

    # ---------- backends ----------
    def _docker(self, args: List[str], detach: bool = False) -> None:
        base = ["docker", "exec"]
        if detach:
            base.append("-d")
        base.append(self.target)  # container name
        base.extend(args)
        subprocess.run(base, check=False)

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
        base.extend([shlex.quote(a) for a in args])

        if self.mode == "sshpass":
            base = ["sshpass", "-e"] + base

        try:
            subprocess.run(base, check=False, timeout=15.0)
        except subprocess.TimeoutExpired:
            print(f"[Adapter][SSH][WARN] command timed out in {self.target}: {' '.join(args)}")

    def _run_shell(self, shell_cmd: str, detach: bool = False) -> None:
        wrapped = wrap_shell_command(shell_cmd, self.shell_prelude)
        self._run(["bash", "-lc", wrapped], detach=detach)

    def _run(self, args: List[str], detach: bool = False) -> None:
        if self.mode == "docker":
            self._docker(args, detach=detach)
        else:
            self._ssh(args, detach=detach)

    # ---------- Adapter interface ----------
    def enter(self, stage: Dict[str, Any]) -> None:
        # Start roslaunch in background with nohup, write pidfile
        stage_cmd = self._stage_cmd(stage)
        cmd_str = " ".join(shlex.quote(x) for x in stage_cmd)
        shell_cmd = (
            f"nohup {cmd_str} < /dev/null > {shlex.quote(self.logfile)} 2>&1 & "
            f"echo $! > {shlex.quote(self.pidfile)}"
        )
        self._run_shell(shell_cmd, detach=True)
        print(f"[Adapter][{stage.get('intent','?')}][{self.mode}] started in {self.container}: {cmd_str}")

    def tick(self, stage: Dict[str, Any]) -> None:
        return

    def exit(self, stage: Dict[str, Any]) -> None:
        # Stop by pidfile if present; fallback to pkill -f pattern
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
        print(f"[Adapter][{stage.get('intent','?')}][{self.mode}] stopped in {self.container}")
