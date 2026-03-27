from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class VerificationStatus:
    UNKNOWN = "unknown"
    INCONCLUSIVE = "inconclusive"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    FAILED = "failed"


@dataclass
class VerificationResult:
    name: str
    status: str
    entity_id: Optional[str] = None
    score: float = 0.0
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def is_verified(self) -> bool:
        return self.status == VerificationStatus.VERIFIED

