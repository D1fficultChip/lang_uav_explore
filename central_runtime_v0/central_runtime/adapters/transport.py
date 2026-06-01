from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
import shlex
import subprocess
import re
from typing import IO


@dataclass(frozen=True)
class CommandEndpoint:
    mode: str
    target: str
    port: Optional[int] = None
    identity: Optional[str] = None


def parse_endpoint(spec: str) -> CommandEndpoint:
    s = (spec or "").strip()
    if s in ("", "local", "local:"):
        return CommandEndpoint(mode="local", target="")
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
        return CommandEndpoint(mode="docker", target=s)

    identity = None
    if "?" in s:
        s, query = s.split("?", 1)
        match = re.search(r"(?:^|&)i=([^&]+)", query)
        if match:
            identity = match.group(1)

    port = None
    match = re.match(r"^(?P<who>[^:]+):(?P<port>\d+)$", s)
    if match:
        s = match.group("who")
        port = int(match.group("port"))

    return CommandEndpoint(mode=mode, target=s, port=port, identity=identity)


def build_endpoint_spec(
    *,
    container: Optional[str] = None,
    transport: Optional[str] = None,
    target: Optional[str] = None,
    port: Optional[int] = None,
    identity: Optional[str] = None,
) -> str:
    if container:
        return str(container)

    mode = (transport or "docker").strip().lower()
    if mode == "local":
        return "local"
    endpoint_target = (target or "").strip()
    if not endpoint_target:
        raise ValueError("adapter target/container is required")

    if mode in ("docker", "container"):
        return endpoint_target

    if mode not in ("ssh", "sshpass"):
        raise ValueError(f"unsupported adapter transport: {mode}")

    spec = f"{mode}:{endpoint_target}"
    if port is not None:
        spec = f"{spec}:{int(port)}"
    if identity:
        spec = f"{spec}?i={identity}"
    return spec


def normalize_shell_prelude(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, Iterable):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " && ".join(parts) or None
    return str(value).strip() or None


def wrap_shell_command(shell_cmd: str, shell_prelude: Optional[str]) -> str:
    prelude = normalize_shell_prelude(shell_prelude)
    if not prelude:
        return shell_cmd
    return f"{prelude} && {shell_cmd}"


class ShellTransport:
    def __init__(
        self,
        *,
        spec: Optional[str] = None,
        ssh_extra_args: Optional[Iterable[str]] = None,
        shell_prelude: Optional[str] = None,
        shell_binary: str = "bash",
    ):
        endpoint = parse_endpoint(spec or "local")
        self.mode = endpoint.mode
        self.target = endpoint.target
        self.port = endpoint.port
        self.identity = endpoint.identity
        self.ssh_extra_args = [str(item) for item in (ssh_extra_args or [])]
        self.shell_prelude = shell_prelude
        self.shell_binary = str(shell_binary or "bash")

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "ShellTransport":
        cfg = cfg or {}
        spec: Optional[str]
        if not cfg:
            spec = "local"
        elif any(cfg.get(key) is not None for key in ("transport", "target", "port", "identity", "container")):
            spec = build_endpoint_spec(
                container=cfg.get("container"),
                transport=str(cfg.get("transport")) if cfg.get("transport") is not None else None,
                target=str(cfg.get("target")) if cfg.get("target") is not None else None,
                port=int(cfg.get("port")) if cfg.get("port") is not None else None,
                identity=str(cfg.get("identity")) if cfg.get("identity") is not None else None,
            )
        else:
            spec = str(cfg.get("spec", "local"))
        return cls(
            spec=spec,
            ssh_extra_args=cfg.get("ssh_extra_args"),
            shell_prelude=normalize_shell_prelude(cfg.get("shell_prelude")),
            shell_binary=str(cfg.get("shell_binary", "bash")),
        )

    def _ssh_base(self) -> list[str]:
        base = ["ssh", "-n", "-o", "StrictHostKeyChecking=accept-new"]
        if self.mode == "ssh":
            base += ["-o", "BatchMode=yes"]
        if self.identity:
            base += ["-i", self.identity]
        if self.port:
            base += ["-p", str(self.port)]
        if self.ssh_extra_args:
            base += self.ssh_extra_args
        base.append(self.target)
        return base

    def _argv_for_shell(self, shell_cmd: str) -> list[str]:
        wrapped = wrap_shell_command(shell_cmd, self.shell_prelude)
        if self.mode == "local":
            return [self.shell_binary, "-lc", wrapped]
        if self.mode == "docker":
            return ["docker", "exec", self.target, self.shell_binary, "-lc", wrapped]
        remote_cmd = shlex.join([self.shell_binary, "-lc", wrapped])
        base = self._ssh_base()
        if self.mode == "sshpass":
            base = ["sshpass", "-e"] + base
        return base + [remote_cmd]

    def run_shell(
        self,
        shell_cmd: str,
        *,
        timeout: Optional[float] = None,
        capture_output: bool = False,
        check: bool = False,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._argv_for_shell(shell_cmd),
            timeout=timeout,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=text,
            check=check,
        )

    def popen_shell(
        self,
        shell_cmd: str,
        *,
        stdout: Optional[IO[str]] = None,
        stderr: Optional[IO[str]] = None,
        text: bool = True,
        bufsize: int = 1,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            self._argv_for_shell(shell_cmd),
            stdout=stdout,
            stderr=stderr,
            text=text,
            bufsize=bufsize,
        )
