from __future__ import annotations

import os
import time
from typing import TextIO


def open_append_resilient(path: str, *, encoding: str = "utf-8") -> TextIO:
    """
    Open a jsonl/log file for append, even if a stale file left by a container
    is present with restrictive ownership/permissions.

    If append fails with PermissionError but the parent directory is writable,
    rotate the stale file aside and create a fresh one owned by the current user.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        return open(path, "a", encoding=encoding)
    except PermissionError:
        parent = os.path.dirname(path) or "."
        if not os.access(parent, os.W_OK):
            raise

        stale_path = f"{path}.stale.{int(time.time())}"
        try:
            if os.path.exists(path):
                os.replace(path, stale_path)
        except OSError:
            # Best effort: if we cannot rotate the stale file, surface the
            # original permission issue instead of hiding it.
            raise

        return open(path, "a", encoding=encoding)
