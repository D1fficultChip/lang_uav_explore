#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_last_jsonl(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = bytearray()
        while pos > 0:
            pos -= 1
            f.seek(pos)
            b = f.read(1)
            if b == b"\n" and buf:
                break
            if b != b"\n":
                buf.extend(b)
        if not buf:
            return None
    line = bytes(reversed(buf)).decode("utf-8", errors="ignore").strip()
    if not line:
        return None
    return json.loads(line)


def _read_recent_events(path: Path, limit: int = 12) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    out: List[Dict[str, Any]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _fmt_pos(v: Any) -> Optional[List[float]]:
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return None
    return [round(float(v[0]), 3), round(float(v[1]), 3), round(float(v[2]), 3)]


def _file_meta(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    return {
        "exists": True,
        "size": path.stat().st_size,
        "age_s": round(time.time() - path.stat().st_mtime, 3),
    }


def build_status(runtime_log: Path, event_log: Path, shared_dir: Path) -> Dict[str, Any]:
    snap = _read_last_jsonl(runtime_log) or {}
    events = _read_recent_events(event_log, limit=12)

    entity = snap.get("entity_state") or {}
    primary = snap.get("primary_det") or {}
    cue = snap.get("cue_det") or {}
    localization = snap.get("localization") or {}
    observe_state = snap.get("observe_state") or {}
    track_state = snap.get("track_state") or {}
    verify_window = snap.get("verify_window") or {}
    criteria = snap.get("criteria") or {}
    diagnostics = snap.get("diagnostics") or []
    escape_state = snap.get("escape_state") or {}

    latest_event = events[-1] if events else {}
    recent_event_briefs = []
    for ev in events[-8:]:
        recent_event_briefs.append(
            {
                "t_wall": ev.get("t_wall"),
                "event_type": ev.get("event_type"),
                "stage_id": ev.get("stage_id"),
                "intent": ev.get("intent"),
                "details": ev.get("details", {}),
            }
        )

    diag_briefs = []
    for d in diagnostics[-5:]:
        diag_briefs.append(
            {
                "code": d.get("code"),
                "severity": d.get("severity"),
                "message": d.get("message"),
            }
        )

    status = {
        "generated_at": time.time(),
        "runtime": {
            "stage": snap.get("stage"),
            "intent": snap.get("intent"),
            "active_entity": snap.get("active_entity"),
            "current_req_id": snap.get("current_req_id"),
            "current_skill_id": snap.get("current_skill_id"),
            "mission_elapsed": round(float(snap.get("mission_elapsed", 0.0)), 3) if snap else None,
            "stage_elapsed": round(float(snap.get("stage_elapsed", 0.0)), 3) if snap else None,
        },
        "perception": {
            "primary_found": bool(primary.get("found", False)),
            "primary_score": primary.get("score"),
            "primary_status": primary.get("proposal_status"),
            "cue_found": bool(cue.get("found", False)),
            "cue_score": cue.get("score"),
        },
        "localization": {
            "found": bool(localization.get("found", False)),
            "confidence": localization.get("localization_confidence", entity.get("localization_confidence")),
            "target_world": _fmt_pos(localization.get("target_position_world") or entity.get("target_position_world")),
            "support_pixels": localization.get("support_pixels", entity.get("support_pixels")),
            "failure_reason": localization.get("failure_reason", entity.get("localization_failure_reason")),
        },
        "verification": {
            "entity_verified": bool(entity.get("verified", False)),
            "verification_status": entity.get("verification_status"),
            "stable_hit_streak": entity.get("stable_hit_streak"),
            "verify_window_active": bool(verify_window.get("active", False)),
        },
        "semantic": {
            "status": entity.get("semantic_verify_status"),
            "confidence": entity.get("semantic_verify_confidence"),
            "need_additional_view": entity.get("semantic_need_additional_view"),
            "recommended_followup": entity.get("semantic_recommended_followup"),
            "failure_hypothesis": entity.get("semantic_failure_hypothesis"),
            "explanation": entity.get("semantic_explanation"),
            "last_semantic_verify_t": entity.get("last_semantic_verify_t"),
        },
        "observe": {
            "phase": observe_state.get("phase"),
            "reason": observe_state.get("reason"),
            "observe_goal": observe_state.get("observe_goal"),
            "rollback_started_t": observe_state.get("rollback_started_t"),
        },
        "track": {
            "phase": track_state.get("phase"),
            "reason": track_state.get("reason"),
            "last_goal": track_state.get("last_goal"),
        },
        "escape": {
            "active": bool(escape_state.get("active", False)),
            "phase": escape_state.get("phase"),
            "reason": escape_state.get("trigger_reason"),
            "goal_pose": escape_state.get("goal_pose"),
            "retry_count": escape_state.get("retry_count"),
            "history_size": snap.get("escape_history_size"),
            "recent_progress_m": snap.get("search_motion_progress_m"),
        },
        "criteria": {
            "success_met": bool(criteria.get("success_met", False)),
            "failure_met": bool(criteria.get("failure_met", False)),
            "success": criteria.get("success", []),
            "failure": criteria.get("failure", []),
        },
        "diagnostics": diag_briefs,
        "latest_event": latest_event,
        "recent_events": recent_event_briefs,
        "files": {
            "frame.jpg": _file_meta(shared_dir / "frame.jpg"),
            "infer.json": _file_meta(shared_dir / "infer.json"),
            "target_localization.json": _file_meta(shared_dir / "target_localization.json"),
            "plan_runtime.jsonl": _file_meta(runtime_log),
            "runtime_events.jsonl": _file_meta(event_log),
        },
    }
    return status


def print_terminal(status: Dict[str, Any]) -> None:
    rt = status["runtime"]
    loc = status["localization"]
    obs = status["observe"]
    ver = status["verification"]
    sem = status.get("semantic", {})
    crit = status["criteria"]
    esc = status.get("escape", {})
    print("=" * 72)
    print(
        f"Stage={rt.get('stage')}  Intent={rt.get('intent')}  Skill={rt.get('current_skill_id')}  "
        f"Entity={rt.get('active_entity')}  Req={rt.get('current_req_id')}"
    )
    print(
        f"Mission={rt.get('mission_elapsed')}s  StageElapsed={rt.get('stage_elapsed')}s  "
        f"Verified={ver.get('entity_verified')} ({ver.get('verification_status')})"
    )
    print(
        f"Localization found={loc.get('found')} conf={loc.get('confidence')} "
        f"target={loc.get('target_world')} reason={loc.get('failure_reason')}"
    )
    print(
        f"Observe phase={obs.get('phase')} reason={obs.get('reason')}  "
        f"Criteria success={crit.get('success_met')} failure={crit.get('failure_met')}"
    )
    print(
        f"Escape active={esc.get('active')} phase={esc.get('phase')} "
        f"reason={esc.get('reason')} retries={esc.get('retry_count')} "
        f"progress={esc.get('recent_progress_m')}"
    )
    print(
        f"Semantic status={sem.get('status')} conf={sem.get('confidence')} "
        f"followup={sem.get('recommended_followup')} need_view={sem.get('need_additional_view')}"
    )
    print("Recent events:")
    for ev in status["recent_events"][-6:]:
        print(f"  - {ev.get('event_type')} @ {ev.get('stage_id')}  {ev.get('details')}")
    if status["diagnostics"]:
        print("Diagnostics:")
        for d in status["diagnostics"]:
            print(f"  - [{d.get('severity')}] {d.get('code')}: {d.get('message')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-log", default="/home/young/uav_demo/shared/plan_runtime.jsonl")
    ap.add_argument("--event-log", default="/home/young/uav_demo/shared/runtime_events.jsonl")
    ap.add_argument("--shared-dir", default="/home/young/uav_demo/shared")
    ap.add_argument("--out", help="write compact status json to this path")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--terminal", action="store_true")
    args = ap.parse_args()

    runtime_log = Path(args.runtime_log)
    event_log = Path(args.event_log)
    shared_dir = Path(args.shared_dir)
    out_path = Path(args.out) if args.out else None

    def one() -> None:
        status = build_status(runtime_log, event_log, shared_dir)
        if out_path:
            out_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.terminal:
            print_terminal(status)
        elif not out_path:
            print(json.dumps(status, ensure_ascii=False, indent=2))

    if args.watch:
        while True:
            try:
                if args.terminal:
                    os.system("clear")
                one()
            except KeyboardInterrupt:
                return
            except Exception as e:
                msg = {"error": str(e), "generated_at": time.time()}
                if out_path:
                    out_path.write_text(json.dumps(msg, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    print(json.dumps(msg, ensure_ascii=False), file=sys.stderr)
            time.sleep(max(0.1, args.interval))
    else:
        one()


if __name__ == "__main__":
    main()
