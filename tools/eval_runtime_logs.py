#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _iter_log_pairs(args) -> Iterable[Tuple[str, Optional[str]]]:
    if args.runtime_log:
        yield args.runtime_log, args.event_log
        return

    if args.log_dir:
        runtime_paths = sorted(glob.glob(os.path.join(args.log_dir, args.runtime_glob)))
        for runtime_path in runtime_paths:
            event_path = os.path.join(os.path.dirname(runtime_path), args.event_name)
            yield runtime_path, event_path if os.path.exists(event_path) else None


def _safe_div(num: float, den: float) -> Optional[float]:
    if den <= 0:
        return None
    return float(num) / float(den)


def _last_snapshot_before(snapshots: List[Dict[str, Any]], *, stage_id: str, t_wall: float) -> Optional[Dict[str, Any]]:
    candidates = [s for s in snapshots if s.get("stage") == stage_id and float(s.get("t", 0.0)) <= float(t_wall)]
    if not candidates:
        return None
    candidates.sort(key=lambda x: float(x.get("t", 0.0)))
    return candidates[-1]


def _criteria_contains_budget_exceeded(criteria: Dict[str, Any]) -> bool:
    for bucket_name in ("success", "failure"):
        for item in criteria.get(bucket_name, []) or []:
            if item.get("type") == "BUDGET_EXCEEDED" and bool(item.get("ok")):
                return True
    return False


@dataclass
class EpisodeMetrics:
    episode_id: str
    task_success: bool
    within_budget_success: bool
    completion_time_s: float
    stage_completion_accuracy: Optional[float]
    correct_transition_rate: Optional[float]
    premature_progression_rate: Optional[float]
    verification_precision: Optional[float]
    verification_recall: Optional[float]
    failure_diagnosis_accuracy: Optional[float]
    recovery_success_rate: Optional[float]
    replanning_effectiveness: Optional[float]
    closed_loop_correction_rate: Optional[float]
    total_stage_enters: int
    total_transitions: int
    total_verification_calls: int
    total_recoveries: int
    total_reasoner_actions: int


