from __future__ import annotations
from typing import Any, Dict, List, Optional
import subprocess
import shlex

from .base import Adapter

class DockerRoslaunchAdapter(Adapter):
    """
    Start/stop a roslaunch INSIDE a running container using `docker exec`.
    Robust stop uses a pidfile stored in /shared (shared between host & container).
    """

    def __init__(
        self,
        container: str,
        cmd: List[str],
        pidfile_in_shared: str = "/shared/falcon_search.pid",
        logfile_in_shared: str = "/shared/falcon_search.log",
        stop_fallback_pattern: str = "roslaunch exploration_manager exploration.launch",
    ):
        self.container = container
        self.cmd = cmd
        self.pidfile = pidfile_in_shared
        self.logfile = logfile_in_shared
        self.pattern = stop_fallback_pattern

    def _docker(self, args: List[str], detach: bool = False) -> None:
        base = ["docker", "exec"]
        if detach:
            base.append("-d")
        base.append(self.container)
        base.extend(args)
        subprocess.run(base, check=False)

    def enter(self, stage: Dict[str, Any]) -> None:
        # Start roslaunch in background with nohup, write pidfile
        cmd_str = " ".join(shlex.quote(x) for x in self.cmd)
        bash = (
            f"nohup {cmd_str} > {shlex.quote(self.logfile)} 2>&1 & "
            f"echo $! > {shlex.quote(self.pidfile)}"
        )
        self._docker(["bash", "-lc", bash], detach=True)
        print(f"[Adapter][SEARCH][docker] started in {self.container}: {cmd_str}")

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
        self._docker(["bash", "-lc", bash], detach=False)
        print(f"[Adapter][SEARCH][docker] stopped in {self.container}")
