from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SkillIO:
    name: str
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class SkillSignal:
    name: str
    description: str = ""


@dataclass
class SkillContract:
    skill_id: str
    intent: str
    description: str
    inputs: List[SkillIO] = field(default_factory=list)
    outputs: List[SkillIO] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    success_signals: List[SkillSignal] = field(default_factory=list)
    failure_signals: List[SkillSignal] = field(default_factory=list)
    recoverability: Dict[str, Any] = field(default_factory=dict)
    safe_stop: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "intent": self.intent,
            "description": self.description,
            "inputs": [vars(x) for x in self.inputs],
            "outputs": [vars(x) for x in self.outputs],
            "preconditions": list(self.preconditions),
            "success_signals": [vars(x) for x in self.success_signals],
            "failure_signals": [vars(x) for x in self.failure_signals],
            "recoverability": dict(self.recoverability),
            "safe_stop": self.safe_stop,
            "metadata": dict(self.metadata),
        }
