#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Optional

import cv2


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def encode_jpg(frame, jpeg_quality: int) -> bytes:
    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return enc.tobytes()


def resolve_replay_fps(cap: cv2.VideoCapture, requested_fps: Optional[float]) -> float:
    if requested_fps and requested_fps > 0:
        return float(requested_fps)
    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if src_fps > 0:
        return src_fps
    return 5.0


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a recorded experiment video into shared/frame.jpg for offline reruns.")
    ap.add_argument("--video", required=True, help="Path to the recorded first-person video.")
    ap.add_argument("--shared-dir", default="/home/young/uav_demo/shared", help="Shared directory used by the runtime.")
    ap.add_argument("--fps", type=float, default=0.0, help="Replay FPS. Default: use source video FPS.")
    ap.add_argument("--start-sec", type=float, default=0.0, help="Start replay from this second.")
    ap.add_argument("--max-frames", type=int, default=0, help="Optional frame cap for debugging.")
    ap.add_argument("--jpeg-quality", type=int, default=90, help="JPEG quality for shared/frame.jpg.")
    ap.add_argument("--stamp-mode", choices=["wall", "video"], default="video", help="frame_meta stamp source.")
    ap.add_argument("--wait-key", action="store_true", help="Show a local preview window and allow q to stop.")
    args = ap.parse_args()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    shared_dir = Path(args.shared_dir).expanduser().resolve()
    shared_dir.mkdir(parents=True, exist_ok=True)
    frame_path = shared_dir / "frame.jpg"
    meta_path = shared_dir / "frame_meta.json"

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video: {video_path}")

    if args.start_sec > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(args.start_sec) * 1000.0)

    replay_fps = resolve_replay_fps(cap, args.fps)
    replay_dt = 1.0 / max(replay_fps, 1e-6)
    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    start_wall = time.time()
    frame_count = 0

    print(f"[video_replay] video={video_path}")
    print(f"[video_replay] shared_dir={shared_dir} replay_fps={replay_fps:.3f} source_fps={src_fps:.3f}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            video_frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            video_sec = float(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            frame_count += 1

            jpg = encode_jpg(frame, args.jpeg_quality)
            atomic_write_bytes(frame_path, jpg)

            if args.stamp_mode == "wall":
                stamp = time.time()
            else:
                stamp = video_sec

            meta = {
                "seq": frame_count,
                "stamp": stamp,
                "t_wall": time.time(),
                "video_path": str(video_path),
                "video_frame_idx": video_frame_idx,
                "video_time_sec": video_sec,
                "replay_fps": replay_fps,
                "source_fps": src_fps,
                "mode": "offline_video_replay",
            }
            atomic_write_json(meta_path, meta)

            if args.wait_key:
                cv2.imshow("video_replay", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            if args.max_frames > 0 and frame_count >= args.max_frames:
                break

            target_elapsed = frame_count * replay_dt
            sleep_s = target_elapsed - (time.time() - start_wall)
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        cap.release()
        if args.wait_key:
            cv2.destroyAllWindows()

    print(f"[video_replay] finished frames={frame_count} last_frame={frame_path}")


if __name__ == "__main__":
    main()
