#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_last_jsonl(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
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
    except Exception:
        return None
    if not buf:
        return None
    try:
        return json.loads(bytes(reversed(buf)).decode("utf-8", errors="ignore").strip())
    except Exception:
        return None


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()


def file_meta(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    st = path.stat()
    return {
        "exists": True,
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def build_manifest(args, run_dir: Path) -> Dict[str, Any]:
    return {
        "created_at": time.time(),
        "created_at_iso": datetime.now().isoformat(timespec="seconds"),
        "run_name": run_dir.name,
        "shared_dir": str(Path(args.shared_dir).resolve()),
        "video": str(Path(args.video).resolve()) if args.video else None,
        "plan": str(Path(args.plan).resolve()) if args.plan else None,
        "notes": args.notes,
        "paths": {
            "infer_json": args.infer_json,
            "request_json": args.request_json,
            "runtime_log": args.runtime_log,
            "event_log": args.event_log,
            "frame_meta": args.frame_meta,
        },
    }


def copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description="Record per-inference trace rows for offline replay experiments.")
    ap.add_argument("--shared-dir", default="/home/young/uav_demo/shared")
    ap.add_argument("--infer-json", default="/home/young/uav_demo/shared/infer.json")
    ap.add_argument("--request-json", default="/home/young/uav_demo/shared/perception_request.json")
    ap.add_argument("--runtime-log", default="/home/young/uav_demo/shared/plan_runtime.jsonl")
    ap.add_argument("--event-log", default="/home/young/uav_demo/shared/runtime_events.jsonl")
    ap.add_argument("--frame-meta", default="/home/young/uav_demo/shared/frame_meta.json")
    ap.add_argument("--archive-root", default="/home/young/uav_demo/experiment_replays")
    ap.add_argument("--run-name", help="Optional fixed archive directory name.")
    ap.add_argument("--video", help="Optional source video path to include in manifest.")
    ap.add_argument("--plan", help="Optional plan.json path to copy into the archive.")
    ap.add_argument("--notes", default="", help="Optional free-form notes for this rerun.")
    ap.add_argument("--poll-sec", type=float, default=0.05)
    args = ap.parse_args()

    archive_root = Path(args.archive_root).expanduser().resolve()
    archive_root.mkdir(parents=True, exist_ok=True)
    run_name = args.run_name or datetime.now().strftime("replay_%Y%m%d_%H%M%S")
    run_dir = archive_root / run_name
    run_dir.mkdir(parents=True, exist_ok=False)

    trace_path = run_dir / "inference_trace.jsonl"
    summary_path = run_dir / "summary.json"
    manifest_path = run_dir / "manifest.json"

    manifest = build_manifest(args, run_dir)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.plan:
        copy_if_exists(Path(args.plan).expanduser().resolve(), run_dir / "plan.json")

    infer_path = Path(args.infer_json).expanduser().resolve()
    request_path = Path(args.request_json).expanduser().resolve()
    runtime_log_path = Path(args.runtime_log).expanduser().resolve()
    event_log_path = Path(args.event_log).expanduser().resolve()
    frame_meta_path = Path(args.frame_meta).expanduser().resolve()

    stop = {"flag": False}

    def handle_stop(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"[record_trace] archive -> {run_dir}")
    print(f"[record_trace] watching infer={infer_path}")

    rows = 0
    last_infer_mtime = -1.0
    while not stop["flag"]:
        try:
            st = infer_path.stat()
        except FileNotFoundError:
            time.sleep(args.poll_sec)
            continue

        if st.st_mtime <= last_infer_mtime:
            time.sleep(args.poll_sec)
            continue
        last_infer_mtime = st.st_mtime

        infer_obj = read_json(infer_path)
        if infer_obj is None:
            time.sleep(args.poll_sec)
            continue

        request_obj = read_json(request_path) or {}
        frame_meta = read_json(frame_meta_path) or {}
        runtime_last = read_last_jsonl(runtime_log_path) or {}
        event_last = read_last_jsonl(event_log_path) or {}

        row = {
            "recorded_at": time.time(),
            "record_index": rows + 1,
            "infer_mtime": st.st_mtime,
            "infer": infer_obj,
            "request": request_obj,
            "frame_meta": frame_meta,
            "runtime_last": runtime_last,
            "event_last": event_last,
        }
        append_jsonl(trace_path, row)
        rows += 1

    summary = {
        "stopped_at": time.time(),
        "stopped_at_iso": datetime.now().isoformat(timespec="seconds"),
        "records": rows,
        "files": {
            "infer_json": file_meta(infer_path),
            "request_json": file_meta(request_path),
            "runtime_log": file_meta(runtime_log_path),
            "event_log": file_meta(event_log_path),
            "frame_meta": file_meta(frame_meta_path),
        },
    }

    copy_if_exists(runtime_log_path, run_dir / runtime_log_path.name)
    copy_if_exists(event_log_path, run_dir / event_log_path.name)
    copy_if_exists(request_path, run_dir / request_path.name)
    copy_if_exists(frame_meta_path, run_dir / frame_meta_path.name)
    copy_if_exists(infer_path, run_dir / infer_path.name)

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[record_trace] done rows={rows} summary={summary_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
