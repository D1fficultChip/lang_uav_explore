#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List


FIELDS: List[str] = [
    "record_index",
    "video_time_sec",
    "video_frame_idx",
    "stage_id",
    "entity_id",
    "prompt",
    "found",
    "proposal_status",
    "score",
    "class_name",
    "bbox_x1",
    "bbox_y1",
    "bbox_x2",
    "bbox_y2",
    "image_region",
    "center_x_norm",
    "center_y_norm",
    "box_area_ratio",
    "topk_count",
    "text_conf",
    "mask_score",
    "mask_quality",
    "req_id",
    "cue_count",
]


def _float(v: Any) -> Any:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _bbox_cols(bbox: Any) -> Dict[str, Any]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return {
            "bbox_x1": None,
            "bbox_y1": None,
            "bbox_x2": None,
            "bbox_y2": None,
        }
    return {
        "bbox_x1": _float(bbox[0]),
        "bbox_y1": _float(bbox[1]),
        "bbox_x2": _float(bbox[2]),
        "bbox_y2": _float(bbox[3]),
    }


def compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    infer = row.get("infer") or {}
    frame_meta = row.get("frame_meta") or {}
    request = row.get("request") or {}
    spatial = infer.get("spatial_hint") or {}
    best = infer.get("best_candidate") or {}
    cues = request.get("cues") or []
    primary = request.get("primary") or {}
    prompt = infer.get("prompt") or primary.get("prompt")

    out = {
        "record_index": row.get("record_index"),
        "video_time_sec": frame_meta.get("video_time_sec", frame_meta.get("stamp")),
        "video_frame_idx": frame_meta.get("video_frame_idx", frame_meta.get("seq")),
        "stage_id": infer.get("stage_id"),
        "entity_id": infer.get("entity_id"),
        "prompt": prompt,
        "found": bool(infer.get("found", False)),
        "proposal_status": infer.get("proposal_status"),
        "score": _float(infer.get("score")),
        "class_name": infer.get("class_name"),
        "image_region": spatial.get("image_region"),
        "center_x_norm": None,
        "center_y_norm": None,
        "box_area_ratio": _float(spatial.get("box_area_ratio")),
        "topk_count": len(infer.get("topk_candidates") or []),
        "text_conf": _float(best.get("text_conf")),
        "mask_score": _float(best.get("mask_score")),
        "mask_quality": _float(infer.get("mask_quality", best.get("mask_quality"))),
        "req_id": infer.get("req_id"),
        "cue_count": len(cues) if isinstance(cues, list) else 0,
    }

    center_xy = spatial.get("center_xy_norm")
    if isinstance(center_xy, list) and len(center_xy) >= 2:
        out["center_x_norm"] = _float(center_xy[0])
        out["center_y_norm"] = _float(center_xy[1])

    out.update(_bbox_cols(infer.get("bbox")))
    return out


def default_jsonl_path(input_path: Path) -> Path:
    stem = input_path.name
    if stem.endswith(".jsonl"):
        stem = stem[:-6]
    return input_path.with_name(f"{stem}.compact.jsonl")


def default_csv_path(input_path: Path) -> Path:
    stem = input_path.name
    if stem.endswith(".jsonl"):
        stem = stem[:-6]
    return input_path.with_name(f"{stem}.compact.csv")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export a compact paper-friendly inference trace.")
    ap.add_argument("--input", required=True, help="Path to filtered inference_trace jsonl")
    ap.add_argument("--jsonl-out", help="Compact jsonl output path")
    ap.add_argument("--csv-out", help="Compact csv output path")
    args = ap.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    jsonl_out = Path(args.jsonl_out).expanduser().resolve() if args.jsonl_out else default_jsonl_path(input_path)
    csv_out = Path(args.csv_out).expanduser().resolve() if args.csv_out else default_csv_path(input_path)

    rows: List[Dict[str, Any]] = []
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(compact_row(json.loads(line)))

    with jsonl_out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with csv_out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(
        {
            "input": str(input_path),
            "jsonl_out": str(jsonl_out),
            "csv_out": str(csv_out),
            "rows": len(rows),
            "fields": FIELDS,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
