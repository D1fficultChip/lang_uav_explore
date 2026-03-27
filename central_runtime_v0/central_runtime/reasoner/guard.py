from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import ReasonerProposal


@dataclass
class GuardDecision:
    approved: bool
    reason: str
    action: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class ReasonerGuard:
    def __init__(
        self,
        *,
        allow_actions: Optional[List[str]] = None,
        auto_apply_actions: Optional[List[str]] = None,
        allow_insert_verify_stage: bool = False,
        allow_unknown_target_stage: bool = False,
    ):
        self.allow_actions = set(allow_actions or [])
        self.auto_apply_actions = set(auto_apply_actions or [])
        self.allow_insert_verify_stage = bool(allow_insert_verify_stage)
        self.allow_unknown_target_stage = bool(allow_unknown_target_stage)

    def review(self, proposal: ReasonerProposal, *, plan_stage_ids: List[str]) -> GuardDecision:
        action = (proposal.suggested_action or "").strip()
        if not action:
            return GuardDecision(approved=False, reason="empty suggested_action")
        if action not in self.allow_actions:
            return GuardDecision(approved=False, reason=f"action not allowed: {action}", action=action)
        if action == "insert_verify_stage" and not self.allow_insert_verify_stage:
            return GuardDecision(approved=False, reason="insert_verify_stage disabled by guard", action=action)
        if proposal.target_stage_id:
            if proposal.target_stage_id not in plan_stage_ids and not self.allow_unknown_target_stage:
                return GuardDecision(
                    approved=False,
                    reason=f"unknown target_stage_id: {proposal.target_stage_id}",
                    action=action,
                    details={"target_stage_id": proposal.target_stage_id},
                )
        return GuardDecision(
            approved=True,
            reason="proposal approved",
            action=action,
            details={"auto_apply": action in self.auto_apply_actions},
        )

