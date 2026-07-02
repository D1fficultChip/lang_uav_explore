#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def has_request_cues(row: Dict[str, Any]) -> bool:
    req = row.get("request") or {}
    cues = req.get("cues")
    return isinstance(cues, list) and len(cues) > 0


def is_empty_spatial_hint(infer: Dict[str, Any]) -> bool:
    spatial_hint = infer.get("spatial_hint")
    return not spatial_hint


def is_empty_topk(infer: Dict[str, Any]) -> bool:
    topk = infer.get("topk_candidates")
    return not isinstance(topk, list) or len(topk) == 0


def cue_support_score(infer: Dict[str, Any]) -> float:
    val = infer.get("cue_support_score")
    if val is None:
        return 0.0
    try:
        return float(val)
    except Exception:
        return 0.0


def is_low_value_no_candidate(row: Dict[str, Any]) -> bool:
    infer = row.get("infer") or {}
    if infer.get("proposal_status") != "no_candidate":
        return False
    if infer.get("found", False):
        return False
    try:
        score = float(infer.get("score", 0.0))
    except Exception:
        score = 0.0
    if score != 0.0:
        return False
    if not is_empty_topk(infer):
        return False
    if cue_support_score(infer) != 0.0:
        return False
    if has_request_cues(row):
        return False
    if not is_empty_spatial_hint(infer):
        return False
    return True


def is_valid_detection(row: Dict[str, Any]) -> bool:
    infer = row.get("infer") or {}
    if infer.get("found", False):
        return True
    if infer.get("proposal_status") and infer.get("proposal_status") != "no_candidate":
        return True
    if not is_empty_topk(infer):
        return True
    try:
        return float(infer.get("score", 0.0)) > 0.0
    except Exception:
        return False


def default_output_path(input_path: Path, keep_ratio: int) -> Path:
    stem = input_path.name
    if stem.endswith(".jsonl"):
        stem = stem[:-6]
    return input_path.with_name(f"{stem}.filtered_keep1of{keep_ratio}.jsonl")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Keep all effective detections, but only sample 1/N rows from low-value no-candidate inference results."
    )
    ap.add_argument("--input", required=True, help="Path to inference_trace.jsonl")
    ap.add_argument("--output", help="Output filtered jsonl path")
    ap.add_argument("--keep-ratio", type=int, default=20, help="Keep 1 row out of N for low-value no-candidate results")
    args = ap.parse_args()

    if args.keep_ratio <= 0:
        raise ValueError("--keep-ratio must be > 0")

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else default_output_path(input_path, args.keep_ratio)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    valid_kept = 0
    low_value_total = 0
    low_value_kept = 0
    low_value_seen = 0

    with input_path.open("r", encoding="utf-8") as fin, output_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            row = json.loads(line)

            keep = True
            if is_low_value_no_candidate(row):
                low_value_total += 1
                low_value_seen += 1
                keep = ((low_value_seen - 1) % args.keep_ratio) == 0
                if keep:
                    low_value_kept += 1
            elif is_valid_detection(row):
                valid_kept += 1

            if keep:
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                kept += 1

    print(json.dumps(
        {
            "input": str(input_path),
            "output": str(output_path),
            "total_rows": total,
            "kept_rows": kept,
            "valid_detection_kept": valid_kept,
            "low_value_no_candidate_total": low_value_total,
            "low_value_no_candidate_kept": low_value_kept,
            "keep_ratio": args.keep_ratio,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
