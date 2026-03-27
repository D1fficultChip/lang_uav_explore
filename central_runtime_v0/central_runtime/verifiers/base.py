from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from ..verification import VerificationResult


class BaseVerifier(ABC):
    name = "base"

    @abstractmethod
    def evaluate(self, **kwargs: Dict[str, Any]) -> VerificationResult:
        ...

