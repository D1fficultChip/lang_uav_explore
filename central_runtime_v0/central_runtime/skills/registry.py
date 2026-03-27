from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .contracts import SkillContract


@dataclass
class SkillRegistry:
    _by_intent: Dict[str, SkillContract] = field(default_factory=dict)
    _by_skill_id: Dict[str, SkillContract] = field(default_factory=dict)

    def register(self, contract: SkillContract) -> None:
        self._by_intent[contract.intent] = contract
        self._by_skill_id[contract.skill_id] = contract

    def get_by_intent(self, intent: str) -> Optional[SkillContract]:
        return self._by_intent.get(intent)

    def get(self, skill_id: str) -> Optional[SkillContract]:
        return self._by_skill_id.get(skill_id)

    def list_all(self) -> List[SkillContract]:
        return list(self._by_skill_id.values())
