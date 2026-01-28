from __future__ import annotations
import os, tempfile

class PromptController:
    """Atomically write the prompt file used by the perception container."""

    def __init__(self, prompt_path: str):
        self.prompt_path = prompt_path

    @staticmethod
    def _normalize_prompt(p: str) -> str:
        p = (p or "").strip().lower()
        if not p:
            return p
        if not p.endswith("."):
            p += "."
        return p

    def set_prompt(self, prompt: str) -> None:
        prompt = self._normalize_prompt(prompt)
        tmp_path = self.prompt_path + ".tmp"
        # atomic replace
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(prompt + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.prompt_path)
