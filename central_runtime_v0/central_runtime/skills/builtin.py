from __future__ import annotations

from .contracts import SkillContract, SkillIO, SkillSignal


def fast_proposal_contract() -> SkillContract:
    return SkillContract(
        skill_id="fast_proposal",
        intent="PERCEIVE",
        description="Generate open-vocabulary proposals and cue evidence from RGB imagery.",
        inputs=[
            SkillIO("rgb_frame", "Current RGB image frame"),
            SkillIO("primary_prompt", "Primary open-vocabulary target prompt"),
            SkillIO("cue_prompts", "Optional cue prompts", required=False),
        ],
        outputs=[
            SkillIO("proposal_status", "Proposal summary status"),
            SkillIO("topk_candidates", "Top-k proposal list"),
            SkillIO("proposal_uncertainty", "Heuristic ambiguity signal"),
        ],
        success_signals=[SkillSignal("proposal_generated", "At least one proposal was produced")],
        failure_signals=[SkillSignal("no_candidate", "No valid candidate was generated")],
        recoverability={"retry": True, "fallback": "hold_observe"},
        safe_stop="Stop proposal refresh and keep current observation state.",
    )


def semantic_verify_contract() -> SkillContract:
    return SkillContract(
        skill_id="semantic_verify",
        intent="VERIFY",
        description="Low-frequency semantic verification over proposals and task context.",
        inputs=[
            SkillIO("proposal_summary", "Proposal metadata and top-k candidates"),
            SkillIO("task_context", "Current stage, relations, assumptions, and cues"),
        ],
        outputs=[
            SkillIO("verify_status", "Semantic verification result"),
            SkillIO("recommended_followup", "Suggested bounded follow-up action", required=False),
        ],
        success_signals=[SkillSignal("semantic_supported", "Semantic evidence supports the current hypothesis")],
        failure_signals=[SkillSignal("semantic_inconclusive", "Semantic evidence is insufficient or contradictory")],
        recoverability={"retry": True, "fallback": "search"},
        safe_stop="Abort verification request and keep runtime in the current stage.",
    )


def search_contract() -> SkillContract:
    return SkillContract(
        skill_id="search",
        intent="SEARCH",
        description="Explore the environment and search for the current primary target.",
        outputs=[SkillIO("search_progress", "Exploration progress and target sightings")],
        success_signals=[SkillSignal("target_detected", "Primary target candidate found")],
        failure_signals=[SkillSignal("search_timeout", "No target found within budget")],
        recoverability={"retry": True, "fallback": "hold_observe"},
        safe_stop="Switch to hold and stop exploration output.",
    )


def navigate_contract() -> SkillContract:
    return SkillContract(
        skill_id="navigate",
        intent="NAVIGATE",
        description="Navigate toward a goal using the underlying planner.",
        inputs=[SkillIO("goal_pose", "Navigation goal in world frame")],
        outputs=[SkillIO("nav_progress", "Navigation progress state")],
        success_signals=[SkillSignal("goal_reached", "Goal is reached")],
        failure_signals=[SkillSignal("nav_stalled", "Planner made no measurable progress")],
        recoverability={"retry": True, "fallback": "search"},
        safe_stop="Switch command mux to hold and stop navigation output.",
    )


def observe_contract() -> SkillContract:
    return SkillContract(
        skill_id="observe",
        intent="OBSERVE",
        description="Approach a localized target, hover briefly for verification, and rollback if evidence stays insufficient.",
        inputs=[
            SkillIO("target_position_world", "Localized target position in world frame"),
            SkillIO("current_odom", "Current vehicle odom pose"),
        ],
        outputs=[
            SkillIO("observe_status", "Observe result status"),
            SkillIO("rollback_target_odom", "Rollback odom pose", required=False),
        ],
        success_signals=[SkillSignal("observe_supported", "Target observation produced enough evidence")],
        failure_signals=[SkillSignal("observe_inconclusive", "Observation finished but evidence stayed insufficient")],
        recoverability={"retry": False, "fallback": "search"},
        safe_stop="Switch to hold and cancel observe maneuver progression.",
    )


def track_dynamic_contract() -> SkillContract:
    return SkillContract(
        skill_id="track_dynamic",
        intent="TRACK",
        description="Continuously update target-follow goals from localization and drive dynamic tracking with bounded recovery.",
        inputs=[
            SkillIO("target_position_world", "Continuously updated target position in world frame"),
            SkillIO("current_odom", "Current vehicle odom pose"),
        ],
        outputs=[
            SkillIO("track_status", "Tracking status"),
            SkillIO("last_goal_pose", "Last published tracking goal", required=False),
        ],
        success_signals=[SkillSignal("tracking_active", "Tracking loop is active and receiving target updates")],
        failure_signals=[SkillSignal("track_failed", "Tracking lost the target beyond the allowed timeout")],
        recoverability={"retry": True, "fallback": "search"},
        safe_stop="Switch command mux to hold and stop dynamic goal refresh.",
    )


def hold_observe_contract() -> SkillContract:
    return SkillContract(
        skill_id="hold_observe",
        intent="HOLD",
        description="Hold position and maintain observation for verification or recovery.",
        outputs=[SkillIO("observe_window", "Short temporal observation evidence")],
        success_signals=[SkillSignal("observation_refreshed", "New evidence collected")],
        failure_signals=[SkillSignal("observation_timeout", "Observation window expired without useful evidence")],
        recoverability={"retry": True, "fallback": "search"},
        safe_stop="Remain in hold mode with no additional maneuver.",
    )
