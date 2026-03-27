from __future__ import annotations

from .base import BaseRuntimeReasoner, ReasonerResponse


class NoopRuntimeReasoner(BaseRuntimeReasoner):
    name = "noop"

    def propose(self, summary):
        return ReasonerResponse(
            proposal=None,
            accepted=False,
            reason="reasoner disabled",
        )

