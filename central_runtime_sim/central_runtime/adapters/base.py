from __future__ import annotations
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod

class Adapter(ABC):
    @abstractmethod
    def enter(self, stage: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def tick(self, stage: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def exit(self, stage: Dict[str, Any]) -> None:
        ...
