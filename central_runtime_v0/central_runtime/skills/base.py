from __future__ import annotations

from abc import ABC, abstractmethod

from .contracts import SkillContract


class RuntimeSkill(ABC):
    @abstractmethod
    def contract(self) -> SkillContract:
        ...
