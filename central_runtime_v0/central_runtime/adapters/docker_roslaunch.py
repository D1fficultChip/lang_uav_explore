from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import subprocess
import shlex
import re

from .base import Adapter


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

    # 下面保持你原来的 query/port/identity 解析不变...


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
    ):
        self.container = container
        self.cmd = cmd
        self.pidfile = pidfile_in_shared
        self.logfile = logfile_in_shared
        self.pattern = stop_fallback_pattern
        self.ssh_extra_args = ssh_extra_args or []

        self.mode, self.target, self.port, self.identity = _parse_target(container)

    # ---------- backends ----------
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
            # 必须对 args 里的每一个参数进行 quote (加引号)，
            # 否则到了远程机器上，复杂的 shell 命令会被拆散，导致 zsh 报错！
            base.extend([shlex.quote(a) for a in args])  

            if self.mode == "sshpass":
                # 使用环境变量 SSHPASS
                # 需要：sudo apt-get install sshpass
                base = ["sshpass", "-e"] + base

            subprocess.run(base, check=False)


    def _run(self, args: List[str], detach: bool = False) -> None:
        if self.mode == "docker":
            self._docker(args, detach=detach)
        else:
            # ssh: detach handled by nohup in bash wrapper; no need ssh -f
            self._ssh(args)

    # ---------- Adapter interface ----------
    def enter(self, stage: Dict[str, Any]) -> None:
        # Start roslaunch in background with nohup, write pidfile
        cmd_str = " ".join(shlex.quote(x) for x in self.cmd)
        bash = (
            f"nohup {cmd_str} > {shlex.quote(self.logfile)} 2>&1 & "
            f"echo $! > {shlex.quote(self.pidfile)}"
        )
        self._run(["zsh", "-lc", bash], detach=True)
        print(f"[Adapter][{stage.get('intent','?')}][{self.mode}] started in {self.container}: {cmd_str}")

    def tick(self, stage: Dict[str, Any]) -> None:
        return

    def exit(self, stage: Dict[str, Any]) -> None:
        # Stop by pidfile if present; fallback to pkill -f pattern
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
        print(f"[Adapter][{stage.get('intent','?')}][{self.mode}] stopped in {self.container}")