def _compute_episode(runtime_path: str, event_path: Optional[str]) -> EpisodeMetrics:
    snapshots = []
    for x in _read_jsonl(runtime_path):
        rec_type = x.get("rec_type")
        if rec_type == "runtime_snapshot" or (rec_type is None and "stage" in x and "t" in x):
            snapshots.append(x)
    events = [x for x in _read_jsonl(event_path) if x.get("rec_type") == "runtime_event"] if event_path else []

    if not snapshots and not events:
        return EpisodeMetrics(
            episode_id=os.path.basename(runtime_path),
            task_success=False,
            within_budget_success=False,
            completion_time_s=0.0,
            stage_completion_accuracy=None,
            correct_transition_rate=None,
            premature_progression_rate=None,
            verification_precision=None,
            verification_recall=None,
            failure_diagnosis_accuracy=None,
            recovery_success_rate=None,
            replanning_effectiveness=None,
            closed_loop_correction_rate=None,
            total_stage_enters=0,
            total_transitions=0,
            total_verification_calls=0,
            total_recoveries=0,
            total_reasoner_actions=0,
        )

    t_candidates: List[float] = []
    t_candidates.extend(float(x.get("t", 0.0)) for x in snapshots if x.get("t") is not None)
    t_candidates.extend(float(x.get("t_wall", 0.0)) for x in events if x.get("t_wall") is not None)
    start_t = min(t_candidates) if t_candidates else 0.0
    end_t = max(t_candidates) if t_candidates else 0.0

    finish_events = [e for e in events if e.get("event_type") == "mission_finished"]
    stop_events = [e for e in events if e.get("event_type") == "mission_stop_requested"]
    terminal_reason = None
    if finish_events:
        terminal_reason = (finish_events[-1].get("details") or {}).get("terminal_reason")
        end_t = max(end_t, float(finish_events[-1].get("t_wall", end_t)))
    elif stop_events:
        terminal_reason = (stop_events[-1].get("details") or {}).get("reason")
        end_t = max(end_t, float(stop_events[-1].get("t_wall", end_t)))

    task_success = isinstance(terminal_reason, str) and terminal_reason.startswith("stage success")
    budget_exceeded = any(_criteria_contains_budget_exceeded(s.get("criteria") or {}) for s in snapshots)
    within_budget_success = bool(task_success and not budget_exceeded)

    stage_enter_events = [e for e in events if e.get("event_type") == "stage_enter"]
    if not stage_enter_events and snapshots:
        last_stage = None
        inferred_stage_enters = []
        for snap in sorted(snapshots, key=lambda x: float(x.get("t", 0.0))):
            stage_id = snap.get("stage")
            if stage_id != last_stage:
                inferred_stage_enters.append(
                    {
                        "event_type": "stage_enter",
                        "stage_id": stage_id,
                        "t_wall": float(snap.get("t", 0.0)),
                    }
                )
                last_stage = stage_id
        stage_enter_events = inferred_stage_enters
    transition_events = [e for e in events if e.get("event_type") == "transition"]
    if not transition_events and snapshots:
        inferred_transitions = []
        ordered = sorted(snapshots, key=lambda x: float(x.get("t", 0.0)))
        last_stage = None
        for snap in ordered:
            stage_id = snap.get("stage")
            if last_stage is not None and stage_id != last_stage:
                inferred_transitions.append(
                    {
                        "event_type": "transition",
                        "t_wall": float(snap.get("t", 0.0)),
                        "stage_id": last_stage,
                        "entity_id": snap.get("active_entity"),
                        "details": {"from": last_stage, "to": stage_id, "when": {"type": "INFERRED"}},
                    }
                )
            last_stage = stage_id
        transition_events = inferred_transitions
    stage_success_events = [e for e in events if e.get("event_type") == "stage_success"]
    stage_failure_events = [e for e in events if e.get("event_type") == "stage_failure"]
    semantic_calls = [e for e in events if e.get("event_type") == "semantic_verify_called"]
    semantic_supported = [e for e in events if e.get("event_type") == "semantic_verify_supported"]
    recovery_started = [e for e in events if e.get("event_type") == "recovery_started"]
    recovery_finished = [e for e in events if e.get("event_type") == "recovery_finished"]
    reasoner_actions = [e for e in events if e.get("event_type") == "reasoner_action"]
    reasoner_approved = [e for e in events if e.get("event_type") == "reasoner_approved"]
    semantic_followup_applied = [e for e in events if e.get("event_type") == "semantic_followup_applied"]
    verify_windows = [e for e in events if e.get("event_type") == "verify_window_started"]
    reasoner_rejected = [e for e in events if e.get("event_type") == "reasoner_rejected"]

    entered_stages = len(stage_enter_events)
    completed_stages = len(stage_success_events)
    stage_completion_accuracy = _safe_div(completed_stages, entered_stages)

    correct_transitions = 0
    premature_progressions = 0
    for ev in transition_events:
        details = ev.get("details") or {}
        from_stage = details.get("from")
        snap = _last_snapshot_before(snapshots, stage_id=from_stage, t_wall=float(ev.get("t_wall", 0.0)))
        if not snap:
            continue
        criteria = snap.get("criteria") or {}
        milestones = snap.get("milestones") or {}
        transition_ok = bool(criteria.get("success_met")) or ("stage_success" in milestones)
        if transition_ok:
            correct_transitions += 1
        else:
            premature_progressions += 1

    correct_transition_rate = _safe_div(correct_transitions, len(transition_events))
    premature_progression_rate = _safe_div(premature_progressions, len(transition_events))

    semantic_supported_stage_keys = {(e.get("stage_id"), e.get("entity_id")) for e in semantic_supported}
    progressed_supported_stage_keys = set()
    for ev in transition_events:
        details = ev.get("details") or {}
        key = (details.get("from"), ev.get("entity_id"))
        if key in semantic_supported_stage_keys:
            progressed_supported_stage_keys.add(key)
    verification_precision = _safe_div(len(progressed_supported_stage_keys), len(semantic_supported_stage_keys))

    semantic_called_stage_keys = {(e.get("stage_id"), e.get("entity_id")) for e in semantic_calls}
    progressed_called_stage_keys = set()
    for ev in transition_events:
        details = ev.get("details") or {}
        key = (details.get("from"), ev.get("entity_id"))
        if key in semantic_called_stage_keys:
            progressed_called_stage_keys.add(key)
    verification_recall = _safe_div(len(progressed_supported_stage_keys), len(progressed_called_stage_keys))

    diagnosis_events = [e for e in snapshots if e.get("diagnostics")]
    diagnosis_with_failure = 0
    for snap in diagnosis_events:
        diags = snap.get("diagnostics") or []
        if not diags:
            continue
        top = diags[-1]
        code = top.get("code")
        if code in ("semantic_inconclusive", "semantic_not_supported", "semantic_needs_view", "nav_stalled", "req_mismatch", "entity_mismatch", "role_mismatch", "stage_failure"):
            diagnosis_with_failure += 1
    failure_diagnosis_accuracy = _safe_div(diagnosis_with_failure, len(diagnosis_events))

    recovery_success_rate = _safe_div(len(recovery_finished), len(recovery_started))

    replanning_sources = len(reasoner_approved) + len(semantic_followup_applied) + len(verify_windows)
    replanning_effectiveness = _safe_div(len(transition_events) + len(recovery_finished), replanning_sources)

    correction_numerator = len(recovery_finished) + len(semantic_followup_applied) + len(verify_windows)
    correction_denominator = len(recovery_started) + len(semantic_calls) + len(reasoner_actions) + len(reasoner_rejected)
    closed_loop_correction_rate = _safe_div(correction_numerator, correction_denominator)

    return EpisodeMetrics(
        episode_id=os.path.basename(runtime_path),
        task_success=task_success,
        within_budget_success=within_budget_success,
        completion_time_s=max(0.0, end_t - start_t),
        stage_completion_accuracy=stage_completion_accuracy,
        correct_transition_rate=correct_transition_rate,
        premature_progression_rate=premature_progression_rate,
        verification_precision=verification_precision,
        verification_recall=verification_recall,
        failure_diagnosis_accuracy=failure_diagnosis_accuracy,
        recovery_success_rate=recovery_success_rate,
        replanning_effectiveness=replanning_effectiveness,
        closed_loop_correction_rate=closed_loop_correction_rate,
        total_stage_enters=entered_stages,
        total_transitions=len(transition_events),
        total_verification_calls=len(semantic_calls),
        total_recoveries=len(recovery_started),
        total_reasoner_actions=len(reasoner_actions),
    )


