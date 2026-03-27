from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ReasonerProposal:
    proposal_id: str
    stage_id: str
    assessment: str
    evidence_summary: str
    failure_hypothesis: Optional[str]
    suggested_action: str
    target_stage_id: Optional[str] = None
    confidence: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasonerResponse:
    proposal: Optional[ReasonerProposal]
    accepted: bool
    reason: str
    raw_text: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class BaseRuntimeReasoner(ABC):
    name = "base"

    @abstractmethod
    def propose(self, summary: Dict[str, Any]) -> ReasonerResponse:
        ...

