from __future__ import annotations
from typing import Any, Dict, List, Optional
import subprocess
import signal
import time

from .base import Adapter

class FalconSearchAdapter(Adapter):
    """Start/stop Falcon exploration via a subprocess command (typically roslaunch)."""

    def __init__(self, cmd: List[str]):
        self.cmd = cmd
        self.proc: Optional[subprocess.Popen] = None

    def enter(self, stage: Dict[str, Any]) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(self.cmd)
        print(f"[Adapter][SEARCH] started: {self.cmd}")

    def tick(self, stage: Dict[str, Any]) -> None:
        # Could update params via ROS param/topic later
        return

    def exit(self, stage: Dict[str, Any]) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=5.0)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        print("[Adapter][SEARCH] stopped")
