from __future__ import annotations

import os
import time
from typing import TextIO


def open_append_resilient(path: str, *, encoding: str = "utf-8") -> TextIO:
    """
    Open a log/jsonl file for append.

    If a stale file is left behind with restrictive ownership, rotate it aside
    when the parent directory is writable and create a fresh file instead.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        return open(path, "a", encoding=encoding)
    except PermissionError:
        parent = os.path.dirname(path) or "."
        if not os.access(parent, os.W_OK):
            raise

        stale_path = f"{path}.stale.{int(time.time())}"
        if os.path.exists(path):
            os.replace(path, stale_path)
        return open(path, "a", encoding=encoding)