def _aggregate(metrics: List[EpisodeMetrics]) -> Dict[str, Any]:
    if not metrics:
        return {"episodes": 0}

    def mean_of(name: str) -> Optional[float]:
        vals = [getattr(m, name) for m in metrics if getattr(m, name) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    return {
        "episodes": len(metrics),
        "task_success_rate": mean_of("task_success"),
        "within_budget_success_rate": mean_of("within_budget_success"),
        "avg_completion_time_s": mean_of("completion_time_s"),
        "avg_stage_completion_accuracy": mean_of("stage_completion_accuracy"),
        "avg_correct_transition_rate": mean_of("correct_transition_rate"),
        "avg_premature_progression_rate": mean_of("premature_progression_rate"),
        "avg_verification_precision": mean_of("verification_precision"),
        "avg_verification_recall": mean_of("verification_recall"),
        "avg_failure_diagnosis_accuracy": mean_of("failure_diagnosis_accuracy"),
        "avg_recovery_success_rate": mean_of("recovery_success_rate"),
        "avg_replanning_effectiveness": mean_of("replanning_effectiveness"),
        "avg_closed_loop_correction_rate": mean_of("closed_loop_correction_rate"),
        "notes": [
            "This evaluator computes process-oriented metrics from runtime snapshots and runtime events.",
            "Verification, diagnosis, and replanning metrics are stage-level approximations for the current log schema.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate central runtime logs against Benchmark 2.0 style metrics.")
    ap.add_argument("--runtime-log", help="Single plan_runtime.jsonl path")
    ap.add_argument("--event-log", help="Single runtime_events.jsonl path")
    ap.add_argument("--log-dir", help="Directory containing runtime logs for batch evaluation")
    ap.add_argument("--runtime-glob", default="plan_runtime*.jsonl", help="Glob under --log-dir for runtime logs")
    ap.add_argument("--event-name", default="runtime_events.jsonl", help="Event log filename under each log dir")
    ap.add_argument("--output", help="Optional output JSON path")
    args = ap.parse_args()

    episodes = [_compute_episode(runtime_path, event_path) for runtime_path, event_path in _iter_log_pairs(args)]
    report = {
        "summary": _aggregate(episodes),
        "episodes": [asdict(ep) for ep in episodes],
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
