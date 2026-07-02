#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_signature(event: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        event.get("t_wall"),
        event.get("event_type"),
        event.get("stage_id"),
        event.get("intent"),
        event.get("entity_id"),
        stable_json(event.get("details")),
    )


def has_perception_evidence(row: Dict[str, Any], runtime: Dict[str, Any], infer: Dict[str, Any]) -> bool:
    if infer:
        return True
    if runtime.get("primary_det") or runtime.get("cue_det"):
        return True
    entity_state = runtime.get("entity_state") or {}
    if entity_state.get("evidence_count") not in (None, 0):
        return True
    return False


def has_verification_status(runtime: Dict[str, Any]) -> bool:
    entity_state = runtime.get("entity_state") or {}
    if entity_state.get("verification_status") is not None:
        return True
    verifiers = runtime.get("verifiers") or {}
    return bool(verifiers)


def reasoner_signature(runtime: Dict[str, Any]) -> Tuple[Any, ...] | None:
    reasoner = runtime.get("reasoner")
    if not reasoner:
        return None
    proposal = reasoner.get("proposal") or {}
    return (
        proposal.get("proposal_id"),
        reasoner.get("accepted"),
        reasoner.get("reason"),
        proposal.get("suggested_action"),
        proposal.get("target_stage_id"),
        proposal.get("confidence"),
    )


def guard_signature(runtime: Dict[str, Any]) -> Tuple[Any, ...] | None:
    guard = runtime.get("reasoner_guard")
    outgoing = runtime.get("outgoing") or []
    if not guard and not outgoing:
        return None
    return (
        stable_json(guard),
        stable_json(outgoing),
    )


def is_applied_action_event(event_type: str) -> bool:
    action_prefixes = (
        "reasoner_action",
        "semantic_followup_applied",
        "goal_published",
        "traj_start_trigger_published",
        "observe_",
        "track_",
        "recovery_",
        "mission_finished",
        "stage_success",
        "stage_failed",
    )
    return event_type.startswith(action_prefixes)


def is_skill_event(event_type: str) -> bool:
    return event_type == "skill_invoked" or event_type.startswith("skill_")


def is_transition_event(event_type: str) -> bool:
    return event_type.startswith(("transition", "stage_enter", "stage_exit", "reasoner_approved", "reasoner_rejected"))


def summarize_trace(path: Path) -> Dict[str, Any]:
    counts = Counter()
    unique_stages = set()
    unique_reasoners = set()
    unique_guards = set()
    unique_events = set()
    unique_skill_events = set()
    unique_applied_actions = set()
    unique_transition_events = set()
    event_type_counter = Counter()

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            runtime = row.get("runtime_last") or {}
            infer = row.get("infer") or {}
            event = row.get("event_last") or {}

            counts["rows"] += 1

            stage = runtime.get("stage") or infer.get("stage_id")
            if stage is not None:
                counts["stage"] += 1
                unique_stages.add(stage)

            if has_perception_evidence(row, runtime, infer):
                counts["perception_evidence"] += 1

            if runtime:
                counts["state_summary"] += 1

            if has_verification_status(runtime):
                counts["verification_status"] += 1

            rsig = reasoner_signature(runtime)
            if rsig is not None:
                counts["reasoner_proposal"] += 1
                unique_reasoners.add(rsig)

            gsig = guard_signature(runtime)
            if gsig is not None:
                counts["guard_transition_result"] += 1
                unique_guards.add(gsig)

            if event:
                esig = event_signature(event)
                unique_events.add(esig)
                etype = str(event.get("event_type"))
                event_type_counter[etype] += 1
                if is_skill_event(etype):
                    counts["skill_event"] += 1
                    unique_skill_events.add(esig)
                if is_applied_action_event(etype):
                    counts["applied_action"] += 1
                    unique_applied_actions.add(esig)
                if is_transition_event(etype):
                    unique_transition_events.add(esig)

    return {
        "trace_path": str(path),
        "rows": counts["rows"],
        "counts_by_snapshot": {
            "stage": counts["stage"],
            "perception_evidence": counts["perception_evidence"],
            "state_summary": counts["state_summary"],
            "verification_status": counts["verification_status"],
            "reasoner_proposal": counts["reasoner_proposal"],
            "guard_transition_result": counts["guard_transition_result"],
            "applied_action": counts["applied_action"],
            "skill_event": counts["skill_event"],
        },
        "counts_unique": {
            "stage_values": len(unique_stages),
            "reasoner_proposal": len(unique_reasoners),
            "guard_transition_result": len(unique_guards),
            "applied_action_event": len(unique_applied_actions),
            "skill_event": len(unique_skill_events),
            "transition_event": len(unique_transition_events),
            "all_event_records": len(unique_events),
        },
        "stage_values": sorted(unique_stages),
        "event_type_counter": dict(sorted(event_type_counter.items())),
    }


def aggregate(summaries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    total_snapshot = Counter()
    total_unique = Counter()
    all_event_types = Counter()
    task_count = 0
    for item in summaries:
        task_count += 1
        total_snapshot.update(item["counts_by_snapshot"])
        total_unique.update(item["counts_unique"])
        all_event_types.update(item["event_type_counter"])
    return {
        "task_count": task_count,
        "counts_by_snapshot": dict(total_snapshot),
        "counts_unique": dict(total_unique),
        "event_type_counter": dict(sorted(all_event_types.items())),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Count how many times key runtime log contents appear in archived inference_trace.jsonl files."
    )
    ap.add_argument(
        "--glob",
        default="/home/young/uav_demo/experiment_replays/*/inference_trace.jsonl",
        help="Glob for archived inference_trace.jsonl files",
    )
    ap.add_argument("--out", help="Optional output JSON path")
    args = ap.parse_args()

    paths = [Path(p) for p in sorted(glob.glob(args.glob))]
    if not paths:
        raise SystemExit(f"No files matched: {args.glob}")

    summaries = [summarize_trace(path) for path in paths]
    result = {
        "definition": {
            "stage": "Rows containing stage id in runtime snapshot or infer result.",
            "perception_evidence": "Rows containing infer payload or runtime primary/cue evidence fields.",
            "state_summary": "Rows with a non-empty runtime_last snapshot.",
            "verification_status": "Rows with entity_state.verification_status or verifiers block.",
            "reasoner_proposal": "Rows with non-null runtime_last.reasoner; unique count dedupes by proposal content.",
            "guard_transition_result": "Rows with non-null reasoner_guard or outgoing transition evaluation.",
            "applied_action": "Unique event records whose event_type indicates an action was applied.",
            "skill_event": "Unique event records whose event_type is skill_invoked or skill_*.",
        },
        "per_task": summaries,
        "aggregate": aggregate(summaries),
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).expanduser().resolve().write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
